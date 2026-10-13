"""Project edits exercise real ZIP/XML payloads; no slicer or GUI is started."""
import hashlib
import importlib
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest


CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
RANGES = "Metadata/layer_config_ranges.xml"
PROFILE = "Metadata/layer_heights_profile.txt"
BRIM = "Metadata/brim_ear_points.txt"
CUT = "Metadata/cut_information.xml"


def project(path):
    # An import inside the test yields a failed test, rather than a collection
    # error, while the new editor is still absent during the first RED run.
    return importlib.import_module("snapmaker_slicer_mcp.project").Project(path)


def payloads(path):
    with zipfile.ZipFile(path) as archive:
        return {n: archive.read(n) for n in archive.namelist()}


def build_items(path):
    root = ET.fromstring(payloads(path)["3D/3dmodel.model"])
    return root.findall(f"{{{CORE}}}build/{{{CORE}}}item")


@pytest.fixture
def source(tmp_path):
    root = f'''<model xmlns="{CORE}" xmlns:p="{PROD}"
        xmlns:q="urn:required-but-unused" unit="millimeter" requiredextensions="p q">
      <resources>
        <object id="20" p:UUID="root-20" type="model"><components>
          <component objectid="3" p:path="/3D/Objects/painted.model" p:UUID="component-3"/>
          <component objectid="7" p:path="/3D/Objects/negative.model" p:UUID="component-7"/>
        </components></object>
        <object id="40" p:UUID="root-40" type="model"><components>
          <component objectid="3" p:path="/3D/Objects/painted.model" p:UUID="component-40"/>
        </components></object>
      </resources>
      <build p:UUID="build-id">
        <item objectid="20" p:UUID="instance-20-0" transform="1 0 0 0 1 0 0 0 1 10 20 0" printable="1"/>
        <item objectid="20" p:UUID="instance-20-1" transform="1 0 0 0 1 0 0 0 1 334 20 0" printable="1"/>
        <item objectid="40" p:UUID="instance-40-0" transform="1 0 0 0 1 0 0 0 1 15 -299 0" printable="1"/>
      </build>
    </model>'''
    settings = '''<config>
      <object id="20"><metadata key="name" value="Painted assembly"/><metadata key="extruder" value="1"/>
        <metadata key="wall_loops" value="2"/>
        <part id="3" subtype="normal_part"><metadata key="name" value="painted"/>
          <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>
          <metadata key="source_object_id" value="9"/><metadata key="source_volume_id" value="6"/>
          <metadata key="source_file" value="original.3mf"/><mesh_stat edges_fixed="0"/>
        </part>
        <part id="7" subtype="negative_part"><metadata key="name" value="cavity"/><metadata key="extruder" value="2"/></part>
      </object>
      <object id="40"><metadata key="name" value="Shared mesh"/><metadata key="extruder" value="2"/>
        <part id="3" subtype="normal_part"><metadata key="source_volume_id" value="6"/></part>
      </object>
      <plate><metadata key="plater_id" value="1"/><metadata key="plater_name" value="First"/>
        <metadata key="locked" value="false"/><metadata key="user_note" value="keep"/>
        <metadata key="gcode_file" value="Metadata/plate_1.gcode"/>
        <metadata key="thumbnail_file" value="Metadata/plate_1.png"/>
        <metadata key="thumbnail_no_light_file" value="Metadata/plate_no_light_1.png"/>
        <metadata key="top_file" value="Metadata/top_1.png"/><metadata key="pick_file" value="Metadata/pick_1.png"/>
        <model_instance><metadata key="object_id" value="20"/><metadata key="instance_id" value="0"/><metadata key="identify_id" value="101"/></model_instance>
      </plate>
      <plate><metadata key="plater_id" value="2"/><metadata key="plater_name" value="Second"/>
        <model_instance><metadata key="object_id" value="20"/><metadata key="instance_id" value="1"/><metadata key="identify_id" value="102"/></model_instance>
      </plate>
      <plate><metadata key="plater_id" value="3"/><metadata key="plater_name" value="Third"/>
        <model_instance><metadata key="object_id" value="40"/><metadata key="instance_id" value="0"/><metadata key="identify_id" value="103"/></model_instance>
      </plate>
      <assemble>
        <assemble_item object_id="20" instance_id="0" transform="1 0 0 0 1 0 0 0 1 10 20 0" offset="0 0 0"/>
        <assemble_item object_id="20" instance_id="1" transform="1 0 0 0 1 0 0 0 1 334 20 0" offset="0 0 0"/>
        <assemble_item object_id="40" instance_id="0" transform="1 0 0 0 1 0 0 0 1 15 -299 0" offset="0 0 0"/>
      </assemble>
    </config>'''
    mesh = f'''<model xmlns="{CORE}" xmlns:p="{PROD}" unit="millimeter"><resources>
      <object id="3" p:UUID="mesh-3" type="model"><mesh><vertices>
        <vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="1"/>
      </vertices><triangles><triangle v1="0" v2="1" v3="2" paint_color="1" paint_supports="2" paint_seam="3"/></triangles></mesh></object>
    </resources><build/></model>'''
    values = {
        "layer_height": "0.2", "wall_loops": "2", "printer_model": "Snapmaker U1",
        "printer_settings_id": "Snapmaker U1 (0.4 nozzle)", "printer_variant": "0.4",
        "printable_area": ["0x0", "270x0", "270x270", "0x270"],
        "filament_colour": ["#111111", "#EEEEEE"], "filament_type": ["PETG", "PLA"],
        "filament_settings_id": ["_GENERIC_PETG", "_GENERIC_PLA"],
        "additional_cooling_fan_speed": ["0", "0"], "flush_volumes_matrix": ["0", "100", "200", "0"],
        "mixed_filament_definitions": "1,2,1,0,50,0,g,w,m2,z0,xa0,xb0,d0,o1,u1",
    }
    files = {
        "3D/3dmodel.model": root.encode(),
        "3D/Objects/painted.model": mesh.encode(),
        "3D/Objects/negative.model": mesh.replace('id="3"', 'id="7"').encode(),
        "3D/_rels/3dmodel.model.rels": b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="mesh" Target="/3D/Objects/painted.model" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>',
        "Metadata/project_settings.config": json.dumps(values).encode(),
        "Metadata/model_settings.config": settings.encode(),
        RANGES: b'<objects><object id="1"><range min_z="1" max_z="2"><option opt_key="layer_height">0.16</option><option opt_key="wall_loops">4</option></range></object><object id="2"><range min_z="3" max_z="4"><option opt_key="layer_height">0.12</option></range></object></objects>',
        PROFILE: b'object_id=1|0;0.2;1;0.2;2;0.16\nobject_id=2|0;0.2;1;0.2;4;0.12\n',
        BRIM: b'brim_points_format_version=0\nobject_id=1|1 2 3 4\nobject_id=2|10 20 30 40\n',
        CUT: b'<objects><object id="1"><cut_id id="123" check_sum="456" connectors_cnt="1"/><connectors><connector volume_id="0" type="1" r_tolerance="0.2" h_tolerance="0.1"/></connectors></object><object id="2"><cut_id id="789" check_sum="987" connectors_cnt="0"/></object></objects>',
        "Metadata/custom_gcode_per_layer.xml": b'<custom_gcodes_per_layer><plate><plate_info id="1"/><layer top_z="2" type="0" extruder="1" color="" extra="M600"/></plate></custom_gcodes_per_layer>',
        "Metadata/plate_1.gcode": b'G1 X999 E999 ; old slice\n',
        "Metadata/plate_1.gcode.md5": b'old-md5',
        "Metadata/plate_1.png": b'old-preview', "Metadata/plate_1_small.png": b'old-small-preview',
        "Metadata/plate_no_light_1.png": b'old-no-light', "Metadata/top_1.png": b'old-top',
        "Metadata/pick_1.png": b'old-pick', "Metadata/plate_1.json": b'{"bbox_all":[1,2,3]}',
        "Metadata/slice_info.config": b'<config><plate><metadata key="prediction" value="9999"/></plate></config>',
        "Auxiliaries/original.png": b'original-reference-image', "Auxiliaries/notes.txt": b'keep exactly\x00\xff',
        "_rels/.rels": b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="model" Target="/3D/3dmodel.model" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/><Relationship Id="cover" Target="/Metadata/plate_1.png" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/></Relationships>',
        "[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/><Default Extension="png" ContentType="image/png"/><Default Extension="gcode" ContentType="text/x.gcode"/><Override PartName="/Metadata/plate_1.png" ContentType="image/png"/></Types>',
    }
    path = tmp_path / "source.3mf"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


