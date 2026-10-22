import asyncio
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from test_http import TOKEN, make_app, result, session_at
from test_service import GCODE_1, GCODE_2, make_project


def client(app, **kwargs):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver',
                             headers={'Authorization': 'Bearer ' + TOKEN}, **kwargs)


async def test_rest_slices_saved_project_and_serves_verified_artifacts(tmp_path):
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    original = source.read_bytes()
    async with app.router.lifespan_context(app), client(app) as http:
        response = await http.post('/api/v1/slices', json={'input_3mf': str(source), 'plate': 0})
        assert response.status_code == 200, response.text
        job = response.json()
        assert job['status'] == 'ready' and job['cached'] is False
        assert [p['plate'] for p in job['plates']] == [1, 2]
        assert (await http.get(job['links']['self'])).json()['job_id'] == job['job_id']
        props = await http.get(job['links']['props'])
        assert props.status_code == 200 and props.json()['native_global']['layer_height'] == '0.2'
        assert hashlib.sha256(props.content).hexdigest() == job['props_sha256']
        assert props.headers['etag'] == '"' + job['props_sha256'] + '"'
        restored = await http.get(job['links']['source_3mf'])
        assert restored.content == original == source.read_bytes()
        for number, expected in [(1, GCODE_1), (2, GCODE_2)]:
            download = await http.get(job['links']['plates'][str(number)]['gcode'])
            assert download.status_code == 200 and download.text == expected
            assert download.headers['etag'] == '"' + hashlib.sha256(download.content).hexdigest() + '"'
            assert 'no-store' in download.headers['cache-control']
            assert 'attachment' in download.headers['content-disposition']
        lines = await http.get(job['links']['plates']['1']['lines'], params={'start': 2, 'count': 1})
        assert lines.json()['lines'] == ['G1 X1 Y2 Z0.2 E0.3 F1200']
        assert len(list((slicer.root / 'runs').iterdir())) == 1


async def test_rest_and_mcp_reuse_the_same_verified_job_cache(tmp_path):
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    async with app.router.lifespan_context(app), client(app) as http:
        response = await http.post('/api/v1/slices', json={'input_3mf': str(source), 'plate': 1})
        assert response.status_code == 200
        job = response.json()
        slicer.engine.executable.with_suffix('.mode').write_text('fail')
        async with session_at('http://testserver/mcp', httpx.ASGITransport(app=app)) as mcp:
            reused = result(await mcp.call_tool('slice_3mf', {'input_3mf': str(source), 'plate': 1}))
            assert reused['cached'] is True and reused['job_id'] == job['job_id']
            resource = await mcp.read_resource(reused['props_uri'])
            assert json.loads(resource.contents[0].text)['native_global']['layer_height'] == '0.2'
            forced = await mcp.call_tool('slice_3mf', {'input_3mf': str(source), 'plate': 1, 'force': True})
            assert forced.isError


async def test_slow_rest_slice_keeps_mcp_reads_and_health_available(tmp_path, monkeypatch):
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    started, release = threading.Event(), threading.Event()
    native = slicer.slice_3mf

    def slow(*args, **kwargs):
        started.set()
        if not release.wait(5):
            raise TimeoutError('Test did not release the slice')
        return native(*args, **kwargs)

    monkeypatch.setattr(slicer, 'slice_3mf', slow)
    async with app.router.lifespan_context(app), client(app) as http:
        async with session_at('http://testserver/mcp', httpx.ASGITransport(app=app)) as mcp:
            pending = asyncio.create_task(http.post('/api/v1/slices', json={'input_3mf': str(source)}))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                assert (await asyncio.wait_for(http.get('/health'), 1)).status_code == 200
                status = result(await asyncio.wait_for(mcp.call_tool('engine_status', {'check_updates': False}), 1))
                assert status['source_commit'] == '1' * 40
                assert len((await mcp.list_tools()).tools) == 34
            finally:
                release.set()
                response = await pending
            assert response.status_code == 200


