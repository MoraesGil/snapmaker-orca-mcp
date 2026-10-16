"""REST adapters over the same native slicer used by MCP."""
import asyncio
import hashlib
import io
import itertools
import json
import re
import tempfile
import zipfile
from functools import wraps
from importlib.metadata import version

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

PREFIX = '/api/v1'
PRIVATE = {'Cache-Control': 'private, no-store'}


class SliceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    input_3mf: str = Field(min_length=1)
    plate: int = Field(default=0, ge=0)
    force: bool = False
    allow_newer_file: bool = True


def _fail(status, code, message):
    raise HTTPException(status, {'code': code, 'message': message})


def _endpoint(function):
    @wraps(function)
    async def wrapped(request):
        try:
            return await function(request)
        except HTTPException as error:
            return JSONResponse({'error': error.detail}, status_code=error.status_code, headers=PRIVATE)
        except FileNotFoundError as error:
            return JSONResponse({'error': {'code': 'not_found', 'message': str(error)}}, 404, headers=PRIVATE)
        except (ValueError, zipfile.BadZipFile, IsADirectoryError) as error:
            return JSONResponse({'error': {'code': 'invalid_project', 'message': str(error)}}, 400, headers=PRIVATE)
        except TimeoutError as error:
            return JSONResponse({'error': {'code': 'native_timeout', 'message': str(error)}}, 504, headers=PRIVATE)
        except RuntimeError as error:
            return JSONResponse({'error': {'code': 'native_failed', 'message': str(error)}}, 502, headers=PRIVATE)
        except OSError as error:
            return JSONResponse({'error': {'code': 'service_io', 'message': str(error)}}, 503, headers=PRIVATE)
    return wrapped


def _job(slicer, job_id):
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        _fail(400, 'invalid_job', 'Job ID must contain 32 lowercase hexadecimal characters')
    path = slicer.root / 'runs' / job_id / 'result.json'
    if path.resolve() != path:
        _fail(409, 'artifact_conflict', 'Job paths must not contain symlinks')
    if not path.is_file():
        _fail(404, 'not_found', 'Completed job is unavailable')
    try:
        return slicer.job(job_id)
    except (OSError, ValueError, KeyError, TypeError):
        _fail(409, 'artifact_conflict', 'Stored job manifest is invalid')


def _integer(value, name):
    if not re.fullmatch(r'[1-9][0-9]*', value):
        _fail(400, 'invalid_range', f'{name} must be a positive integer')
    try:
        return int(value)
    except ValueError:
        _fail(400, 'invalid_range', f'{name} is too large')


def _artifact(slicer, job_id, kind, plate=None):
    job = _job(slicer, job_id)
    if kind == 'gcode':
        record = next((p for p in job['plates'] if p['plate'] == plate), None)
        if record is None:
            _fail(404, 'not_found', 'Plate is not part of this job')
        path, expected = slicer.root / 'runs' / job_id / f'plate_{plate}.gcode', record['sha256']
    elif kind == 'source':
        path, expected = slicer.root / 'runs' / job_id / 'source.3mf', job['source']['sha256']
    else:
        path, expected = slicer.root / 'runs' / job_id / 'props.json', job['props_sha256']
    snapshot = tempfile.TemporaryFile(dir=path.parent)
    try:
        digest = hashlib.sha256()
        try:
            if path.resolve() != path or not path.is_file():
                _fail(409, 'artifact_conflict', 'Retained artifact is missing or a symlink')
            source = path.open('rb')
        except OSError:
            _fail(409, 'artifact_conflict', 'Retained artifact is unavailable')
        with source:
            while True:
                try:
                    chunk = source.read(1024 * 1024)
                except OSError:
                    _fail(409, 'artifact_conflict', 'Retained artifact is unreadable')
                if not chunk:
                    break
                snapshot.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != expected:
            _fail(409, 'artifact_conflict', 'Retained artifact changed')
        size = snapshot.tell()
        snapshot.seek(0)
        return snapshot, expected, path.name, size
    except BaseException:
        snapshot.close()
        raise


