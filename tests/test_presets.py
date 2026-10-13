"""Real JSON file tests for catalog/editing; no slicer or printer is launched."""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest


NATIVE_COMMIT = "9bf2f18eaff7cc0655cbdeef6a215660085ecb0f"
REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "src/snapmaker_slicer_mcp/data/settings.json"


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def catalog_files(tmp_path):
    from snapmaker_slicer_mcp.engine import Engine

    package = tmp_path / "engine"
    binary = package / ("Contents/MacOS/native" if sys.platform == "darwin" else
                        "native" if os.name == "nt" else "bin/native")
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")  # Bound for provenance; never executed.
    binary.chmod(0o755)
    resources = package / ("Contents/Resources" if sys.platform == "darwin" else "resources")
    profiles = resources / "profiles" / "Snapmaker"
    root = put(profiles / "filament/root.json", {
        "name": "PLA base", "type": "filament", "from": "system", "instantiation": "false",
        "nozzle_temperature": ["210"], "cool_plate_temp": ["50"],
        "cool_plate_temp_initial_layer": ["50"], "filament_type": ["PLA"],
        "filament_start_gcode": [""], "additional_cooling_fan_speed": ["20"],
    })
    put(profiles / "filament/generic.json", {
        "name": "Generic PLA", "type": "filament", "from": "system",
        "inherits": "PLA base", "nozzle_temperature": ["215"],
    })
    put(profiles / "process/default.json", {
        "name": "Standard U1", "type": "process", "from": "system", "layer_height": "0.2",
        "inner_wall_speed": ["300", "600"], "process_flow_support": ["standard", "high_flow"],
    })
    put(profiles / "machine/u1.json", {
        "name": "U1 fixture", "type": "machine", "from": "system",
        "printer_model": "Snapmaker U1", "printer_technology": "FFF", "nozzle_diameter": ["0.4"],
        "machine_start_gcode": "G28", "printer_variant": "0.4",
    })
    user = tmp_path / "forja/presets"
    sot = put(user / "filament/_GENERIC_PLA.json", {
        "name": "_GENERIC_PLA", "from": "User", "version": "2.2.0.4",
        "inherits": "Generic PLA", "filament_settings_id": ["_GENERIC_PLA"],
        "cool_plate_temp": ["50"], "additional_cooling_fan_speed": ["0"],
    })
    engine = Engine(Engine.bind(binary, NATIVE_COMMIT, resources))
    return engine, user, tmp_path / "artifacts/preset-drafts", sot, root


@pytest.fixture
def catalog(catalog_files):
    from snapmaker_slicer_mcp.presets import Catalog
    engine, user, drafts, _, _ = catalog_files
    return Catalog(engine, [user], drafts)


def test_resolve_forja_shape_without_type_and_report_full_hash_chain(catalog, catalog_files):
    engine, _, drafts, sot, _ = catalog_files
    original = sot.read_bytes()
    value = catalog.get_preset_config("filament", "_GENERIC_PLA")
    assert value["config"]["nozzle_temperature"] == ["215"]
    assert value["config"]["cool_plate_temp"] == ["50"]
    assert value["config"]["additional_cooling_fan_speed"] == ["0"]
    assert [p["name"] for p in value["inheritance"]] == ["PLA base", "Generic PLA", "_GENERIC_PLA"]
    assert value["sha256"] == hashlib.sha256(original).hexdigest()
    assert value["engine"] == engine.identity()
    assert value["source"] == "user" and value["read_only"] is True
    assert sot.read_bytes() == original and not drafts.exists()


def test_list_hides_system_by_default_and_accepts_native_type_alias(catalog):
    public = catalog.list_presets("filament")
    assert [p["name"] for p in public["presets"]] == ["_GENERIC_PLA"]
    system = catalog.list_presets("process", include_system=True)
    assert system["presets"][0]["type"] == "print"
    assert system["presets"][0]["name"] == "Standard U1"