def test_copy_invalidates_stale_slice_and_keeps_meshes_painting_ids_and_aux(source, tmp_path):
    before = source.read_bytes()
    original = payloads(source)
    p = project(source)
    p.set_config({"wall_loops": "4"})
    output = tmp_path / "edited.3mf"
    p.save(output)
    saved = payloads(output)
    assert source.read_bytes() == before
    for name in ("3D/3dmodel.model", "3D/Objects/painted.model", "3D/Objects/negative.model",
                 "3D/_rels/3dmodel.model.rels", "Auxiliaries/original.png", "Auxiliaries/notes.txt",
                 "Metadata/custom_gcode_per_layer.xml", RANGES, PROFILE, BRIM, CUT):
        assert saved[name] == original[name]
    assert json.loads(saved["Metadata/project_settings.config"])["wall_loops"] == "4"
    assert not any(n.endswith((".gcode", ".gcode.md5")) for n in saved)
    assert "Metadata/slice_info.config" not in saved
    assert not any(n in saved for n in ("Metadata/plate_1.png", "Metadata/plate_1_small.png", "Metadata/top_1.png", "Metadata/pick_1.png", "Metadata/plate_1.json", "Metadata/plate_no_light_1.png"))
    settings = ET.fromstring(saved["Metadata/model_settings.config"])
    assert not any(e.get("key") in {"gcode_file", "thumbnail_file", "thumbnail_no_light_file", "top_file", "pick_file"} for e in settings.iter())
    assert any(e.get("key") == "user_note" and e.get("value") == "keep" for e in settings.iter())
    assert "Metadata/plate_1.gcode" in p.data  # invalidation belongs to the output copy
    rels = ET.fromstring(saved["_rels/.rels"])
    assert [e.get("Id") for e in rels] == ["model"]
    types = ET.fromstring(saved["[Content_Types].xml"])
    assert not any(e.get("Extension") == "gcode" or e.get("PartName") == "/Metadata/plate_1.png" for e in types)
    assert any(e.get("Extension") == "png" for e in types)  # auxiliary PNG remains


def test_save_rejects_source_and_existing_destination(source, tmp_path):
    p = project(source)
    before = source.read_bytes()
    with pytest.raises(ValueError):
        p.save(source)
    alias = tmp_path / "alias.3mf"
    alias.symlink_to(source)
    with pytest.raises(ValueError):
        p.save(alias)
    output = tmp_path / "existing.3mf"
    output.write_bytes(b"do not overwrite")
    with pytest.raises(FileExistsError):
        p.save(output)
    assert source.read_bytes() == before and output.read_bytes() == b"do not overwrite"