class _SnapshotResponse(StreamingResponse):
    def __init__(self, snapshot, **kwargs):
        self.snapshot = snapshot
        super().__init__(iter(lambda: snapshot.read(64 * 1024), b''), **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.snapshot.close()


def _properties(snapshot):
    with snapshot:
        contents = snapshot.read()
        try:
            json.loads(contents)
        except (ValueError, UnicodeDecodeError):
            _fail(409, 'artifact_conflict', 'Stored properties are invalid')
        return contents


def _lines(snapshot, job_id, plate, start, count):
    with io.TextIOWrapper(snapshot, encoding='utf-8', errors='replace') as stream:
        lines = [line.rstrip('\r\n') for line in itertools.islice(stream, start - 1, start - 1 + count)]
    return {'job_id': job_id, 'plate': plate, 'start_line': start, 'lines': lines}


def _linked(job):
    base = f'{PREFIX}/jobs/{job["job_id"]}'
    return {**job, 'links': {
        'self': base, 'props': base + '/props', 'source_3mf': base + '/source',
        'plates': {str(p['plate']): {'gcode': f'{base}/plates/{p["plate"]}/gcode',
                                   'lines': f'{base}/plates/{p["plate"]}/lines'} for p in job['plates']}}}


def _openapi():
    def response(description, media='application/json', binary=False):
        schema = {'type': 'string', 'format': 'binary'} if binary else {'type': 'object'}
        return {'description': description, 'content': {media: {'schema': schema}}}

    errors = {str(code): response(label) for code, label in (
        (400, 'Invalid request or unsupported project'), (401, 'Bearer authentication required'),
        (403, 'Origin rejected'), (404, 'Input, completed job or plate unavailable'),
        (409, 'Artifact or bound engine conflict'), (421, 'Host rejected'),
        (422, 'Invalid JSON schema'), (502, 'Native process failed'),
        (503, 'Service filesystem unavailable'), (504, 'Native process timed out'))}
    job = {'name': 'job_id', 'in': 'path', 'required': True,
           'schema': {'type': 'string', 'pattern': '^[0-9a-f]{32}$'}}
    plate = {'name': 'plate', 'in': 'path', 'required': True,
             'schema': {'type': 'integer', 'minimum': 1}}
    paths = {}
    for path, summary, parameters, media, binary in (
        ('/engine', 'Report local bound engine identity', [], 'application/json', False),
        ('/openapi.json', 'Read this authenticated API description', [], 'application/json', False),
        ('/jobs/{job_id}', 'Read a completed slice manifest', [job], 'application/json', False),
        ('/jobs/{job_id}/props', 'Read verified native properties', [job], 'application/json', False),
        ('/jobs/{job_id}/source', 'Download the verified preserved source', [job], 'model/3mf', True),
        ('/jobs/{job_id}/plates/{plate}/gcode', 'Download verified G-code', [job, plate], 'text/x.gcode', True),
        ('/jobs/{job_id}/plates/{plate}/lines', 'Read bounded verified G-code lines', [job, plate,
            {'name': 'start', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'default': 1}},
            {'name': 'count', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'default': 200}}],
            'application/json', False)):
        paths[PREFIX + path] = {'get': {'summary': summary, 'parameters': parameters,
                                       'responses': {'200': response(summary, media, binary), **errors}}}
    paths[PREFIX + '/slices'] = {'post': {
        'summary': 'Slice a saved 3MF and wait for its completed native result',
        'description': 'Synchronous. Configure client timeout for native execution plus queue wait. Disconnection does not cancel native execution.',
        'requestBody': {'required': True, 'content': {'application/json': {
            'schema': {'$ref': '#/components/schemas/SliceRequest'}}}},
        'responses': {'200': response('Completed native result or verified cache hit'), **errors}}}
    return {'openapi': '3.1.0', 'info': {'title': 'Agentic Slicer — Snapmaker Orca REST',
                                      'version': version('snapmaker-slicer-mcp')},
            'servers': [{'url': '/'}], 'security': [{'bearerAuth': []}], 'paths': paths,
            'components': {'securitySchemes': {'bearerAuth': {'type': 'http', 'scheme': 'bearer'}},
                           'schemas': {'SliceRequest': SliceRequest.model_json_schema()}}}


