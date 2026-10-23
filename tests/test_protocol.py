import json
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_service import boundary, make_project


def command(tmp_path):
    slicer, _ = boundary(tmp_path)
    engine_file = tmp_path / "engine.json"
    engine_file.write_text(json.dumps(slicer.engine.manifest))
    return slicer, [sys.executable, "-m", "snapmaker_slicer_mcp.cli", "--engine", str(engine_file),
                    "--artifacts", str(slicer.root)]


def test_installed_cli_returns_bound_native_artifacts_and_recovers_original(tmp_path):
    _, argv = command(tmp_path)
    source = make_project(tmp_path / "input.3mf")
    native = subprocess.run([*argv, "slice", str(source), "--plate", "2"], capture_output=True, text=True)
    assert native.returncode == 0, native.stderr + native.stdout
    result = json.loads(native.stdout)
    assert [p["plate"] for p in result["plates"]] == [2]
    recovery = subprocess.run([*argv, "recover", result["plates"][0]["gcode_path"]], capture_output=True, text=True)
    assert recovery.returncode == 0, recovery.stderr
    assert Path(json.loads(recovery.stdout)["path"]).read_bytes() == source.read_bytes()


async def test_mcp_protocol_returns_results_resources_and_errors(tmp_path):
    _, argv = command(tmp_path)
    source = make_project(tmp_path / "input.3mf")
    params = StdioServerParameters(command=argv[0], args=[*argv[1:], "serve", "--no-update-check"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert {"slice_3mf", "recover_3mf", "engine_status"} <= names
            call = await session.call_tool("slice_3mf", {"input_3mf": str(source), "plate": 1})
            assert not call.is_error
            result = call.structured_content or json.loads(call.content[0].text)
            resource = await session.read_resource(result["plates"][0]["gcode_lines_uri"])
            lines = json.loads(resource.contents[0].text)["lines"]
            assert lines[1] == "G1 X1 Y2 Z0.2 E0.3 F1200"
            props = await session.read_resource(result["props_uri"])
            assert json.loads(props.contents[0].text)["native_global"]["layer_height"] == "0.2"
            failure = await session.call_tool("slice_3mf", {"input_3mf": str(source), "plate": -1})
            assert failure.is_error is True


async def test_mcp_edits_working_copy_and_slices_the_saved_revision(tmp_path):
    from test_editor import project
    _, argv = command(tmp_path)
    source = project(tmp_path / 'editable.3mf')
    original = source.read_bytes()
    params = StdioServerParameters(command=argv[0], args=[*argv[1:], 'serve', '--no-update-check'])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            def result(call):
                assert not call.is_error, call.content
                return call.structured_content or json.loads(call.content[0].text)
            loaded = result(await session.call_tool('load_model', {'path': str(source)}))
            edited = result(await session.call_tool('set_config', {'project_id': loaded['project_id'],
                            'changes': {'layer_height': 0.12}, 'expected_sha256': loaded['sha256']}))
            sliced = result(await session.call_tool('slice_project', {'project_id': loaded['project_id'], 'plate': 1}))
            assert sliced['source']['sha256'] == edited['sha256']
            assert source.read_bytes() == original
            failure = await session.call_tool('set_config', {'project_id': loaded['project_id'],
                            'changes': {'layer_height': 0.16}, 'expected_sha256': loaded['sha256']})
            assert failure.is_error