def test_inspection_groups_root_ids_and_instance_indices_not_source_ids(source):
    p = project(source)
    objects = p.list_objects()
    assert [o["object_id"] for o in objects] == [20, 40]
    assert objects[0]["name"] == "Painted assembly"
    assert [i["instance_id"] for i in objects[0]["instances"]] == [0, 1]
    assert [i["plate"] for i in objects[0]["instances"]] == [1, 2]
    assert objects[0]["instances"][1]["transform"][-3:] == [334, 20, 0]
    assert [part["part_id"] for part in objects[0]["parts"]] == [3, 7]
    assert p.describe()["objects"] == objects
    assert [x["plate"] for x in p.describe()["plates"]] == [1, 2, 3]


def test_filtered_config_is_detached_and_material_slots_stay_in_order(source):
    p = project(source)
    value = p.get_config(["filament_colour", "missing"])
    value["filament_colour"][0] = "#000000"
    assert p.config["filament_colour"] == ["#111111", "#EEEEEE"]
    p.set_config({"filament_colour": ["#123456", "#ABCDEF"]})
    assert p.get_config()["filament_colour"] == ["#123456", "#ABCDEF"]
    assert p.config["filament_settings_id"] == ["_GENERIC_PETG", "_GENERIC_PLA"]
    assert p.config["additional_cooling_fan_speed"] == ["0", "0"]


@pytest.mark.parametrize("changes", [
    {"filament_colour": ["#000000"]},
    {"filament_type": ["PETG", "PLA", "PLA"]},
    {"flush_volumes_matrix": ["0", "0"]},
    {"printer_model": "Other printer"},
    {"printer_variant": "0.6"},
])
def test_set_config_rejects_implicit_slot_count_or_machine_changes(source, changes):
    p = project(source)
    before = p.get_config()
    with pytest.raises(ValueError):
        p.set_config(changes)
    assert p.config == before


def test_object_and_part_overrides_are_scoped_and_required_prefixes_survive(source, tmp_path):
    p = project(source)
    p.set_object_config(20, {"wall_loops": 5, "extruder": 2})
    p.set_object_config(20, {"extruder": 1}, part_id=7)
    p.set_object_config(20, {"wall_loops": None})
    output = tmp_path / "overrides.3mf"
    p.save(output)
    saved = project(output).list_objects()
    assert saved[0]["config"]["extruder"] == "2"
    assert "wall_loops" not in saved[0]["config"]
    assert saved[0]["parts"][1]["config"]["extruder"] == "1"
    assert saved[1]["config"]["extruder"] == "2"
    with pytest.raises(ValueError):
        p.set_object_config(999, {"wall_loops": 2})
    with pytest.raises(ValueError):
        p.set_object_config(20, {"extruder": 2}, part_id=999)


def test_compose_transform_applies_a_then_b_in_3mf_row_vector_convention():
    compose = importlib.import_module("snapmaker_slicer_mcp.project").compose_transform
    a = [0, 1, 0, -1, 0, 0, 0, 0, 1, 10, 20, 0]
    b = [1, 0, 0, 0, 1, 0, 0, 0, 1, 5, 6, 7]
    assert compose(a, b) == pytest.approx([0, 1, 0, -1, 0, 0, 0, 0, 1, 15, 26, 7])
    assert compose(a, [2, 0, 0, 0, 3, 0, 0, 0, 4, 0, 0, 0]) == pytest.approx([0, 3, 0, -2, 0, 0, 0, 0, 4, 20, 60, 0])


def test_instance_transform_keeps_pivot_and_other_instance_and_required_namespaces(source, tmp_path):
    p = project(source)
    p.transform_object(20, translate=[1, 2, 3], rotate=[0, 0, 90], scale=[2, 3, 4], instance_id=0)
    output = tmp_path / "transformed.3mf"
    p.save(output)
    items = build_items(output)
    assert list(map(float, items[0].get("transform").split())) == pytest.approx([0, 2, 0, -3, 0, 0, 0, 0, 4, 11, 22, 3])
    assert list(map(float, items[1].get("transform").split())) == IDENTITY[:9] + [334, 20, 0]
    assert [e.get(f"{{{PROD}}}UUID") for e in items] == ["instance-20-0", "instance-20-1", "instance-40-0"]
    xml = payloads(output)["3D/3dmodel.model"].decode()
    assert 'requiredextensions="p q"' in xml
    assert f'xmlns:p="{PROD}"' in xml and 'xmlns:q="urn:required-but-unused"' in xml
    assert f'xmlns="{CORE}"' in xml
    assert payloads(output)["3D/Objects/painted.model"] == payloads(source)["3D/Objects/painted.model"]


def test_translate_all_instances_preserves_each_local_offset(source):
    p = project(source)
    p.transform_object(20, translate=[-2, 3, 0])
    obj = p.list_objects()[0]
    assert [i["transform"][-3:] for i in obj["instances"]] == [[8, 23, 0], [332, 23, 0]]


@pytest.mark.parametrize("kwargs", [
    {"scale": [1, 0, 1]}, {"rotate": [0, float("nan"), 0]},
    {"translate": [1, 2]}, {"instance_id": 99},
])
def test_invalid_transform_rejects_without_changing_placements(source, kwargs):
    p = project(source)
    before = p.list_objects()
    with pytest.raises(ValueError):
        p.transform_object(20, **kwargs)
    assert p.list_objects() == before


