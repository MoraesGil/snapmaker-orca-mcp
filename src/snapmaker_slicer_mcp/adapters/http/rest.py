"""REST adapters over the same native slicer used by MCP."""
import asyncio
import hashlib
import io
import json
import re
import tempfile
import zipfile
from collections.abc import Awaitable, Callable, Iterator, Mapping
from functools import wraps
from typing import BinaryIO, Literal, NoReturn, ParamSpec, TypeVar
from importlib.metadata import version

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

from ...service import Slicer
from ...domain.types.json import JsonObject, JsonValue, json_object
from ...application.contracts.artifacts import GcodeLines
from ...application.services.lines import bounded_lines
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

PREFIX = '/api/v1'
PRIVATE = {'Cache-Control': 'private, no-store'}
Endpoint = Callable[[Request], Awaitable[Response]]
ArtifactKind = Literal['gcode', 'source', 'props']
P = ParamSpec('P')
T = TypeVar('T')


class SliceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    input_3mf: str = Field(min_length=1)
    plate: int = Field(default=0, ge=0)
    force: bool = False
    allow_newer_file: bool = True


class _RequestFailure(Exception):
    def __init__(self, status: int, detail: JsonObject) -> None:
        super().__init__(detail)
        self.status_code = status
        self.detail = detail


def _fail(status: int, code: str, message: str) -> NoReturn:
    raise _RequestFailure(status, {'code': code, 'message': message})


def _endpoint(function: Endpoint) -> Endpoint:
    @wraps(function)
    async def wrapped(request: Request) -> Response:
        try:
            return await function(request)
        except _RequestFailure as error:
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


def _job(slicer: Slicer, job_id: str) -> JsonObject:
    if not re.fullmatch(r'[0-9a-f]{32}', job_id):
        _fail(400, 'invalid_job', 'Job ID must contain 32 lowercase hexadecimal characters')
    path = slicer.root / 'runs' / job_id / 'result.json'
    if path.resolve() != path:
        _fail(409, 'artifact_conflict', 'Job paths must not contain symlinks')
    if not path.is_file():
        _fail(404, 'not_found', 'Completed job is unavailable')
    try:
        return json_object(slicer.job(job_id))
    except (OSError, ValueError, KeyError, TypeError):
        _fail(409, 'artifact_conflict', 'Stored job manifest is invalid')


def _integer(value: str, name: str) -> int:
    if not re.fullmatch(r'[1-9][0-9]*', value):
        _fail(400, 'invalid_range', f'{name} must be a positive integer')
    try:
        return int(value)
    except ValueError:
        _fail(400, 'invalid_range', f'{name} is too large')



def _text(value: JsonValue) -> str:
    if not isinstance(value, str):
        _fail(409, 'artifact_conflict', 'Stored job manifest is invalid')
    return value


def _plates(job: JsonObject) -> list[JsonObject]:
    records = job['plates']
    if not isinstance(records, list):
        _fail(409, 'artifact_conflict', 'Stored job manifest is invalid')
    return [json_object(record) for record in records]


def _artifact(slicer: Slicer, job_id: str, kind: ArtifactKind,
              plate: int | None = None) -> tuple[BinaryIO, str, str, int]:
    job = _job(slicer, job_id)
    if kind == 'gcode':
        record = next((p for p in _plates(job) if p['plate'] == plate), None)
        if record is None:
            _fail(404, 'not_found', 'Plate is not part of this job')
        path, expected = slicer.root / 'runs' / job_id / f'plate_{plate}.gcode', _text(record['sha256'])
    elif kind == 'source':
        path, expected = slicer.root / 'runs' / job_id / 'source.3mf', _text(json_object(job['source'])['sha256'])
    else:
        path, expected = slicer.root / 'runs' / job_id / 'props.json', _text(job['props_sha256'])
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
    def __init__(self, snapshot: BinaryIO, *, media_type: str, headers: Mapping[str, str]) -> None:
        self.snapshot = snapshot
        super().__init__(self._chunks(), media_type=media_type, headers=headers)

    def _chunks(self) -> Iterator[bytes]:
        while chunk := self.snapshot.read(64 * 1024):
            yield chunk

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.snapshot.close()


def _properties(snapshot: BinaryIO) -> bytes:
    with snapshot:
        contents = snapshot.read()
        try:
            json.loads(contents)
        except (ValueError, UnicodeDecodeError):
            _fail(409, 'artifact_conflict', 'Stored properties are invalid')
        return contents


def _lines(snapshot: BinaryIO, job_id: str, plate: int, start: int, count: int) -> GcodeLines:
    with io.TextIOWrapper(snapshot, encoding='utf-8', errors='replace') as stream:
        return bounded_lines(stream, job_id, plate, start, count)


def _linked(job: JsonObject) -> JsonObject:
    base = f'{PREFIX}/jobs/{job["job_id"]}'
    return {**job, 'links': {
        'self': base, 'props': base + '/props', 'source_3mf': base + '/source',
        'plates': {str(p['plate']): {'gcode': f'{base}/plates/{p["plate"]}/gcode',
                                   'lines': f'{base}/plates/{p["plate"]}/lines'} for p in _plates(job)}}}