async def test_queued_rest_and_mcp_slices_do_not_exhaust_the_read_executor(tmp_path, monkeypatch):
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    started, release = threading.Event(), threading.Event()
    native = slicer.slice_3mf

    def slow(*args, **kwargs):
        started.set()
        if not release.wait(5):
            raise TimeoutError('Test did not release the slice')
        return native(*args, **kwargs)

    monkeypatch.setattr(slicer, 'slice_3mf', slow)
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=2))
    async with app.router.lifespan_context(app), client(app) as http:
        async with session_at('http://testserver/mcp', httpx.ASGITransport(app=app)) as mcp:
            pending = [asyncio.create_task(http.post('/api/v1/slices', json={'input_3mf': str(source)})),
                       asyncio.create_task(mcp.call_tool('slice_3mf', {'input_3mf': str(source)})),
                       asyncio.create_task(http.post('/api/v1/slices', json={'input_3mf': str(source)}))]
            try:
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.01)
                assert started.is_set()
                await asyncio.sleep(0.05)
                assert (await asyncio.wait_for(http.get('/health'), 1)).status_code == 200
                status = result(await asyncio.wait_for(mcp.call_tool('engine_status', {'check_updates': False}), 1))
                assert status['source_commit'] == '1' * 40
            finally:
                release.set()
                await asyncio.gather(*pending)


@pytest.mark.parametrize('fails', [False, True])
async def test_cancelled_caller_does_not_admit_another_slice_until_native_completion(tmp_path, caplog, fails):
    slicer, _ = make_app(tmp_path)
    started, release, second_started = threading.Event(), threading.Event(), threading.Event()

    def first_native():
        started.set()
        assert release.wait(5)
        if fails:
            raise RuntimeError('Native failure after caller disconnected')
        return 'first'

    def second_native():
        second_started.set()
        return 'second'

    first = asyncio.create_task(slicer.run_slice(first_native))
    assert await asyncio.to_thread(started.wait, 2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(slicer.run_slice(second_native))
    try:
        await asyncio.sleep(0.05)
        assert not second_started.is_set()
    finally:
        release.set()
        assert await asyncio.wait_for(second, 2) == 'second'
    assert 'Task exception was never retrieved' not in caplog.text


@pytest.mark.parametrize('kind', ['source', 'gcode', 'props', 'lines'])
async def test_response_uses_the_same_artifact_snapshot_that_was_verified(tmp_path, monkeypatch, kind):
    from snapmaker_slicer_mcp import rest
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    async with app.router.lifespan_context(app), client(app) as http:
        job = (await http.post('/api/v1/slices', json={'input_3mf': str(source), 'plate': 1})).json()
        filename = {'source': 'source.3mf', 'gcode': 'plate_1.gcode',
                    'props': 'props.json', 'lines': 'plate_1.gcode'}[kind]
        artifact = slicer.root / 'runs' / job['job_id'] / filename
        verified = artifact.read_bytes()
        original = rest._artifact

        def modified_after_verification(*args, **kwargs):
            value = original(*args, **kwargs)
            artifact.write_bytes(b'{"changed":true}\n')
            return value

        monkeypatch.setattr(rest, '_artifact', modified_after_verification)
        if kind in ('gcode', 'lines'):
            url = job['links']['plates']['1'][kind]
        else:
            url = job['links']['source_3mf' if kind == 'source' else 'props']
        response = await http.get(url)
        assert response.status_code == 200
        if kind == 'lines':
            assert response.json()['lines'] == verified.decode().splitlines()
        else:
            assert response.content == verified
        assert response.headers['etag'] == '"' + hashlib.sha256(verified).hexdigest() + '"'


async def test_snapshot_storage_failure_is_service_unavailable_not_artifact_corruption(tmp_path, monkeypatch):
    from snapmaker_slicer_mcp import rest
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    async with app.router.lifespan_context(app), client(app) as http:
        job = (await http.post('/api/v1/slices', json={'input_3mf': str(source), 'plate': 1})).json()
        original = rest.tempfile.TemporaryFile

        def disk_full(*args, **kwargs):
            snapshot = original(*args, **kwargs)
            def write(_):
                raise OSError(28, 'No space left on device')
            monkeypatch.setattr(snapshot, 'write', write)
            return snapshot

        monkeypatch.setattr(rest.tempfile, 'TemporaryFile', disk_full)
        response = await http.get(job['links']['plates']['1']['gcode'])
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'service_io'
        artifact = slicer.root / 'runs' / job['job_id'] / 'plate_1.gcode'
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == job['plates'][0]['sha256']


@pytest.mark.parametrize('extra,status', [
    ({'Authorization': ''}, 401), ({'Authorization': 'Bearer wrong'}, 401),
    ({'Host': 'evil.example'}, 421), ({'Origin': 'https://evil.example'}, 403),
])
async def test_rest_rejects_unauthorized_requests_before_native_work(tmp_path, extra, status):
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    async with app.router.lifespan_context(app), client(app) as http:
        response = await http.post('/api/v1/slices', headers=extra, json={'input_3mf': str(source)})
        assert response.status_code == status
        assert not list((slicer.root / 'runs').iterdir())


@pytest.mark.parametrize('payload', [
    {}, {'input_3mf': ''}, {'input_3mf': 7}, {'input_3mf': 'a', 'plate': -1},
    {'input_3mf': 'a', 'plate': True}, {'input_3mf': 'a', 'plate': 1.0},
    {'input_3mf': 'a', 'force': 'true'}, {'input_3mf': 'a', 'allow_newer_file': 'false'},
    {'input_3mf': 'a', 'token': 'do-not-echo-this-value'}, ['not', 'an', 'object'],
])
async def test_rest_rejects_invalid_schema_without_creating_a_job(tmp_path, payload):
    slicer, app = make_app(tmp_path)
    async with app.router.lifespan_context(app), client(app) as http:
        response = await http.post('/api/v1/slices', json=payload)
        assert response.status_code == 422
        assert 'do-not-echo-this-value' not in response.text
        assert not list((slicer.root / 'runs').iterdir())


async def test_rest_malformed_json_missing_source_and_unsupported_project(tmp_path):
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'scripted.3mf', post_process=['run-shell'])
    bad_zip = tmp_path / 'invalid.3mf'
    bad_zip.write_text('not a ZIP archive')
    async with app.router.lifespan_context(app), client(app) as http:
        assert (await http.post('/api/v1/slices', content=b'{broken')).status_code == 400
        assert (await http.post('/api/v1/slices', json={'input_3mf': str(tmp_path / 'missing.3mf')})).status_code == 404
        for path in [source, bad_zip]:
            assert (await http.post('/api/v1/slices', json={'input_3mf': str(path)})).status_code == 400
        assert not list((slicer.root / 'runs').iterdir())