def test_missing_parent_and_cycles_fail_instead_of_using_defaults(catalog, catalog_files):
    _, user, _, _, _ = catalog_files
    put(user / "filament/missing.json", {"name": "Missing", "inherits": "No such parent"})
    with pytest.raises(ValueError, match="parent|inherit"):
        catalog.get_preset_config("filament", "Missing")
    put(user / "filament/a.json", {"name": "A", "inherits": "B"})
    put(user / "filament/b.json", {"name": "B", "inherits": "A"})
    with pytest.raises(ValueError, match="cycle"):
        catalog.get_preset_config("filament", "A")


def test_ambiguous_names_fail_with_sources(catalog, catalog_files):
    _, user, _, _, _ = catalog_files
    put(user / "filament/copy.json", {"name": "_GENERIC_PLA", "cool_plate_temp": ["65"]})
    with pytest.raises(ValueError, match="[Aa]mbiguous"):
        catalog.get_preset_config("filament", "_GENERIC_PLA")


def test_external_preset_is_never_overwritten_or_deleted(catalog, catalog_files):
    _, _, drafts, sot, _ = catalog_files
    original = sot.read_bytes()
    for operation in (
        lambda: catalog.save_preset("filament", "_GENERIC_PLA", {"cool_plate_temp": ["65"]}),
        lambda: catalog.edit_preset("filament", "_GENERIC_PLA", {"cool_plate_temp": ["65"]}),
        lambda: catalog.rename_preset("filament", "_GENERIC_PLA", "Changed"),
        lambda: catalog.delete_preset("filament", "_GENERIC_PLA"),
    ):
        with pytest.raises(ValueError, match="read.only|another name|draft"):
            operation()
    assert sot.read_bytes() == original and not drafts.exists()


def test_save_edit_rename_delete_only_drafts_and_keep_sources(catalog, catalog_files):
    _, _, drafts, sot, _ = catalog_files
    original = sot.read_bytes()
    saved = catalog.save_preset("filament", "PLA draft", {
        "inherits": "_GENERIC_PLA", "cool_plate_temp": [55],
    })
    path = Path(saved["path"])
    assert path.is_relative_to(drafts)
    stored = json.loads(path.read_text())
    assert stored["inherits"] == "_GENERIC_PLA"
    assert stored["filament_settings_id"] == ["PLA draft"]
    assert stored["cool_plate_temp"] == ["55"]
    assert "nozzle_temperature" not in stored  # Save a delta, not accidental global defaults.
    edited = catalog.edit_preset("filament", "PLA draft", {"nozzle_temperature": [220]})
    assert edited["config"]["nozzle_temperature"] == ["220"]
    assert edited["sha256"] != saved["sha256"]
    renamed = catalog.rename_preset("filament", "PLA draft", "PLA revised")
    assert renamed["config"]["filament_settings_id"] == ["PLA revised"]
    assert not path.exists() and Path(renamed["path"]).is_file()
    deleted = catalog.delete_preset("filament", "PLA revised")
    assert deleted["deleted"] is True and not Path(renamed["path"]).exists()
    assert sot.read_bytes() == original


def test_detach_flattens_resolved_values_without_future_parent_changes(catalog, catalog_files):
    _, user, _, _, _ = catalog_files
    detached = catalog.save_preset("filament", "Independent PLA", {"inherits": "_GENERIC_PLA"}, detach=True)
    assert detached["inherits"] == ""
    assert detached["config"]["nozzle_temperature"] == ["215"]
    put(user / "filament/_GENERIC_PLA.json", {
        "name": "_GENERIC_PLA", "inherits": "Generic PLA", "nozzle_temperature": ["240"],
    })
    assert catalog.get_preset_config("filament", "Independent PLA")["config"]["nozzle_temperature"] == ["215"]


def test_independent_root_draft_is_supported(catalog):
    saved = catalog.save_preset("print", "Independent process", {"layer_height": 0.16})
    assert saved["config"]["layer_height"] == "0.16" and saved["inherits"] == ""
    assert saved["config"]["print_settings_id"] == "Independent process"


