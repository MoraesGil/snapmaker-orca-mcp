# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.30,<2", "httpx>=0.28,<1"]
# ///
"""Check both transports from an isolated SDK-v1 consumer using synthetic inputs.

Run with uv run --isolated scripts/check_sdk1_interop.py --server-python
.venv/bin/python --scratch .temp/sdk1-interop. The server environment needs the
project's test extra to build the existing synthetic native boundary. This is
wrapper interoperability evidence; it does not run or validate the real engine.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import socket
import subprocess
import tempfile
import time
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.client.streamable_http import streamable_http_client


async def check(session: ClientSession, fixture: dict, source: Path) -> None:
    await session.initialize()
    discovery = {
        'tools': sorted([tool.model_dump(mode='json', by_alias=True, exclude_none=True)
                         for tool in (await session.list_tools()).tools], key=lambda tool: tool['name']),
        'resources': [item.model_dump(mode='json', by_alias=True, exclude_none=True)
                      for item in (await session.list_resources()).resources],
        'resource_templates': sorted([item.model_dump(mode='json', by_alias=True, exclude_none=True)
                                      for item in (await session.list_resource_templates()).resourceTemplates],
                                     key=lambda item: item['uriTemplate']),
    }
    assert discovery == fixture['mcp'], 'SDK-v1 discovery changed'
    for label, name, arguments in [
        ('invalid_plate', 'slice_3mf', {'input_3mf': 'unused.3mf', 'plate': -1}),
        ('unknown_project', 'get_status', {'project_id': 'missing'}),
        ('missing_argument', 'load_model', {}),
        ('unknown_tool', 'contract_unknown_tool', {}),
    ]:
        result = await session.call_tool(name, arguments)
        assert result.model_dump(mode='json', by_alias=True, exclude_none=True) == fixture['mcp_errors'][label], label
    for uri, message in [
        ('slicer://unknown', 'Unknown resource: slicer://unknown'),
        ('slicer://jobs/missing/manifest',
         'Error creating resource from template: Error creating resource from template: Invalid generated job ID'),
    ]:
        try:
            await session.read_resource(uri)
        except McpError as error:
            assert error.error.code == 0 and error.error.message == message and error.error.data is None
        else:
            raise AssertionError('Expected legacy resource failure')
    unknown_setting = await session.call_tool('describe_setting', {'key': 'contract_unknown_setting'})
    assert unknown_setting.content == [] and not unknown_setting.isError
    loaded = await session.call_tool('load_model', {'path': str(source), 'ignored_legacy_key': True})
    assert not loaded.isError, loaded.content
    state = json.loads(loaded.content[0].text)
    edited = await session.call_tool('set_config', {'project_id': state['project_id'],
                                                   'changes': '{"layer_height":0.12}'})
    assert not edited.isError, edited.content
    sliced = await session.call_tool('slice_project', {'project_id': state['project_id'], 'plate': '1'})
    assert not sliced.isError, sliced.content
    job = json.loads(sliced.content[0].text)
    assert job['source']['sha256'] == json.loads(edited.content[0].text)['sha256']
    assert json.loads((await session.read_resource(job['manifest_uri'])).contents[0].text)['job_id'] == job['job_id']
    assert json.loads((await session.read_resource(job['props_uri'])).contents[0].text)['native_global']['layer_height'] == '0.2'
    lines = await session.read_resource(job['plates'][0]['gcode_lines_uri'])
    assert json.loads(lines.contents[0].text)['lines'][1] == 'G1 X1 Y2 Z0.2 E0.3 F1200'
    recovered = await session.call_tool('recover_3mf', {'gcode_file': job['plates'][0]['gcode_path']})
    assert not recovered.isError, recovered.content
    recovery = json.loads(recovered.content[0].text)
    assert Path(recovery['path']).read_bytes() == Path(json.loads(edited.content[0].text)['path']).read_bytes()


async def run(server_python: Path, scratch: Path) -> None:
    assert version('mcp').split('.')[0] == '1', 'Run this consumer with isolated mcp>=1.30,<2'
    repo = Path(__file__).resolve().parents[1]
    fixture = json.loads((repo / 'tests/fixtures/public-contracts.json').read_text())
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sdk1-', dir=scratch) as temporary:
        root = Path(temporary)
        preparation = '''
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_protocol import command
from test_editor import project
root=Path(sys.argv[1]);slicer,argv=command(root)
project(root/'input.3mf')
print(json.dumps(argv))
'''
        prepared = subprocess.run([str(server_python), '-c', preparation, str(root)], cwd=repo,
                                  capture_output=True, text=True, check=True, timeout=15)
        command: list[str] = json.loads(prepared.stdout)
        source = root / 'input.3mf'
        original = source.read_bytes()
        params = StdioServerParameters(command=command[0], args=[*command[1:], 'serve', '--no-update-check'])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await check(session, fixture, source)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        token = secrets.token_urlsafe(32)
        token_file = root / 'token'
        token_file.write_text(token)
        token_file.chmod(0o600)
        with (root / 'http.log').open('w') as log:
            process = subprocess.Popen([*command, 'serve', '--no-update-check', '--transport', 'streamable-http',
                                        '--port', str(port), '--auth-token-file', str(token_file)],
                                       cwd=repo, stdout=log, stderr=log)
            try:
                async with httpx.AsyncClient(timeout=2, headers={'Authorization': 'Bearer ' + token}) as http:
                    deadline = time.monotonic() + 15
                    while True:
                        assert process.poll() is None, 'Server exited before health check'
                        try:
                            if (await http.get(url + '/health')).status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        assert time.monotonic() < deadline, 'HTTP startup timed out'
                        await asyncio.sleep(0.05)
                    async with streamable_http_client(url + '/mcp', http_client=http) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await check(session, fixture, source)
                    schema = (await http.get(url + '/api/v1/openapi.json')).json()
                    assert schema == fixture['rest_openapi'], 'REST schema changed for SDK-v1 consumer'
                    assert (await http.get(url + '/api/v1/engine', headers={'Authorization': ''})).status_code == 401
                    assert (await http.get(url + '/health', headers={'Host': 'invalid.example'})).status_code == 421
                    assert (await http.get(url + '/health', headers={'Origin': 'https://invalid.example'})).status_code == 403
            finally:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, 5)
        assert source.read_bytes() == original, 'Synthetic canonical input was modified'
    print(json.dumps({'sdk': version('mcp'), 'transports': ['stdio', 'streamable-http'],
                      'tools': 34, 'resource_templates': 3, 'rest_paths': 8, 'status': 'passed'}))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-python', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args(argv)
    asyncio.run(run(args.server_python.absolute(), args.scratch.resolve()))


if __name__ == '__main__':
    main()