async def test_rest_job_and_plate_lookup_and_bounded_lines(tmp_path):
    _, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    async with app.router.lifespan_context(app), client(app) as http:
        assert (await http.get('/api/v1/jobs/not-an-id')).status_code == 400
        assert (await http.get('/api/v1/jobs/' + 'f' * 32)).status_code == 404
        response = await http.post('/api/v1/slices', json={'input_3mf': str(source), 'plate': 1})
        assert response.status_code == 200
        job = response.json()
        prefix = '/api/v1/jobs/' + job['job_id'] + '/plates/'
        for plate, expected in [('0', 400), ('a', 400), ('999', 404)]:
            assert (await http.get(prefix + plate + '/gcode')).status_code == expected
        for params in [{'start': 0}, {'count': 0}, {'count': 1001}, {'start': 'x'}]:
            assert (await http.get(job['links']['plates']['1']['lines'], params=params)).status_code == 400


@pytest.mark.parametrize('artifact,link', [('plate_1.gcode', 'gcode'), ('props.json', 'props'), ('source.3mf', 'source_3mf')])
async def test_rest_refuses_modified_artifacts_even_with_conditional_headers(tmp_path, artifact, link):
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    async with app.router.lifespan_context(app), client(app) as http:
        response = await http.post('/api/v1/slices', json={'input_3mf': str(source), 'plate': 1})
        assert response.status_code == 200
        job = response.json()
        url = job['links']['plates']['1']['gcode'] if link == 'gcode' else job['links'][link]
        previous = await http.get(url)
        (slicer.root / 'runs' / job['job_id'] / artifact).write_bytes(b'corrupted')
        corrupted = await http.get(url, headers={'If-None-Match': previous.headers['etag']})
        assert corrupted.status_code == 409
        assert corrupted.content != b'corrupted'


