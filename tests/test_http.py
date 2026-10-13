import asyncio
import json
import socket
import subprocess
import threading
import time
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from test_editor import project
from test_protocol import command
from test_service import boundary


TOKEN = 'shared-test-token'


def make_app(tmp_path, **options):
    from snapmaker_slicer_mcp.http_server import build_http_app
    slicer, _ = boundary(tmp_path)
    token_file = tmp_path / 'token'
    token_file.write_text(TOKEN + '\n')
    app = build_http_app(slicer, auth_token_file=token_file,
                         allowed_hosts=['testserver'], allowed_origins=['https://trusted.example'], **options)
    return slicer, app


@asynccontextmanager
async def session_at(url, transport=None, origin=None):
    headers = {'Authorization': 'Bearer ' + TOKEN}
    if origin:
        headers['Origin'] = origin
    async with httpx.AsyncClient(transport=transport, headers=headers, timeout=10) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def result(call):
    assert not call.isError, call.content
    return call.structuredContent or json.loads(call.content[0].text)


@pytest.mark.parametrize('authorization', [None, '', 'Basic ' + TOKEN, 'Bearer wrong', 'Bearer '])
async def test_http_rejects_unauthorized_calls_before_project_operations(tmp_path, authorization, caplog):
    slicer, app = make_app(tmp_path)
    source = project(tmp_path / 'input.3mf')
    headers = {} if authorization is None else {'Authorization': authorization}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            response = await client.post('/mcp', headers=headers, json={
                'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': 'load_model', 'arguments': {'path': str(source)}},
            })
    assert response.status_code == 401
    assert response.headers['www-authenticate'].startswith('Bearer')
    assert not list((slicer.root / 'projects').iterdir())
    assert TOKEN not in response.text + caplog.text


@pytest.mark.parametrize('headers,status', [
    ({'Host': 'evil.example'}, 421),
    ({'Host': 'testserver.evil.example'}, 421),
    ({'Origin': 'https://evil.example'}, 403),
    ({'Origin': 'https://trusted.example.evil.example'}, 403),
    ({'Origin': 'null'}, 403),
])
async def test_http_enforces_exact_host_and_origin_even_with_valid_bearer(tmp_path, headers, status):
    slicer, app = make_app(tmp_path)
    source = project(tmp_path / 'input.3mf')
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            response = await client.post('/mcp', headers={'Authorization': 'Bearer ' + TOKEN, **headers}, json={
                'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': 'load_model', 'arguments': {'path': str(source)}},
            })
    assert response.status_code == status
    assert not list((slicer.root / 'projects').iterdir())


async def test_http_rejects_duplicate_security_headers(tmp_path):
    _, app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            for duplicate in ('Authorization', 'Host', 'Origin'):
                headers = [('Authorization', 'Bearer ' + TOKEN)]
                values = {'Authorization': 'Bearer ' + TOKEN, 'Host': 'testserver', 'Origin': 'https://trusted.example'}
                if duplicate != 'Authorization':
                    headers.append((duplicate, values[duplicate]))
                headers.append((duplicate, values[duplicate]))
                response = await client.post('/mcp', headers=headers, json={})
                assert response.status_code in (401, 403, 421)


@pytest.mark.parametrize('contents', [None, b'', b' \n\t', b'not a token', b'\xff'])
def test_http_requires_an_explicit_readable_nonempty_token_before_building_backend(tmp_path, contents):
    from snapmaker_slicer_mcp.http_server import build_http_app
    slicer, _ = boundary(tmp_path)
    token_file = tmp_path / 'token'
    if contents is not None:
        token_file.write_bytes(contents)
    with pytest.raises(ValueError, match='token'):
        build_http_app(slicer, auth_token_file=token_file, allowed_hosts=['testserver'])
    assert not (slicer.root / 'projects').exists()


@pytest.mark.parametrize('hosts,origins', [([], []), (['*'], []), (['testserver:*'], []),
                                         (['testserver'], ['*'])])
def test_http_requires_exact_explicit_allowlists(tmp_path, hosts, origins):
    from snapmaker_slicer_mcp.http_server import build_http_app
    slicer, _ = boundary(tmp_path)
    token_file = tmp_path / 'token'
    token_file.write_text(TOKEN)
    with pytest.raises(ValueError, match='Host|Origin|allowlist'):
        build_http_app(slicer, auth_token_file=token_file, allowed_hosts=hosts, allowed_origins=origins)


async def test_health_is_minimal_unauthenticated_and_still_checks_host(tmp_path):
    _, app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            response = await client.get('/health')
            assert response.status_code == 200
            assert response.json() == {'status': 'ok'}
            assert (await client.get('/health', headers={'Host': 'evil.example'})).status_code == 421


