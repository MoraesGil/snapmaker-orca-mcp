"""Named independent examples: stored XML is a contract, not engine evidence."""
import pytest
from snapmaker_slicer_mcp.presets import validate_changes
from regression.contracts import named_cases, editor_fixture, assert_saved, assert_atomic

CASES = named_cases()
VALID = [(c['key'], r) for c in CASES for r in c['valid']]
INVALID = [(c['key'], v) for c in CASES for v in c['invalid']]


@pytest.mark.parametrize('scope', ['global', 'object', 'part'])
@pytest.mark.parametrize('key,row', VALID, ids=[f'{k}-{i}' for i, (k, _) in enumerate(VALID)])
def test_named_property_persistence(key, row, scope, tmp_path):
    editor, source, loaded = editor_fixture(tmp_path, part=True)
    original = source.read_bytes()
    assert validate_changes({key: row['value']}, {}) == {key: row['normalized']}
    if scope == 'global':
        state = editor.set_config(loaded['project_id'], {key: row['value']})
    else:
        operation = {'operation': 'set_object_config', 'object_id': 2, 'changes': {key: row['value']}}
        if scope == 'part':
            operation['part_id'] = 2
        state = editor.edit_project(loaded['project_id'], [operation])
    assert_saved(editor, source, original, state, key, row['normalized'], row['xml'], scope)


@pytest.mark.parametrize('scope', ['global', 'object', 'part'])
@pytest.mark.parametrize('key,value', INVALID, ids=[f'{k}-{i}' for i, (k, _) in enumerate(INVALID)])
def test_named_invalid_is_atomic(key, value, scope, tmp_path):
    editor, source, loaded = editor_fixture(tmp_path, part=True)
    original = source.read_bytes()
    with pytest.raises(ValueError):
        validate_changes({key: value}, {})
    operation = {'operation': 'set_config' if scope == 'global' else 'set_object_config', 'changes': {key: value}}
    if scope != 'global':
        operation['object_id'] = 2
    if scope == 'part':
        operation['part_id'] = 2
    assert_atomic(editor, loaded['project_id'], lambda: editor.edit_project(loaded['project_id'], [operation]))
    assert source.read_bytes() == original