def test_duplicate_shares_mesh_payloads_and_clones_instances_metadata_ranges(source, tmp_path):
    p = project(source)
    original_objects = p.list_objects()
    new_id = p.duplicate_object(20)
    assert new_id not in {20, 40}
    objects = p.list_objects()
    assert objects[:2] == original_objects
    assert objects[2]["object_id"] == new_id
    assert objects[2]["parts"] == original_objects[0]["parts"]
    assert objects[2]["config"] == original_objects[0]["config"]
    assert [i["plate"] for i in objects[2]["instances"]] == [1, 2]
    output = tmp_path / "duplicated.3mf"
    p.save(output)
    saved = payloads(output)
    assert {n for n in saved if n.startswith("3D/Objects/")} == {"3D/Objects/painted.model", "3D/Objects/negative.model"}
    for name in ("3D/Objects/painted.model", "3D/Objects/negative.model"):
        assert saved[name] == payloads(source)[name]
    root = ET.fromstring(saved["3D/3dmodel.model"])
    roots = root.find(f"{{{CORE}}}resources")
    assert [e.get("id") for e in roots] == ["20", "40", str(new_id)]
    original_uuid = roots[0].get(f"{{{PROD}}}UUID")
    assert roots[2].get(f"{{{PROD}}}UUID") != original_uuid
    uuids = [e.get(f"{{{PROD}}}UUID") for e in root.iter() if e.get(f"{{{PROD}}}UUID")]
    assert len(set(uuids)) == len(uuids)
    ranges = ET.fromstring(saved[RANGES])
    assert [e.get("id") for e in ranges] == ["1", "2", "3"]
    assert ET.tostring(ranges[0][0]) == ET.tostring(ranges[2][0])
    assert p.get_layer_profile(new_id) == p.get_layer_profile(20)


def test_delete_preserves_other_root_ids_and_remaps_legacy_layer_metadata(source, tmp_path):
    p = project(source)
    p.delete_object(20)
    assert [o["object_id"] for o in p.list_objects()] == [40]
    assert p.get_layer_profile(40) == [0, 0.2, 1, 0.2, 4, 0.12]
    assert p.list_plates()[0]["objects"] == [] and p.list_plates()[1]["objects"] == []
    output = tmp_path / "deleted.3mf"
    p.save(output)
    saved = payloads(output)
    ranges = ET.fromstring(saved[RANGES])
    assert [e.get("id") for e in ranges] == ["1"]
    assert ranges[0][0].get("min_z") == "3"
    assert [e.get("objectid") for e in build_items(output)] == ["40"]
    settings = ET.fromstring(saved["Metadata/model_settings.config"])
    assert [e.get("id") for e in settings.findall("object")] == ["40"]
    assert [e.get("object_id") for e in settings.findall("assemble/assemble_item")] == ["40"]
    assert saved["3D/Objects/painted.model"] == payloads(source)["3D/Objects/painted.model"]
    assert "object_id=40|" in saved[PROFILE].decode()


def test_unknown_object_mutations_raise(source):
    p = project(source)
    for action in (p.duplicate_object, p.delete_object):
        with pytest.raises(ValueError):
            action(999)


def test_versioned_profiles_use_root_id_while_legacy_profiles_use_ordinal(source, tmp_path):
    p = project(source)
    assert p.get_layer_profile(20) == [0, 0.2, 1, 0.2, 2, 0.16]
    p.set_layer_profile(20, [0, 0.12, 1, 0.12, 2, 0.2])
    output = tmp_path / "profile.3mf"
    p.save(output)
    profile = payloads(output)[PROFILE].decode()
    assert profile.startswith("layer_heights_profile_format_version=2\n")
    assert "object_id=20|" in profile and "object_id=40|" in profile
    reloaded = project(output)
    assert reloaded.get_layer_profile(20) == [0, 0.12, 1, 0.12, 2, 0.2]
    assert reloaded.get_layer_profile(40) == [0, 0.2, 1, 0.2, 4, 0.12]


@pytest.mark.parametrize("profile", [[0, 0.2, 1, 0.2], [0, 0.2, 1, 0.2, 2],
                                      [0, 0.2, 2, 0.2, 1, 0.2], [0, 0.2, 1, 0, 2, 0.2]])
def test_invalid_layer_profile_rejects_without_corrupting_existing_profile(source, profile):
    p = project(source)
    before = p.get_layer_profile(20)
    with pytest.raises(ValueError):
        p.set_layer_profile(20, profile)
    assert p.get_layer_profile(20) == before


def test_switching_machine_preset_id_keeps_same_machine_and_materials(source):
    p = project(source)
    p.set_config({"printer_settings_id": "My U1 0.4 approved profile"})
    assert p.config["printer_settings_id"] == "My U1 0.4 approved profile"
    assert p.config["printer_model"] == "Snapmaker U1"
    assert p.config["filament_settings_id"] == ["_GENERIC_PETG", "_GENERIC_PLA"]


@pytest.mark.parametrize("extruder", [-1, 3, 1.5, "1.5", True])
def test_material_assignment_rejects_nonexistent_or_noninteger_slots(source, extruder):
    p = project(source)
    before = ET.tostring(p.settings)
    with pytest.raises(ValueError):
        p.set_object_config(20, {"extruder": extruder}, part_id=3)
    assert ET.tostring(p.settings) == before


def test_material_zero_inherits_and_last_slot_is_accepted(source):
    p = project(source)
    p.set_object_config(20, {"extruder": 0})
    p.set_object_config(20, {"extruder": "2"}, part_id=3)
    assert p.list_objects()[0]["config"]["extruder"] == "0"
    assert p.list_objects()[0]["parts"][0]["config"]["extruder"] == "2"