async def test_asgi_mcp_shares_project_state_and_keeps_reads_available_during_slice(tmp_path, monkeypatch):
    slicer, app = make_app(tmp_path)
    source = project(tmp_path / 'input.3mf')
    original = source.read_bytes()
    started, release = threading.Event(), threading.Event()
    native_slice = slicer.slice_3mf

    def slow_slice(*args, **kwargs):
        started.set()
        if not release.wait(5):
            raise TimeoutError('Test did not release the slice boundary')
        return native_slice(*args, **kwargs)

    monkeypatch.setattr(slicer, 'slice_3mf', slow_slice)
    async with app.router.lifespan_context(app):
        async with AsyncExitStack() as stack:
            one = await stack.enter_async_context(session_at('http://testserver/mcp', httpx.ASGITransport(app=app)))
            two = await stack.enter_async_context(session_at('http://testserver/mcp', httpx.ASGITransport(app=app)))
            loaded = result(await one.call_tool('load_model', {'path': str(source)}))
            pending = asyncio.create_task(one.call_tool('slice_project', {'project_id': loaded['project_id'], 'force': True}))
            assert await asyncio.to_thread(started.wait, 3)
            try:
                read = result(await asyncio.wait_for(two.call_tool('get_config', {'project_id': loaded['project_id']}), 1))
                assert read['sha256'] == loaded['sha256']
                edited = result(await two.call_tool('set_config', {'project_id': loaded['project_id'],
                                'changes': {'layer_height': 0.12}, 'expected_sha256': loaded['sha256']}))
                stale = await two.call_tool('set_config', {'project_id': loaded['project_id'],
                                        'changes': {'layer_height': 0.16}, 'expected_sha256': loaded['sha256']})
                assert stale.isError
            finally:
                release.set()
            sliced = result(await pending)
            assert sliced['source']['sha256'] == loaded['sha256'] != edited['sha256']
    assert source.read_bytes() == original


@pytest.mark.parametrize('host', ['127.0.0.1', '0.0.0.0'])
def test_cli_http_fails_without_explicit_token(tmp_path, host):
    _, argv = command(tmp_path)
    process = subprocess.run([*argv, 'serve', '--no-update-check', '--transport', 'streamable-http',
                              '--host', host], capture_output=True, text=True, timeout=5)
    assert process.returncode == 1
    assert 'token' in process.stderr.lower()


async def test_cli_tcp_serves_three_concurrent_mcp_clients_resources_and_recovery(tmp_path):
    slicer, argv = command(tmp_path)
    source = project(tmp_path / 'input.3mf')
    original = source.read_bytes()
    token_file = tmp_path / 'token'
    token_file.write_text(TOKEN + '\n')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    log_file = tmp_path / 'http.log'
    with log_file.open('w') as log:
        process = subprocess.Popen([*argv, 'serve', '--no-update-check', '--transport', 'streamable-http',
                                    '--host', '127.0.0.1', '--port', str(port), '--auth-token-file', str(token_file),
                                    '--allowed-host', f'127.0.0.1:{port}', '--allowed-host', 'testserver',
                                    '--allowed-origin', 'https://trusted.example'], stdout=log, stderr=log)
        try:
            async with httpx.AsyncClient(timeout=1) as http:
                deadline = time.monotonic() + 10
                while True:
                    assert process.poll() is None, log_file.read_text()
                    try:
                        if (await http.get(url + '/health')).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    assert time.monotonic() < deadline, 'HTTP service did not start'
                    await asyncio.sleep(0.05)
                assert (await http.post(url + '/mcp', json={})).status_code == 401
            barrier = asyncio.Barrier(3)
            shared = asyncio.get_running_loop().create_future()

            async def client(index):
                async with session_at(url + '/mcp', origin='https://trusted.example') as session:
                    assert len((await session.list_tools()).tools) == 33
                    assert len((await session.list_resource_templates()).resourceTemplates) == 3
                    await barrier.wait()
                    if index == 0:
                        loaded = result(await session.call_tool('load_model', {'path': str(source)}))
                        sliced = result(await session.call_tool('slice_project', {'project_id': loaded['project_id'], 'force': True}))
                        shared.set_result((loaded, sliced))
                    else:
                        loaded, sliced = await shared
                        state = result(await session.call_tool('get_status', {'project_id': loaded['project_id']}))
                        assert state['sha256'] == loaded['sha256']
                        if index == 1:
                            props = await session.read_resource(sliced['props_uri'])
                            assert json.loads(props.contents[0].text)['native_global']['layer_height'] == '0.2'
                            manifest = await session.read_resource(sliced['manifest_uri'])
                            assert json.loads(manifest.contents[0].text)['job_id'] == sliced['job_id']
                        else:
                            resource = await session.read_resource(sliced['plates'][0]['gcode_lines_uri'])
                            assert json.loads(resource.contents[0].text)['lines'][1] == 'G1 X1 Y2 Z0.2 E0.3 F1200'
                            recovered = result(await session.call_tool('recover_3mf', {'gcode_file': sliced['plates'][0]['gcode_path']}))
                            assert Path(recovered['path']).read_bytes() == original
            await asyncio.wait_for(asyncio.gather(*(client(i) for i in range(3))), 15)
        finally:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 5)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, 5)
    assert source.read_bytes() == original
    assert len(list((slicer.root / 'projects').iterdir())) == 1
    assert TOKEN not in log_file.read_text()