async def test_rest_uses_canonical_download_paths_and_rejects_symlinks(tmp_path):
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    async with app.router.lifespan_context(app), client(app) as http:
        response = await http.post('/api/v1/slices', json={'input_3mf': str(source), 'plate': 1})
        assert response.status_code == 200
        job = response.json()
        folder = slicer.root / 'runs' / job['job_id']
        record = json.loads((folder / 'result.json').read_text())
        outside = tmp_path / 'outside.gcode'
        outside.write_text(GCODE_1)
        record['plates'][0]['gcode_path'] = str(outside)
        (folder / 'result.json').write_text(json.dumps(record))
        url = job['links']['plates']['1']['gcode']
        assert (await http.get(url)).text == GCODE_1
        (folder / 'plate_1.gcode').unlink()
        (folder / 'plate_1.gcode').symlink_to(outside)
        assert (await http.get(url)).status_code == 409


@pytest.mark.parametrize('missing', [False, True])
async def test_rest_detects_missing_artifacts_or_invalid_manifest(tmp_path, missing):
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    async with app.router.lifespan_context(app), client(app) as http:
        response = await http.post('/api/v1/slices', json={'input_3mf': str(source), 'plate': 1})
        assert response.status_code == 200
        job = response.json()
        folder = slicer.root / 'runs' / job['job_id']
        if missing:
            (folder / 'plate_1.gcode').unlink()
        else:
            (folder / 'result.json').write_text('{broken')
        assert (await http.get(job['links']['plates']['1']['gcode'])).status_code == 409


@pytest.mark.parametrize('mode,expected', [('fail', 502), ('sleep', 504)])
async def test_rest_native_failure_and_timeout_keep_diagnostics(tmp_path, mode, expected):
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    slicer.engine.executable.with_suffix('.mode').write_text(mode)
    slicer.timeout = 0.15 if mode == 'sleep' else 5
    async with app.router.lifespan_context(app), client(app) as http:
        response = await http.post('/api/v1/slices', json={'input_3mf': str(source)})
        assert response.status_code == expected
        assert 'diagnostics' in response.text.lower()
        assert len(list((slicer.root / 'runs').glob('*/failure.json'))) == 1
        assert not list((slicer.root / 'cache').iterdir())


async def test_rest_exposes_authenticated_openapi_and_local_engine_identity(tmp_path):
    _, app = make_app(tmp_path)
    async with app.router.lifespan_context(app), client(app) as http:
        denied = await http.get('/api/v1/openapi.json', headers={'Authorization': ''})
        assert denied.status_code == 401
        response = await http.get('/api/v1/openapi.json')
        assert response.status_code == 200
        schema = response.json()
        assert schema['openapi'].startswith('3.')
        assert '/api/v1/slices' in schema['paths']
        assert schema['components']['schemas']['SliceRequest']['additionalProperties'] is False
        assert schema['security'] == [{'bearerAuth': []}]
        status = await http.get('/api/v1/engine')
        assert status.status_code == 200 and status.json()['source_commit'] == '1' * 40


@pytest.mark.parametrize('moment', ['before', 'during'])
async def test_rest_rejects_changed_engine_assets_as_conflict(tmp_path, monkeypatch, moment):
    slicer, app = make_app(tmp_path)
    source = make_project(tmp_path / 'input.3mf')
    native = slicer.slice_3mf

    def change_engine():
        with slicer.engine.executable.open('a') as stream:
            stream.write('\n# external change\n')

    if moment == 'before':
        change_engine()
    else:
        def changed(*args, **kwargs):
            change_engine()
            return native(*args, **kwargs)
        monkeypatch.setattr(slicer, 'slice_3mf', changed)
    async with app.router.lifespan_context(app), client(app) as http:
        response = await http.post('/api/v1/slices', json={'input_3mf': str(source)})
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'engine_conflict'
        assert not list((slicer.root / 'runs').iterdir())
