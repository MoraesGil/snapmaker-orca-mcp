"""One authenticated Streamable HTTP application over the existing MCP backend."""
from pathlib import Path
from secrets import compare_digest

from collections.abc import Sequence

from fastmcp.server.http import StarletteWithLifespan
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from ...service import Slicer
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from ..mcp.server import build_server
from .rest import rest_routes


def _token_from_file(path: str | Path | None) -> bytes:
    if path is None:
        raise ValueError('Streamable HTTP requires --auth-token-file')
    try:
        token = Path(path).read_bytes().strip()
    except (OSError, TypeError, ValueError):
        raise ValueError('Cannot read the HTTP authentication token file') from None
    if not token or any(byte < 33 or byte > 126 for byte in token):
        raise ValueError('HTTP token file must contain one nonempty ASCII bearer token')
    return token


def _allowlist(values: Sequence[str], kind: str, required: bool = False) -> tuple[str, ...]:
    values = tuple(values or ())
    if required and not values:
        raise ValueError(f'{kind} allowlist must not be empty')
    if any(not isinstance(value, str) or not value or '*' in value or
           any(char.isspace() for char in value) for value in values):
        raise ValueError(f'{kind} allowlist requires exact values without wildcards or whitespace')
    return values


class _AccessPolicy:
    def __init__(self, app: ASGIApp, token: bytes, allowed_hosts: tuple[str, ...],
                 allowed_origins: tuple[str, ...]) -> None:
        self.app = app
        self.token = token
        self.hosts = allowed_hosts
        self.origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        hosts, origins = headers.getlist('host'), headers.getlist('origin')
        if len(hosts) != 1 or hosts[0] not in self.hosts:
            return await JSONResponse({'error': 'Invalid Host'}, status_code=421)(scope, receive, send)
        if len(origins) > 1 or origins and origins[0] not in self.origins:
            return await JSONResponse({'error': 'Invalid Origin'}, status_code=403)(scope, receive, send)
        if scope['path'] != '/health' or scope['method'] != 'GET':
            authorization = headers.getlist('authorization')
            scheme, _, supplied = authorization[0].partition(' ') if len(authorization) == 1 else ('', '', '')
            if scheme.lower() != 'bearer' or not compare_digest(supplied.encode('latin-1'), self.token):
                return await JSONResponse({'error': 'Unauthorized'}, status_code=401,
                                          headers={'WWW-Authenticate': 'Bearer'})(scope, receive, send)
        return await self.app(scope, receive, send)


def build_http_app(slicer: Slicer, preset_dirs: Sequence[Path] = (), *,
                   auth_token_file: str | Path | None = None, allowed_hosts: Sequence[str] = (),
                   allowed_origins: Sequence[str] = ()) -> StarletteWithLifespan:
    """Build once per process; HTTP clients share the same slicer and editor.

    Tokens are read at startup. Restart explicitly to rotate a token. Host values
    include their port when clients send it; Origin values include their scheme.
    """
    token = _token_from_file(auth_token_file)
    hosts = _allowlist(allowed_hosts, 'Host', required=True)
    origins = _allowlist(allowed_origins, 'Origin')
    mcp = build_server(slicer, preset_dirs)
    @mcp.custom_route('/health', methods=['GET'])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({'status': 'ok'})

    app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True,
                       allowed_hosts=list(hosts), allowed_origins=list(origins))
    app.router.routes.extend(rest_routes(slicer))
    app.add_middleware(_AccessPolicy, token=token, allowed_hosts=hosts, allowed_origins=origins)
    return app