def test_add_plate_reflows_native_grid_only_when_column_count_changes(source):
    p = project(source)
    original = p.list_objects()
    assert p.add_plate("Fourth") == 4
    assert p.list_objects() == original
    assert p.add_plate("Fifth") == 5
    assert p.list_plates()[-1] == {"plate": 5, "name": "Fifth", "locked": False, "objects": []}
    objects = p.list_objects()
    assert objects[0] == original[0]
    assert objects[1]["instances"][0]["transform"][-3:] == pytest.approx([663, 25, 0])
    p.delete_plate(5)
    assert p.list_objects() == original
    assert [x["plate"] for x in p.list_plates()] == [1, 2, 3, 4]


def test_move_one_instance_between_plates_keeps_local_offsets_and_ids(source):
    p = project(source)
    p.move_object_to_plate(40, 2)
    p.move_object_to_plate(20, 3, instance_id=0)
    objects = p.list_objects()
    assert objects[0]["instances"][0]["transform"][-3:] == pytest.approx([10, -304, 0])
    assert objects[0]["instances"][1]["transform"][-3:] == [334, 20, 0]
    assert objects[1]["instances"][0]["transform"][-3:] == pytest.approx([339, 25, 0])
    assert [i["plate"] for i in objects[0]["instances"]] == [3, 2]
    assert p.list_plates()[0]["objects"] == []
    assert [e.get("value") for e in p.settings.iter() if e.get("key") == "identify_id"] == ["102", "103", "101"]


def test_delete_empty_plate_compacts_grid_and_native_custom_gcode_indices(source):
    p = project(source)
    p.data["Metadata/custom_gcode_per_layer.xml"] = b'<custom_gcodes_per_layer><plate><plate_info id="2"/><layer top_z="4" type="0" extruder="1" color="" extra="keep pause"/></plate></custom_gcodes_per_layer>'
    p.move_object_to_plate(20, 2, instance_id=0)
    p.delete_plate(1)
    assert [x["plate"] for x in p.list_plates()] == [1, 2]
    assert [i["transform"][-3:] for i in p.list_objects()[0]["instances"]] == [[10, 20, 0], [10, 20, 0]]
    assert p.list_objects()[1]["instances"][0]["transform"][-3:] == [339, 25, 0]
    pauses = ET.fromstring(p.data["Metadata/custom_gcode_per_layer.xml"])
    assert pauses.find("plate/plate_info").get("id") == "1"
    assert pauses.find("plate/layer").get("extra") == "keep pause"


def test_plate_errors_do_not_change_placements_or_membership(source):
    p = project(source)
    before = p.describe()
    for action in (lambda: p.delete_plate(99),
                   lambda: p.move_object_to_plate(20, 99),
                   lambda: p.move_object_to_plate(20, 2, instance_id=8)):
        with pytest.raises(ValueError):
            action()
        assert p.describe() == before
    p.delete_object(20)
    p.delete_object(40)
    p.delete_plate(3)
    p.delete_plate(2)
    with pytest.raises(ValueError):
        p.delete_plate(1)


def test_plate_grid_requires_explicit_bed_dimensions_before_any_mutation(source):
    p = project(source)
    del p.config["printable_area"]
    before = p.describe()
    with pytest.raises(ValueError):
        p.move_object_to_plate(20, 3)
    assert p.describe() == before


def test_height_range_uses_ordinal_and_clears_only_its_continuous_profile(source):
    p = project(source)
    p.set_height_range(40, 5, 6, 0.1)
    root = ET.fromstring(p.data[RANGES])
    assert [x.get("id") for x in root] == ["1", "2"]
    ranges = root.find("object[@id='2']").findall("range")
    assert [(r.get("min_z"), r.get("max_z")) for r in ranges] == [("3", "4"), ("5", "6")]
    assert ranges[-1].find("option[@opt_key='layer_height']").text == "0.1"
    assert p.get_layer_profile(40) == []
    assert p.get_layer_profile(20) == [0, 0.2, 1, 0.2, 2, 0.16]
    assert p.list_objects()[1]["object_id"] == 40


def test_identical_height_range_updates_height_without_losing_other_options(source):
    p = project(source)
    p.set_height_range(20, 1, 2, 0.08)
    root = ET.fromstring(p.data[RANGES])
    ranges = root.find("object[@id='1']").findall("range")
    assert len(ranges) == 1
    assert ranges[0].find("option[@opt_key='wall_loops']").text == "4"
    assert ranges[0].find("option[@opt_key='layer_height']").text == "0.08"


def test_height_range_overlap_rejects_and_clear_removes_only_target_bands(source):
    p = project(source)
    before = dict(p.data)
    with pytest.raises(ValueError):
        p.set_height_range(20, 1.5, 2.5, 0.1)
    assert p.data == before
    p.set_height_range(20, 1.5, 2.5, 0.1, clear=True)
    root = ET.fromstring(p.data[RANGES])
    assert root.find("object[@id='1']") is None
    assert root.find("object[@id='2']/range").get("min_z") == "3"
    assert p.get_layer_profile(20) == [0, 0.2, 1, 0.2, 2, 0.16]
    p.set_height_range(40, clear=True)
    assert RANGES not in p.data


@pytest.mark.parametrize("values", [(-1, 2, 0.2), (2, 1, 0.2), (1, 1, 0.2),
                                    (1, 2, 0), (1, 2, float("nan"))])
def test_invalid_height_range_leaves_all_metadata_unchanged(source, values):
    p = project(source)
    before = dict(p.data)
    with pytest.raises(ValueError):
        p.set_height_range(20, *values)
    assert p.data == before