def test_referenced_draft_cannot_be_removed_or_renamed_silently(catalog):
    catalog.save_preset("print", "Parent", {"layer_height": 0.2})
    catalog.save_preset("print", "Child", {"inherits": "Parent", "layer_height": 0.16})
    for action in (lambda: catalog.delete_preset("print", "Parent"),
                   lambda: catalog.rename_preset("print", "Parent", "Other")):
        with pytest.raises(ValueError, match="referenc|depend"):
            action()
    assert catalog.get_preset_config("print", "Child")["config"]["layer_height"] == "0.16"


def test_sources_refresh_and_hashes_match_the_bytes_actually_read(catalog, catalog_files):
    _, _, _, sot, _ = catalog_files
    before = catalog.get_preset_config("filament", "_GENERIC_PLA")
    data = json.loads(sot.read_text())
    data["cool_plate_temp"] = ["55"]
    put(sot, data)
    after = catalog.get_preset_config("filament", "_GENERIC_PLA")
    assert before["sha256"] != after["sha256"]
    assert after["sha256"] == hashlib.sha256(sot.read_bytes()).hexdigest()
    assert after["config"]["cool_plate_temp"] == ["55"]


def test_drafts_cannot_overlap_read_only_sources(catalog_files):
    from snapmaker_slicer_mcp.presets import Catalog
    engine, user, _, _, _ = catalog_files
    for drafts in (user, user / "drafts", engine.resources / "drafts"):
        with pytest.raises(ValueError, match="overlap|read.only|resources"):
            Catalog(engine, [user], drafts)


def test_symlinked_draft_is_never_modified(catalog, catalog_files):
    _, _, _, sot, _ = catalog_files
    saved = catalog.save_preset("filament", "Safe draft", {"inherits": "_GENERIC_PLA"})
    path = Path(saved["path"])
    path.unlink()
    path.symlink_to(sot)
    original = sot.read_bytes()
    with pytest.raises(ValueError, match="symlink|read.only|draft"):
        catalog.edit_preset("filament", "_GENERIC_PLA", {"cool_plate_temp": [65]})
    assert sot.read_bytes() == original


def test_secrets_and_url_credentials_are_redacted_in_arrays_and_raw_config(catalog, catalog_files):
    _, user, _, _, _ = catalog_files
    put(user / "machine/private.json", {
        "name": "Private machine", "type": "machine", "printhost_apikey": "TEST-SECRET",
        "print_host": ["https://test:TEST-PASS@localhost/"], "nozzle_diameter": ["0.4"],
    })
    value = catalog.get_preset_config("printer", "Private machine")
    encoded = json.dumps(value)
    assert "TEST-SECRET" not in encoded and "TEST-PASS" not in encoded
    assert "<redacted>" in encoded and "localhost" in encoded


def test_schema_is_native_versioned_and_excludes_cli_actions(catalog):
    doc = json.loads(SCHEMA.read_text())
    assert doc["_meta"]["source_commit"] == NATIVE_COMMIT
    assert doc["_meta"]["source_sha256"] == "da7478708cde8ee6a86a703c4b16717e91558e995d0c6225b2d577ede77616db"
    assert catalog.describe_setting("nozzle_temperature")["type"] == "coInts"
    assert catalog.describe_setting("curr_bed_type")["enum_values"][0] == "Cool Plate"
    assert catalog.describe_setting("filament_retraction_length")["nullable"] is True
    assert catalog.describe_setting("slice") is None
    assert catalog.search_settings("nozzle temperature", limit=2)["settings"]
    assert len(catalog.search_settings("nozzle temperature", limit=2)["settings"]) == 2


