"""Shared public-fixture contract helpers. Expectations never call normalization.

schema_manifest.json freezes representative accepted values and expected strings,
not compiled defaults or slice-ready configurations. Review that file deliberately
when the full schema fingerprint changes; never regenerate it during test runs.
Named XML strings are independent importer serialization expectations only.
"""
import hashlib
import json
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def manifest():
    return json.loads((Path(__file__).parent / 'schema_manifest.json').read_text())


def schema_fingerprint():
    return hashlib.sha256((ROOT / 'src/snapmaker_slicer_mcp/data/settings.json').read_bytes()).hexdigest()


def named_cases():
    result = []
    for path in sorted((ROOT / 'tests/property_cases').glob('*.json')):
        case = json.loads(path.read_text())
        assert set(case) == {'key', 'batch', 'valid', 'invalid'}, path
        assert path.stem == case['key'] and type(case['batch']) is int, path
        assert case['valid'] and case['invalid'], path
        for row in case['valid']:
            assert set(row) == {'value', 'normalized', 'xml'}, path
        result.append(case)
    return result


def editor_fixture(tmp_path, part=False):
    from test_editor import project
    from test_service import boundary
    from snapmaker_slicer_mcp.editor import Editor
    source = project(tmp_path / 'input.3mf')
    if part:
        with zipfile.ZipFile(source) as z:
            payloads = {n: z.read(n) for n in z.namelist()}
        root = ET.fromstring(payloads['Metadata/model_settings.config'])
        ET.SubElement(root.find('object'), 'part', {'id': '2', 'subtype': 'normal_part'})
        payloads['Metadata/model_settings.config'] = ET.tostring(root)
        with zipfile.ZipFile(source, 'w') as z:
            for name, value in payloads.items():
                z.writestr(name, value)
    editor = Editor(boundary(tmp_path)[0])
    return editor, source, editor.load_model(str(source))


def assert_saved(editor, source, original, state, key, normalized, xml=None, scope='global'):
    from snapmaker_slicer_mcp.project import Project
    assert source.read_bytes() == original
    with zipfile.ZipFile(state['path']) as z:
        assert z.read('Auxiliaries/keep.bin') == b'preserve this payload'
        with zipfile.ZipFile(source) as before:
            for name in before.namelist():
                if name not in {'Metadata/project_settings.config', 'Metadata/model_settings.config'} and not name.endswith('.gcode'):
                    assert z.read(name) == before.read(name), name
            old_config = json.loads(before.read('Metadata/project_settings.config'))
            new_config = json.loads(z.read('Metadata/project_settings.config'))
            assert {k: v for k, v in new_config.items() if k != key} == {k: v for k, v in old_config.items() if k != key}
            if scope != 'global':
                assert new_config == old_config
        assert 'Metadata/plate_1.gcode' not in z.namelist()
        if scope == 'global':
            assert json.loads(z.read('Metadata/project_settings.config'))[key] == normalized
            assert Project(Path(state['path'])).get_config()[key] == normalized
        else:
            root = ET.fromstring(z.read('Metadata/model_settings.config'))
            target = root.find('object') if scope == 'object' else root.find('object/part')
            assert target.find(f"metadata[@key='{key}']").get('value') == xml
    reopened = editor.load_model(state['path'])
    assert reopened['sha256'] == state['sha256']


def assert_atomic(editor, project_id, operation):
    import pytest
    before = editor.get_status(project_id)
    directory = Path(before['path']).parent
    files = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    with pytest.raises(ValueError):
        operation()
    assert editor.get_status(project_id)['sha256'] == before['sha256']
    assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == files