def test_reset_layer_height_removes_target_ranges_and_profile_only(source):
    p = project(source)
    p.reset_layer_height(20)
    assert [e.get("id") for e in ET.fromstring(p.data[RANGES])] == ["2"]
    assert p.get_layer_profile(20) == []
    assert p.get_layer_profile(40) == [0, 0.2, 1, 0.2, 4, 0.12]
    p.reset_layer_height(40)
    assert RANGES not in p.data and PROFILE not in p.data


def test_direct_build_tree_mutation_is_serialized_without_explicit_dirty_flag(source, tmp_path):
    p = project(source)
    item = p.model.find(f"{{{CORE}}}build/{{{CORE}}}item")
    item.set("transform", "1 0 0 0 1 0 0 0 1 23 34 45")
    output = tmp_path / "native-delta.3mf"
    p.save(output)
    assert build_items(output)[0].get("transform") == "1 0 0 0 1 0 0 0 1 23 34 45"


def test_new_range_and_profile_members_have_content_types(source, tmp_path):
    p = project(source)
    p.data.pop(RANGES)
    p.data.pop(PROFILE)
    p.set_height_range(20, 0, 1, 0.16)
    p.set_layer_profile(40, [0, 0.12, 1, 0.12, 4, 0.16])
    output = tmp_path / "new-layer-metadata.3mf"
    p.save(output)
    types = ET.fromstring(payloads(output)["[Content_Types].xml"])
    for name in (RANGES, PROFILE):
        assert any(e.get("PartName") == "/" + name or e.get("Extension") == name.rsplit(".", 1)[1] for e in types)


def test_real_native_fixture_is_readonly_and_edits_preserve_split_geometry(tmp_path):
    fixture = Path(__file__).resolve().parents[3] / ".temp/engine-update-9bf/linux/full-image-state/input.3mf"
    if not fixture.is_file():
        pytest.skip("Optional private native fixture is not distributed; see TEST.md for its audited hash")
    source_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    assert source_hash == "a2765234f4a206ba55928492f6b7f1d3a95fc38345a09bbc7d83bb036831b49d"
    original = payloads(fixture)
    p = project(fixture)
    assert p.list_objects()[0]["object_id"] == 2
    p.set_object_config(2, {"wall_loops": 3, "extruder": 2})
    p.transform_object(2, translate=[1, -1, 0])
    p.set_height_range(2, 0, 2, 0.12)
    clone = p.duplicate_object(2)
    p.add_plate()
    p.move_object_to_plate(clone, 2)
    output = tmp_path / "real-edited.3mf"
    p.save(output)
    saved = payloads(output)
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == source_hash
    for name in original:
        if name.startswith("3D/Objects/"):
            assert saved[name] == original[name]
    assert saved["3D/_rels/3dmodel.model.rels"] == original["3D/_rels/3dmodel.model.rels"]
    reloaded = project(output)
    assert [o["object_id"] for o in reloaded.list_objects()] == [2, clone]
    assert reloaded.list_objects()[1]["instances"][0]["plate"] == 2
    assert 'requiredextensions="p"' in saved["3D/3dmodel.model"].decode()


def test_layer_ordinals_follow_first_build_occurrence_not_resources_order(source, tmp_path):
    p = project(source)
    resources = p.model.find(f"{{{CORE}}}resources")
    resources[:] = list(reversed(resources))
    shuffled = tmp_path / "resources-reordered.3mf"
    p.save(shuffled)
    p = project(shuffled)
    assert p.get_layer_profile(20) == [0, 0.2, 1, 0.2, 2, 0.16]
    p.set_height_range(20, 1, 2, 0.1)
    assert ET.fromstring(p.data[RANGES]).find("object[@id='1']/range/option").text == "0.1"
    p.delete_object(20)
    assert ET.fromstring(p.data[RANGES]).find("object[@id='1']/range").get("min_z") == "3"


def test_delete_occupied_plate_parks_instances_without_deleting_geometry(source, tmp_path):
    p = project(source)
    p.delete_plate(2)
    objects = p.list_objects()
    assert [o["object_id"] for o in objects] == [20, 40]
    assert [i["plate"] for i in objects[0]["instances"]] == [1, None]
    assert objects[0]["instances"][1]["transform"][-3:] == [10, -304, 0]
    assert objects[1]["instances"][0]["transform"][-3:] == [339, 25, 0]
    assert [x["plate"] for x in p.list_plates()] == [1, 2]
    saved = tmp_path / "parked.3mf"
    p.save(saved)
    restored = project(saved)
    restored.move_object_to_plate(20, 2, instance_id=1)
    assert restored.list_objects()[0]["instances"][1]["transform"][-3:] == [459, 135, 0]
    assert restored.list_objects()[0]["instances"][1]["plate"] == 2
    assert sorted(e.get("value") for e in restored.settings.iter() if e.get("key") == "identify_id") == ["101", "102", "103"]
    assert payloads(saved)["3D/Objects/painted.model"] == payloads(source)["3D/Objects/painted.model"]


def test_occupied_plate_delete_compacts_per_plate_wipe_tower_positions(source):
    p = project(source)
    p.config.update(wipe_tower_x=["10", "20", "30"], wipe_tower_y=["40", "50", "60"])
    p.delete_plate(2)
    assert p.config["wipe_tower_x"] == ["10", "30"]
    assert p.config["wipe_tower_y"] == ["40", "60"]
    assert p.config["filament_colour"] == ["#111111", "#EEEEEE"]