def test_schema_extractor_honors_block_comments_brace_defaults_and_nullable_loop():
    from snapmaker_slicer_mcp.schema_extract import parse_print_config
    source = '''
    /* def = this->add("commented_key", coFloat); */
    def = this->add("retraction_length", coFloats);
    def->label = L("Retraction length");
    def->tooltip = L("Retraction; length. https://example.test");
    def->min = 0;
    def->set_default_value(new ConfigOptionFloats{ 0.8 });
    for (const char *opt_key : { "retraction_length" }) {
        auto it_opt = options.find(opt_key);
        def = this->add_nullable(std::string("filament_") + opt_key, it_opt->second.type);
    }
    '''
    settings, _ = parse_print_config(source)
    assert "commented_key" not in settings
    assert settings["retraction_length"]["default"] == "0.8"
    assert settings["retraction_length"]["tooltip"] == "Retraction; length. https://example.test"
    assert settings["filament_retraction_length"]["nullable"] is True


def test_validate_normalizes_without_broadcast_or_slot_reordering():
    from snapmaker_slicer_mcp.presets import validate_changes
    current = {"nozzle_temperature": ["210", "220", "230", "240"], "layer_height": "0.2"}
    assert validate_changes({"nozzle_temperature": [211, 221, 231, 241], "layer_height": 0.16}, current) == {
        "nozzle_temperature": ["211", "221", "231", "241"], "layer_height": "0.16",
    }
    with pytest.raises(ValueError, match="length|slots|cardinality"):
        validate_changes({"nozzle_temperature": [200]}, current)
    with pytest.raises(ValueError, match="array|vector|slots"):
        validate_changes({"nozzle_temperature": 200}, current)


def test_native_float_or_percent_vectors_are_editable():
    from snapmaker_slicer_mcp.presets import validate_changes
    assert validate_changes({'small_perimeter_speed': ['50%', 70],
                             'internal_bridge_speed': ['150%', '80%']}, {}) == {
        'small_perimeter_speed': ['50%', '70'], 'internal_bridge_speed': ['150%', '80%']}


@pytest.mark.parametrize("changes", [
    {"not_a_setting": 1}, {"layer_height": float("nan")}, {"layer_height": float("inf")},
    {"layer_height": "nan"}, {"layer_height": -0.2}, {"layer_height": True},
    {"curr_bed_type": "Smooth Cool Plate"}, {"enable_support": "perhaps"},
    {"wall_loops": 2.5}, {"sparse_infill_density": "101%"},
    {"nozzle_temperature": ["hot"]}, {"nozzle_temperature": [float("nan")]},
    {"layer_height": {"value": 0.2}}, {"layer_height": None},
    {"outer_wall_speed": ["<redacted>", "500"]},
])
def test_invalid_settings_are_rejected_before_writing(changes):
    from snapmaker_slicer_mcp.presets import validate_changes
    with pytest.raises(ValueError):
        validate_changes(changes, {})


@pytest.mark.parametrize("key,value", [
    ("post_process", ["echo unsafe"]), ("post_process", []),
    ("print_host", "localhost"), ("printhost_apikey", "TEST-SECRET"),
    ("printer_model", "Other printer"),
])
def test_transport_and_post_process_never_write_even_with_gcode_opt_in(key, value):
    from snapmaker_slicer_mcp.presets import validate_changes
    with pytest.raises(ValueError):
        validate_changes({key: value}, {}, allow_custom_gcode=True)


def test_custom_gcode_requires_explicit_argument_without_env_bypass(monkeypatch):
    from snapmaker_slicer_mcp.presets import validate_changes
    monkeypatch.setenv("ORCA_MCP_ALLOW_KEYS", "*")
    with pytest.raises(ValueError):
        validate_changes({"machine_start_gcode": "G28\nM104 S200"}, {})
    assert validate_changes({"machine_start_gcode": "G28\nM104 S200"}, {}, True) == {
        "machine_start_gcode": "G28\nM104 S200",
    }


def test_nullable_values_and_flow_variants_stay_native_json_shape():
    from snapmaker_slicer_mcp.presets import validate_changes
    result = validate_changes({"filament_retraction_length": ["nil"],
                               "inner_wall_speed": [200, 500], "curr_bed_type": "Cool Plate"}, {})
    assert result["filament_retraction_length"] == ["nil"]
    assert result["inner_wall_speed"] == ["200", "500"]
    assert result["curr_bed_type"] == "Cool Plate"


