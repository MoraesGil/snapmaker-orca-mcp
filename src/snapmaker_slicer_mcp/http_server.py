"""One authenticated Streamable HTTP application over the existing MCP backend."""
from pathlib import Path
from secrets import compare_digest

from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from .server import build_server
from .rest import rest_routes


def _token_from_file(path):
    if path is None:
        raise ValueError('Streamable HTTP requires --auth-token-file')
    try:
        token = Path(path).read_bytes().strip()
    except (OSError, TypeError, ValueError):
        raise ValueError('Cannot read the HTTP authentication token file') from None
    if not token or any(byte < 33 or byte > 126 for byte in token):
        raise ValueError('HTTP token file must contain one nonempty ASCII bearer token')
    return token


def _allowlist(values, kind, required=False):
    values = tuple(values or ())
    if required and not values:
        raise ValueError(f'{kind} allowlist must not be empty')
    if any(not isinstance(value, str) or not value or '*' in value or
           any(char.isspace() for char in value) for value in values):
        raise ValueError(f'{kind} allowlist requires exact values without wildcards or whitespace')
    return values


class _AccessPolicy:
    def __init__(self, app, token, allowed_hosts, allowed_origins):
        self.app = app
        self.token = token
        self.hosts = allowed_hosts
        self.origins = allowed_origins

    async def __call__(self, scope, receive, send):
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


def build_http_app(slicer, preset_dirs=(), *, auth_token_file=None, allowed_hosts=(), allowed_origins=(), plugins=()):
    """Build once per process; HTTP clients share the same slicer and editor.

    Tokens are read at startup. Restart explicitly to rotate a token. Host values
    include their port when clients send it; Origin values include their scheme.
    """
    token = _token_from_file(auth_token_file)
    hosts = _allowlist(allowed_hosts, 'Host', required=True)
    origins = _allowlist(allowed_origins, 'Origin')
    mcp = build_server(slicer, preset_dirs, plugins=plugins)
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=list(hosts), allowed_origins=list(origins))

    @mcp.custom_route('/health', methods=['GET'])
    async def health(request):
        return JSONResponse({'status': 'ok'})

    app = mcp.streamable_http_app()
    app.router.routes.extend(rest_routes(slicer))
    app.add_middleware(_AccessPolicy, token=token, allowed_hosts=hosts, allowed_origins=origins)
    return app