def test_auxiliary_gcode_is_a_reference_payload_not_a_stale_slice(source, tmp_path):
    p = project(source)
    p.data["Auxiliaries/reference.gcode"] = b"; customer reference, keep bytes\n"
    output = tmp_path / "auxiliary-gcode.3mf"
    p.save(output)
    assert payloads(output)["Auxiliaries/reference.gcode"] == p.data["Auxiliaries/reference.gcode"]


def test_another_project_namespace_registration_does_not_dirty_unchanged_model(source, tmp_path):
    original = payloads(source)
    p = project(source)
    alternate = dict(original)
    alternate["3D/3dmodel.model"] = original["3D/3dmodel.model"].replace(b"xmlns:p=", b"xmlns:prod=").replace(b" p:", b" prod:").replace(b'requiredextensions="p q"', b'requiredextensions="prod q"')
    second_source = tmp_path / "alternate-prefixes.3mf"
    with zipfile.ZipFile(second_source, "w") as archive:
        for name, data in alternate.items():
            archive.writestr(name, data)
    second = project(second_source)
    second.transform_object(20, translate=[1, 0, 0])
    second.save(tmp_path / "second-edited.3mf")
    p.set_config({"wall_loops": "6"})
    output = tmp_path / "config-only.3mf"
    p.save(output)
    assert payloads(output)["3D/3dmodel.model"] == original["3D/3dmodel.model"]


@pytest.mark.parametrize("key,value,serialized", [
    ("inner_wall_speed", ["100", "150"], "100,150"),
    ("additional_cooling_fan_speed", ["0", "70"], "0,70"),
    ("adaptive_pressure_advance", ["1", "0", "nil"], "1,0,nil"),
    ("filament_retract_before_wipe", ["80%", "nil"], "80%,nil"),
    ("bridge_acceleration", ["1000", "50%"], "1000,50%"),
    ("filament_z_hop_types", ["Normal Lift", "Spiral Lift"], "Normal Lift,Spiral Lift"),
    ("printable_area", ["0x0", "270x0", "270x270"], "0x0,270x0,270x270"),
    ("bed_mesh_min", "10x20", "10,20"),
    ("filament_settings_id", ["PLA", "SILK profile", 'quote"inside', "line\nbreak", "C:\\path"],
     'PLA;"SILK profile";"quote\\"inside";"line\\nbreak";"C:\\\\path"'),
    ("filament_settings_id", [""], '""'),
    ("filament_settings_id", [], ""),
    ("filament_settings_id", ["a;b", "c"], '"a;b";c'),
    ("before_layer_change_gcode", 'G1 X1\n; quote" \\path\r', 'G1 X1\\n; quote\\" \\\\path\\r'),
])
def test_object_overrides_use_native_config_serialization_for_xml(source, tmp_path, key, value, serialized):
    p = project(source)
    original_config = p.get_config()
    p.set_object_config(20, {key: value}, part_id=3)
    output = tmp_path / "serialized-overrides.3mf"
    p.save(output)
    settings = ET.fromstring(payloads(output)["Metadata/model_settings.config"])
    part = settings.find("object[@id='20']/part[@id='3']")
    assert part.find(f"metadata[@key='{key}']").get("value") == serialized
    assert p.config == original_config


def test_unknown_vector_override_is_rejected_before_any_metadata_changes(source):
    p = project(source)
    before = ET.tostring(p.settings)
    with pytest.raises(ValueError):
        p.set_object_config(20, {"wall_loops": 4, "unknown_native_vector": ["x", "y"]})
    assert ET.tostring(p.settings) == before


def test_duplicate_and_delete_remap_auxiliary_ordinals_without_changing_cut_or_source_ids(source):
    p = project(source)
    clone = p.duplicate_object(20)
    assert p.data[BRIM].endswith(b"object_id=3|1 2 3 4\n")
    cuts = ET.fromstring(p.data[CUT])
    assert [e.get("id") for e in cuts] == ["1", "2", "3"]
    assert ET.tostring(cuts[0].find("cut_id")) == ET.tostring(cuts[2].find("cut_id"))
    assert ET.tostring(cuts[0].find("connectors")) == ET.tostring(cuts[2].find("connectors"))
    p.delete_object(20)
    assert p.data[BRIM] == b"brim_points_format_version=0\nobject_id=1|10 20 30 40\nobject_id=2|1 2 3 4\n"
    cuts = ET.fromstring(p.data[CUT])
    assert [e.get("id") for e in cuts] == ["1", "2"]
    assert [e.find("cut_id").get("id") for e in cuts] == ["789", "123"]
    assert [o["object_id"] for o in p.list_objects()] == [40, clone]
    settings = p.settings.find(f"object[@id='{clone}']/part[@id='3']")
    assert settings.find("metadata[@key='source_object_id']").get("value") == "9"
    assert settings.find("metadata[@key='source_volume_id']").get("value") == "6"