def test_failed_edit_batch_leaves_draft_hash_unchanged(catalog):
    draft = catalog.save_preset("print", "Quality", {"layer_height": 0.16})
    original = Path(draft["path"]).read_bytes()
    with pytest.raises(ValueError):
        catalog.edit_preset("print", "Quality", {"layer_height": 0.2, "wall_loops": "bad"})
    assert Path(draft["path"]).read_bytes() == original


def test_catalog_rejects_changed_engine_assets(catalog, catalog_files):
    engine, _, _, _, _ = catalog_files
    engine.executable.write_text("changed")
    with pytest.raises(ValueError, match="Engine assets changed"):
        catalog.list_presets()


def test_catalog_reports_settings_schema_mismatch_for_a_different_native_commit(catalog_files):
    from snapmaker_slicer_mcp.engine import Engine
    from snapmaker_slicer_mcp.presets import Catalog
    engine, user, drafts, _, _ = catalog_files
    changed = Engine(Engine.bind(engine.executable, "1" * 40, engine.resources))
    result = Catalog(changed, [user], drafts).list_presets()
    assert result["schema"]["matches_engine"] is False
    assert result["schema"]["source_commit"] == NATIVE_COMMIT
    assert result["schema"]["engine_commit"] == "1" * 40


def test_load_schema_exposes_per_object_extruder_type():
    from snapmaker_slicer_mcp.presets import load_schema, validate_changes
    assert load_schema()["extruder"]["type"] == "coInt"
    assert validate_changes({"extruder": 2}, {"extruder": "1"}) == {"extruder": "2"}


def test_save_filters_combined_config_to_the_requested_preset_group(catalog):
    saved = catalog.save_preset("print", "Process only", {
        "layer_height": 0.16, "nozzle_temperature": [220], "filament_settings_id": ["Other"],
    })
    assert saved["config"]["layer_height"] == "0.16"
    assert "nozzle_temperature" not in saved["config"]
    assert "filament_settings_id" not in saved["config"]
    assert "nozzle_temperature" in saved["ignored_keys"]
    assert "layer_height" in catalog.preset_keys("print")
    assert "nozzle_temperature" not in catalog.preset_keys("print")
    with pytest.raises(ValueError, match="group|type"):
        catalog.edit_preset("print", "Process only", {"nozzle_temperature": [210]})


def test_machine_model_descriptors_are_not_printer_presets(catalog, catalog_files):
    _, user, _, _, _ = catalog_files
    put(user / "machine/u1.json", {"name": "U1 descriptor", "type": "machine_model", "model_id": "U1"})
    assert "U1 descriptor" not in {item["name"] for item in catalog.list_presets(include_system=True)["presets"]}


def test_native_dynamic_machine_limits_and_schema_registry_are_included():
    from snapmaker_slicer_mcp.presets import load_schema
    schema = load_schema()
    assert schema["machine_max_speed_x"]["type"] == "coFloats"
    assert schema["machine_max_speed_x"]["min"] == 0
    assert schema["project_schema_version"]["type"] == "coInt"
    assert "bed_temperature" not in schema  # A legacy key/temperature placeholder, not a setting.


def test_flow_mode_enums_use_native_constants():
    from snapmaker_slicer_mcp.presets import validate_changes
    assert validate_changes({"filament_volume_type": ["high_flow"]}, {}) == {"filament_volume_type": ["high_flow"]}
    with pytest.raises(ValueError):
        validate_changes({"filament_volume_type": ["mystery"]}, {})


