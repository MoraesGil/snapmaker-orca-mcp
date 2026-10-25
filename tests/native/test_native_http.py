"""Maintained real HTTP/native acceptance, isolated from any installed service.

One collector owns the complete workflow so a selected subset cannot report the
mcp_http case as passing. Helpers split each bounded scenario for review.
"""
from pathlib import Path

import pytest

from .conftest import record
from .harness import assert_effect, make_project, official_config
from .http_semantics import semantics
from .http_transport import Calls, digest, gcode, server_session
from .http_workflows import adaptive, arrangement, edits, orient, presets, ranges

EXPECTED_TOOLS = {
    'add_modifier', 'add_plate', 'apply_and_slice', 'arrange_plate', 'auto_orient',
    'delete_object', 'delete_plate', 'delete_preset', 'describe_setting',
    'duplicate_object', 'edit_preset', 'edit_project', 'engine_status', 'get_config',
    'get_preset_config', 'get_status', 'list_objects', 'list_plates', 'list_presets',
    'load_model', 'move_object_to_plate', 'recover_3mf', 'rename_preset',
    'save_preset', 'search_settings', 'select_plate', 'select_preset', 'set_config',
    'set_height_range', 'set_layer_height', 'set_object_config', 'slice_3mf',
    'slice_project', 'transform_object',
}


async def test_real_native_http_complete_workflow(native_engine, tmp_path):
    config = official_config(native_engine, tmp_path)
    resource_root = Path(native_engine.manifest['resources'])
    preset_hashes = {path: digest(path) for path in resource_root.rglob('*.json')}
    originals = {}
    completed = {}
    record(native_engine, 'mcp_http', 'running')
    try:
        async with server_session(native_engine, tmp_path) as session:
            call = Calls(session)
            discovered = {tool.name for tool in (await session.list_tools()).tools}
            assert discovered == EXPECTED_TOOLS
            identity = await call(session, 'engine_status', check_updates=False)
            assert identity['source_commit'] == '9bf2f18eaff7cc0655cbdeef6a215660085ecb0f'

            async def load(name):
                source = make_project(tmp_path / (name + '.3mf'), config)
                originals[source] = source.read_bytes()
                loaded = await call(session, 'load_model', path=str(source))
                assert loaded['sha256'] == digest(source)
                return {**loaded, 'source_path': str(source)}

            for name, workflow in [
                ('semantics', semantics), ('edits', edits), ('presets', presets),
                ('arrangement', arrangement), ('orientation', orient),
                ('adaptive', adaptive), ('height_range', ranges),
            ]:
                completed[name] = await workflow(session, call, load)
            assert call.names == EXPECTED_TOOLS
            assert all(path.read_bytes() == data for path, data in originals.items())
            assert {path: digest(path) for path in resource_root.rglob('*.json')} == preset_hashes
        # Exiting the transport context must successfully reap the subprocess.
        record(native_engine, 'mcp_http', 'pass', {
            'scope': 'all 34 MCP HTTP tools positively invoked; bounded synthetic native workflows, no printer',
            'tools_exercised': sorted(call.names), 'scenarios': completed,
            'sources_preserved': len(originals), 'official_preset_files_preserved': len(preset_hashes),
        })
    except BaseException as error:
        record(native_engine, 'mcp_http', 'fail', {'error_type': type(error).__name__})
        raise


def test_http_semantic_oracles_reject_missing_and_ignored_output(tmp_path):
    """A missing file and an unchanged speed cannot satisfy the native oracle."""
    with pytest.raises(AssertionError, match='missing'):
        gcode({'plates': [{'gcode_path': str(tmp_path / 'missing.gcode')}]})
    comment_only = tmp_path / 'comment-only.gcode'
    comment_only.write_text('; outer_wall_speed = 60\n; FEATURE: Outer wall\n')
    with pytest.raises(AssertionError, match='no linear extrusion'):
        gcode({'plates': [{'gcode_path': str(comment_only), 'sha256': digest(comment_only)}]})
    low = {'moves': [{'feature': 'Outer wall', 'z': 1., 'f': 1200.}]}
    with pytest.raises(AssertionError):
        assert_effect('outer_wall_speed', low, low)
    with pytest.raises(AssertionError):
        assert_effect('outer_wall_speed', {'moves': []}, {'moves': []})