def test_native_layout_adds_plate_preserves_members_and_inherits_filament_maps(source, tmp_path):
    p = project(source)
    p.delete_object(40)
    p.move_object_to_plate(20, 1)
    p.delete_plate(3)
    p.delete_plate(2)
    plate = p.settings.find("plate")
    ET.SubElement(plate, "metadata", {"key": "filament_maps", "value": "1 2"})
    ET.SubElement(plate, "metadata", {"key": "filament_map_mode", "value": "Manual"})
    ET.SubElement(plate, "metadata", {"key": "bed_type", "value": "Cool Plate"})
    layout = {"plate_count": 2, "instances": [{"object_id": 20, "instance_id": 0, "plate": 1},
                                               {"object_id": 20, "instance_id": 1, "plate": 2}]}
    result = p.apply_native_layout(layout, {(20, 0): IDENTITY, (20, 1): IDENTITY[:9] + [324, 0, 0]})
    assert result["plate_count"] == 2
    instances = p.list_objects()[0]["instances"]
    assert [i["plate"] for i in instances] == [1, 2]
    assert [i["transform"][-3:] for i in instances] == [[10, 20, 0], [334, 20, 0]]
    second = p.settings.findall("plate")[1]
    assert second.find("metadata[@key='filament_maps']").get("value") == "1 2"
    assert second.find("metadata[@key='filament_map_mode']").get("value") == "Manual"
    assert second.find("metadata[@key='bed_type']").get("value") == "Cool Plate"
    assert [e.get("value") for e in p.settings.iter() if e.get("key") == "identify_id"] == ["101", "102"]
    output = tmp_path / "native-two-plates.3mf"
    p.save(output)
    assert payloads(output)["3D/Objects/painted.model"] == payloads(source)["3D/Objects/painted.model"]


@pytest.mark.parametrize("old_count,new_count", [(4, 3), (5, 4)])
def test_native_layout_shrinks_grid_reflows_locked_unselected_and_applies_world_delta_once(source, old_count, new_count):
    p = project(source)
    for _ in range(3, old_count):
        p.add_plate()
    p.move_object_to_plate(20, 4, instance_id=1)
    third = p.settings.findall("plate")[2]
    ET.SubElement(third, "metadata", {"key": "locked", "value": "true"})
    original = {o["object_id"]: o for o in p.list_objects()}
    if old_count == 5:
        destination, delta = 4, [324, 0, 0]
    else:
        destination, delta = 2, [0, 324, 0]
    layout = {"plate_count": new_count, "instances": [
        {"object_id": 20, "instance_id": 0, "plate": 1},
        {"object_id": 20, "instance_id": 1, "plate": destination}]}
    p.apply_native_layout(layout, {(20, 0): IDENTITY[:9] + [1, 2, 0], (20, 1): IDENTITY[:9] + delta})
    objects = {o["object_id"]: o for o in p.list_objects()}
    before = original[20]["instances"][1]["transform"][-3:]
    assert objects[20]["instances"][1]["transform"][-3:] == [a + b for a, b in zip(before, delta)]
    assert objects[20]["instances"][0]["transform"][-3:] == [11, 22, 0]
    assert objects[40]["instances"][0]["transform"][-3:] == [15, -299, 0]
    assert p.list_plates()[2]["locked"] is True
    assert len(p.list_plates()) == new_count


def test_native_layout_rejects_removing_a_locked_source_plate_atomically(source):
    p = project(source)
    p.add_plate()
    p.add_plate()
    fifth = p.settings.findall("plate")[4]
    ET.SubElement(fifth, "metadata", {"key": "locked", "value": "true"})
    before = (ET.tostring(p.model), ET.tostring(p.settings), p.get_config(), dict(p.data))
    layout = {"plate_count": 4, "instances": [{"object_id": 20, "instance_id": 0, "plate": 1}]}
    with pytest.raises(ValueError):
        p.apply_native_layout(layout, {(20, 0): IDENTITY})
    assert (ET.tostring(p.model), ET.tostring(p.settings), p.get_config(), p.data) == before


def test_native_layout_restores_parked_metadata_with_provided_world_pose(source):
    p = project(source)
    p.delete_plate(2)
    layout = {"plate_count": 2, "instances": [{"object_id": 20, "instance_id": 1, "plate": 2}]}
    p.apply_native_layout(layout, {(20, 1): IDENTITY[:9] + [449, 439, 0]})
    assert p.list_objects()[0]["instances"][1]["transform"][-3:] == [459, 135, 0]
    assert p.list_objects()[0]["instances"][1]["plate"] == 2
    assert sorted(e.get("value") for e in p.settings.iter() if e.get("key") == "identify_id") == ["101", "102", "103"]
    assert "Metadata/slicer_mcp_parked_instances.xml" not in p.data


@pytest.mark.parametrize("layout,deltas", [
    ({"plate_count": 0, "instances": []}, {}),
    ({"plate_count": 3, "instances": [{"object_id": 999, "instance_id": 0, "plate": 1}]}, {(999, 0): IDENTITY}),
    ({"plate_count": 3, "instances": [{"object_id": 20, "instance_id": 0, "plate": 4}]}, {(20, 0): IDENTITY}),
    ({"plate_count": 3, "instances": [{"object_id": 20, "instance_id": 0, "plate": 1}]}, {}),
])
def test_invalid_native_layout_does_not_change_project(source, layout, deltas):
    p = project(source)
    before = (ET.tostring(p.model), ET.tostring(p.settings), p.get_config(), dict(p.data))
    with pytest.raises(ValueError):
        p.apply_native_layout(layout, deltas)
    assert (ET.tostring(p.model), ET.tostring(p.settings), p.get_config(), p.data) == before


def test_native_layout_new_membership_cannot_reuse_an_existing_selected_identify_id(source):
    p = project(source)
    first = p.settings.find("plate")
    first.remove(first.find("model_instance"))
    layout = {"plate_count": 3, "instances": [{"object_id": 20, "instance_id": 0, "plate": 1},
                                               {"object_id": 40, "instance_id": 0, "plate": 3}]}
    p.apply_native_layout(layout, {(20, 0): IDENTITY, (40, 0): IDENTITY})
    ids = [e.get("value") for e in p.settings.iter() if e.get("key") == "identify_id"]
    assert len(ids) == len(set(ids))
    assert "102" in ids and "103" in ids