def test_resolve_actual_bundled_snapmaker_chain_with_user_override(catalog_files):
    from snapmaker_slicer_mcp.engine import Engine
    from snapmaker_slicer_mcp.presets import Catalog
    engine, user, drafts, sot, _ = catalog_files
    # Copy shipped JSON into a temporary package before rebinding it. This is a
    # real catalog test, not execution or equivalence proof of the native engine.
    destination = engine.resources / "profiles/Snapmaker"
    shutil.rmtree(destination)
    shutil.copytree(REPO / "tests/fixtures/snapmaker-profiles", destination)
    engine = Engine(Engine.bind(engine.executable, NATIVE_COMMIT, engine.resources))
    original = sot.read_bytes()
    value = Catalog(engine, [user], drafts).get_preset_config("filament", "_GENERIC_PLA")
    assert value["config"]["nozzle_temperature"] == ["220"]
    assert value["config"]["cool_plate_temp"] == ["50"]
    assert value["config"]["additional_cooling_fan_speed"] == ["0"]
    assert len(value["inheritance"]) > 3 and sot.read_bytes() == original


def test_detached_machine_preserves_parent_identity_and_scripts_with_explicit_opt_in(catalog):
    saved = catalog.save_preset("printer", "U1 local draft", {"inherits": "U1 fixture"},
                                detach=True, allow_custom_gcode=True)
    assert saved["config"]["printer_model"] == "Snapmaker U1"
    assert saved["config"]["printer_technology"] == "FFF"
    assert saved["config"]["machine_start_gcode"] == "G28"
    assert saved["inherits"] == ""
    with pytest.raises(ValueError):
        catalog.save_preset("printer", "Wrong target", {"inherits": "U1 fixture", "printer_model": "Other"})


def test_explicit_values_survive_changing_a_draft_parent(catalog, catalog_files):
    _, user, _, _, _ = catalog_files
    put(user / "process/other.json", {"name": "Other process", "type": "process", "layer_height": "0.3"})
    catalog.save_preset("print", "Child draft", {"inherits": "Standard U1"})
    result = catalog.save_preset("print", "Child draft", {"inherits": "Other process", "layer_height": "0.2"})
    assert result["config"]["layer_height"] == "0.2"


@pytest.mark.parametrize("changes", [{"layer_height": "1e1000"}, {"extruder": 2 ** 31}])
def test_numbers_must_fit_native_float_and_int_types(changes):
    from snapmaker_slicer_mcp.presets import validate_changes
    with pytest.raises(ValueError):
        validate_changes(changes, {})


def test_native_schema_marks_only_source_declared_filament_flow_variants():
    from snapmaker_slicer_mcp.presets import load_schema
    schema = load_schema()
    assert schema['nozzle_temperature']['flow_variant_domain'] == 'filament'
    assert schema['additional_cooling_fan_speed']['flow_variant_domain'] == 'filament'
    assert schema['filament_retraction_length']['flow_variant_domain'] == 'filament'
    assert 'flow_variant_domain' not in schema['cool_plate_temp']
    assert 'flow_variant_domain' not in schema['filament_type']
    doc = json.loads(SCHEMA.read_text())
    assert len(doc['_meta']['flow_variant_keys']['filament']) == 19


def test_draft_expands_native_parent_padding_without_relaxing_slot_cardinality(catalog, catalog_files):
    _, user, _, _, _ = catalog_files
    parent = put(user / 'filament/padded.json', {
        'name': 'Padded dual-flow', 'type': 'filament', 'inherits': '',
        'filament_flow_support': ['standard', 'high_flow'], 'nozzle_temperature': ['240'],
        'cool_plate_temp': ['65'],
    })
    original = parent.read_bytes()
    draft = catalog.save_preset('filament', 'Padded draft', {
        'inherits': 'Padded dual-flow', 'nozzle_temperature': ['240', '255'],
    })
    assert draft['config']['nozzle_temperature'] == ['240', '255']
    assert draft['config']['cool_plate_temp'] == ['65']
    updated = catalog.edit_preset('filament', 'Padded draft', {'nozzle_temperature': ['240', '256']})
    assert updated['config']['nozzle_temperature'] == ['240', '256']
    with pytest.raises(ValueError, match='length|cardinality|slots'):
        catalog.edit_preset('filament', 'Padded draft', {'cool_plate_temp': ['65', '70']})
    assert parent.read_bytes() == original
