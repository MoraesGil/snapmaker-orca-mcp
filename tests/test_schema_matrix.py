from regression.contracts import manifest, schema_fingerprint


def test_schema_identity_is_pinned():
    assert schema_fingerprint() == manifest()['schema_sha256']

import pytest
from snapmaker_slicer_mcp.presets import load_schema, validate_changes
from regression.contracts import editor_fixture, assert_saved, assert_atomic

ROWS = manifest()['cases']


def test_schema_population_and_unparsed_are_explicit():
    assert len(ROWS) == manifest()['count'] == 699
    assert {k: r['type'] for k, r in ROWS.items()} == {k: r['type'] for k, r in load_schema().items()}
    assert len(manifest()['unparsed_keys']) == 91
    assert set(manifest()['unparsed_keys']) <= set(ROWS)
    assert len(set(manifest()['unparsed_keys'])) == 91
    # Legacy name means missing human documentation, not unsupported types.
    schema = load_schema()
    assert set(manifest()['unparsed_keys']) == {k for k, r in schema.items() if not r.get('label') and not r.get('tooltip')}


@pytest.mark.parametrize('key', sorted(ROWS))
def test_schema_contract_and_persistence(key, tmp_path):
    row = ROWS[key]
    editor, source, state = editor_fixture(tmp_path)
    original = source.read_bytes()
    pid = state['project_id']
    if row['policy']:
        with pytest.raises(ValueError, match='prohibited|allow_custom_gcode'):
            validate_changes({key: row['value']}, {})
        assert_atomic(editor, pid, lambda: editor.set_config(pid, {key: row['value']}))
    else:
        assert validate_changes({key: row['value']}, {}) == {key: row['normalized']}
        result = editor.set_config(pid, {key: row['value']})
        assert_saved(editor, source, original, result, key, row['normalized'])
    assert_atomic(editor, pid, lambda: editor.set_config(pid, {key: row['invalid']}))
    assert source.read_bytes() == original


def test_report_does_not_promote_inventory_or_skips_to_execution(tmp_path):
    import runpy
    from regression.contracts import ROOT
    report = runpy.run_path(str(ROOT / 'scripts/property_coverage.py'))['report']
    inventory = report()
    assert inventory['contract_matrix_executed_passed'] == []
    assert inventory['native_executed_passed_testcases'] == []
    assert len(inventory['missing_native_semantic_coverage']) == 699
    evidence = tmp_path / 'results.xml'
    evidence.write_text('''<testsuite>
      <testcase classname="test_schema_matrix" name="test_schema_contract_and_persistence[layer_height]"/>
      <testcase classname="test_schema_matrix" name="test_schema_contract_and_persistence[layer_height]"/>
      <testcase classname="lookalike" name="test_schema_contract_and_persistence[wall_loops]"/>
      <testcase classname="tests.native.test_effects" name="test_speed"><skipped/></testcase>
      <testcase classname="tests.native.test_effects" name="test_height"><failure/></testcase>
      <testcase classname="tests.native.test_effects" name="test_wall"/>
    </testsuite>''')
    observed = report(evidence)
    assert observed['contract_matrix_executed_passed'] == ['layer_height']
    assert observed['native_executed_passed_testcases'] == ['tests.native.test_effects.test_wall']
    # Run evidence needs explicit semantic attribution, never substring inference.
    assert len(observed['missing_native_semantic_coverage']) == 699


@pytest.mark.parametrize('scope', ['global', 'object', 'part'])
@pytest.mark.parametrize('corruption', ['object_name', 'instance_id', 'part_subtype'])
def test_saved_oracle_detects_unrelated_metadata_corruption(tmp_path, scope, corruption):
    import zipfile
    import xml.etree.ElementTree as ET
    editor, source, loaded = editor_fixture(tmp_path, part=True)
    original = source.read_bytes()
    operation = {'operation': 'set_config' if scope == 'global' else 'set_object_config',
                 'changes': {'layer_height': 0.12}}
    if scope != 'global':
        operation['object_id'] = 2
    if scope == 'part':
        operation['part_id'] = 2
    state = editor.edit_project(loaded['project_id'], [operation])
    with zipfile.ZipFile(state['path']) as z:
        payloads = {n: z.read(n) for n in z.namelist()}
    root = ET.fromstring(payloads['Metadata/model_settings.config'])
    if corruption == 'object_name':
        root.find("object/metadata[@key='name']").set('value', 'erased name')
    elif corruption == 'instance_id':
        root.find("plate/model_instance/metadata[@key='instance_id']").set('value', '99')
    else:
        root.find('object/part').set('subtype', 'modifier_part')
    payloads['Metadata/model_settings.config'] = ET.tostring(root)
    with zipfile.ZipFile(state['path'], 'w') as z:
        for name, value in payloads.items():
            z.writestr(name, value)
    with pytest.raises(AssertionError, match='model_settings preservation'):
        assert_saved(editor, source, original, state, 'layer_height', '0.12', '0.12', scope)


@pytest.mark.parametrize('scope', ['global', 'object', 'part'])
def test_saved_oracle_allows_only_native_stale_reference_cleanup(tmp_path, scope):
    import zipfile
    import xml.etree.ElementTree as ET
    editor, source, _ = editor_fixture(tmp_path, part=True)
    with zipfile.ZipFile(source) as z:
        payloads = {n: z.read(n) for n in z.namelist()}
    root = ET.fromstring(payloads['Metadata/model_settings.config'])
    ET.SubElement(root.find('plate'), 'metadata', {'key': 'gcode_file', 'value': 'Metadata/plate_1.gcode'})
    ET.SubElement(root.find('plate'), 'metadata', {'key': 'is_sliced_valid', 'value': 'true'})
    payloads['Metadata/model_settings.config'] = ET.tostring(root)
    with zipfile.ZipFile(source, 'w') as z:
        for name, value in payloads.items():
            z.writestr(name, value)
    original = source.read_bytes()
    loaded = editor.load_model(str(source))
    operation = {'operation': 'set_config' if scope == 'global' else 'set_object_config',
                 'changes': {'layer_height': 0.12}}
    if scope != 'global':
        operation['object_id'] = 2
    if scope == 'part':
        operation['part_id'] = 2
    state = editor.edit_project(loaded['project_id'], [operation])
    assert_saved(editor, source, original, state, 'layer_height', '0.12', '0.12', scope)