def _openapi() -> JsonObject:
    def response(description: str, media: str = 'application/json', binary: bool = False) -> JsonObject:
        schema: JsonObject = {'type': 'string', 'format': 'binary'} if binary else {'type': 'object'}
        return {'description': description, 'content': {media: {'schema': schema}}}

    errors = {str(code): response(label) for code, label in (
        (400, 'Invalid request or unsupported project'), (401, 'Bearer authentication required'),
        (403, 'Origin rejected'), (404, 'Input, completed job or plate unavailable'),
        (409, 'Artifact or bound engine conflict'), (421, 'Host rejected'),
        (422, 'Invalid JSON schema'), (502, 'Native process failed'),
        (503, 'Service filesystem unavailable'), (504, 'Native process timed out'))}
    job: JsonObject = {'name': 'job_id', 'in': 'path', 'required': True,
           'schema': {'type': 'string', 'pattern': '^[0-9a-f]{32}$'}}
    plate: JsonObject = {'name': 'plate', 'in': 'path', 'required': True,
             'schema': {'type': 'integer', 'minimum': 1}}
    paths: JsonObject = {}
    entries: list[tuple[str, str, list[JsonObject], str, bool]] = [

        ('/engine', 'Report local bound engine identity', [], 'application/json', False),
        ('/openapi.json', 'Read this authenticated API description', [], 'application/json', False),
        ('/jobs/{job_id}', 'Read a completed slice manifest', [job], 'application/json', False),
        ('/jobs/{job_id}/props', 'Read verified native properties', [job], 'application/json', False),
        ('/jobs/{job_id}/source', 'Download the verified preserved source', [job], 'model/3mf', True),
        ('/jobs/{job_id}/plates/{plate}/gcode', 'Download verified G-code', [job, plate], 'text/x.gcode', True),
        ('/jobs/{job_id}/plates/{plate}/lines', 'Read bounded verified G-code lines', [job, plate,
            {'name': 'start', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'default': 1}},
            {'name': 'count', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 1000, 'default': 200}}],
            'application/json', False)]
    for path, summary, parameters, media, binary in entries:
        paths[PREFIX + path] = {'get': {'summary': summary, 'parameters': [parameter for parameter in parameters],
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


def rest_routes(slicer: Slicer) -> list[Route]:
    """Reuse the supplied Slicer, its lock and artifacts; create no backend."""
    async def verify_engine() -> None:
        try:
            await asyncio.to_thread(slicer.engine.verify)
        except ValueError as error:
            _fail(409, 'engine_conflict', str(error))

    async def engine_call(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        await verify_engine()
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except ValueError:
            # Native verification can detect a change after the initial check.
            await verify_engine()
            raise

    @_endpoint
    async def slice_saved(request: Request) -> Response:
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            _fail(400, 'invalid_json', 'Request body must contain valid JSON')
        try:
            options = SliceRequest.model_validate(payload)
        except ValidationError as error:
            return JSONResponse({'error': {'code': 'invalid_request',
                                'details': error.errors(include_input=False, include_url=False)}}, 422, headers=PRIVATE)
        await verify_engine()
        try:
            job = await slicer.run_slice(slicer.slice_3mf, options.input_3mf, options.plate,
                                         options.force, options.allow_newer_file)
        except ValueError:
            await verify_engine()
            raise
        return JSONResponse(_linked(json_object(job)), headers=PRIVATE)

    @_endpoint
    async def manifest(request: Request) -> Response:
        job = await asyncio.to_thread(_job, slicer, _text(request.path_params['job_id']))
        return JSONResponse(_linked(json_object(job)), headers=PRIVATE)

    def artifact_response(kind: ArtifactKind) -> Endpoint:
        @_endpoint
        async def download(request: Request) -> Response:
            plate = _integer(_text(request.path_params['plate']), 'plate') if kind == 'gcode' else None
            snapshot, digest, filename, size = await asyncio.to_thread(
                _artifact, slicer, _text(request.path_params['job_id']), kind, plate)
            headers = {**PRIVATE, 'ETag': '"' + digest + '"'}
            if kind == 'props':
                contents = await asyncio.to_thread(_properties, snapshot)
                return Response(contents, media_type='application/json', headers=headers)
            headers.update({'Content-Length': str(size), 'Content-Disposition': f'attachment; filename="{filename}"'})
            return _SnapshotResponse(snapshot, media_type='text/x.gcode' if kind == 'gcode' else 'model/3mf',
                                     headers=headers)
        return download

    @_endpoint
    async def lines(request: Request) -> Response:
        job_id = _text(request.path_params['job_id'])
        plate = _integer(_text(request.path_params['plate']), 'plate')
        start = _integer(request.query_params.get('start', '1'), 'start')
        count = _integer(request.query_params.get('count', '200'), 'count')
        if count > 1000:
            _fail(400, 'invalid_range', 'count must be between 1 and 1000')
        snapshot, digest, _, _ = await asyncio.to_thread(_artifact, slicer, job_id, 'gcode', plate)
        value = await asyncio.to_thread(_lines, snapshot, job_id, plate, start, count)
        return JSONResponse(value, headers={**PRIVATE, 'ETag': '"' + digest + '"'})

    @_endpoint
    async def engine(request: Request) -> Response:
        return JSONResponse(await engine_call(slicer.engine.status), headers=PRIVATE)

    @_endpoint
    async def openapi(request: Request) -> Response:
        return JSONResponse(_openapi(), headers=PRIVATE)

    return [Route(PREFIX + '/slices', slice_saved, methods=['POST']),
            Route(PREFIX + '/engine', engine), Route(PREFIX + '/openapi.json', openapi),
            Route(PREFIX + '/jobs/{job_id}', manifest),
            Route(PREFIX + '/jobs/{job_id}/props', artifact_response('props')),
            Route(PREFIX + '/jobs/{job_id}/source', artifact_response('source')),
            Route(PREFIX + '/jobs/{job_id}/plates/{plate}/gcode', artifact_response('gcode')),
            Route(PREFIX + '/jobs/{job_id}/plates/{plate}/lines', lines)]