def rest_routes(slicer):
    """Reuse the supplied Slicer, its lock and artifacts; create no backend."""
    async def verify_engine():
        try:
            await asyncio.to_thread(slicer.engine.verify)
        except ValueError as error:
            _fail(409, 'engine_conflict', str(error))

    async def engine_call(function, *, slicing=False, **kwargs):
        await verify_engine()
        try:
            if slicing:
                return await slicer.run_slice(function, **kwargs)
            return await asyncio.to_thread(function, **kwargs)
        except ValueError:
            # Native verification can detect a change after the initial check.
            await verify_engine()
            raise

    @_endpoint
    async def slice_saved(request):
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            _fail(400, 'invalid_json', 'Request body must contain valid JSON')
        try:
            options = SliceRequest.model_validate(payload)
        except ValidationError as error:
            return JSONResponse({'error': {'code': 'invalid_request',
                                'details': error.errors(include_input=False, include_url=False)}}, 422, headers=PRIVATE)
        job = await engine_call(slicer.slice_3mf, slicing=True, **options.model_dump())
        return JSONResponse(_linked(job), headers=PRIVATE)

    @_endpoint
    async def manifest(request):
        job = await asyncio.to_thread(_job, slicer, request.path_params['job_id'])
        return JSONResponse(_linked(job), headers=PRIVATE)

    def artifact_response(kind):
        @_endpoint
        async def download(request):
            plate = _integer(request.path_params['plate'], 'plate') if kind == 'gcode' else None
            snapshot, digest, filename, size = await asyncio.to_thread(
                _artifact, slicer, request.path_params['job_id'], kind, plate)
            headers = {**PRIVATE, 'ETag': '"' + digest + '"'}
            if kind == 'props':
                contents = await asyncio.to_thread(_properties, snapshot)
                return Response(contents, media_type='application/json', headers=headers)
            headers.update({'Content-Length': str(size), 'Content-Disposition': f'attachment; filename="{filename}"'})
            return _SnapshotResponse(snapshot, media_type='text/x.gcode' if kind == 'gcode' else 'model/3mf',
                                     headers=headers)
        return download

    @_endpoint
    async def lines(request):
        job_id = request.path_params['job_id']
        plate = _integer(request.path_params['plate'], 'plate')
        start = _integer(request.query_params.get('start', '1'), 'start')
        count = _integer(request.query_params.get('count', '200'), 'count')
        if count > 1000:
            _fail(400, 'invalid_range', 'count must be between 1 and 1000')
        snapshot, digest, _, _ = await asyncio.to_thread(_artifact, slicer, job_id, 'gcode', plate)
        value = await asyncio.to_thread(_lines, snapshot, job_id, plate, start, count)
        return JSONResponse(value, headers={**PRIVATE, 'ETag': '"' + digest + '"'})

    @_endpoint
    async def engine(request):
        return JSONResponse(await engine_call(slicer.engine.status), headers=PRIVATE)

    @_endpoint
    async def openapi(request):
        return JSONResponse(_openapi(), headers=PRIVATE)

    return [Route(PREFIX + '/slices', slice_saved, methods=['POST']),
            Route(PREFIX + '/engine', engine), Route(PREFIX + '/openapi.json', openapi),
            Route(PREFIX + '/jobs/{job_id}', manifest),
            Route(PREFIX + '/jobs/{job_id}/props', artifact_response('props')),
            Route(PREFIX + '/jobs/{job_id}/source', artifact_response('source')),
            Route(PREFIX + '/jobs/{job_id}/plates/{plate}/gcode', artifact_response('gcode')),
            Route(PREFIX + '/jobs/{job_id}/plates/{plate}/lines', lines)]
