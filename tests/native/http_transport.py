"""Real loopback CLI transport; never discover or use installed service state."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .harness import observe


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gcode(job):
    plate = job['plates'][0]
    path = Path(plate['gcode_path'])
    assert path.is_file(), 'Native G-code output missing'
    assert digest(path) == plate['sha256']
    observed = observe(path.read_text())
    assert observed['moves'], 'Native output contains no linear extrusion'
    return observed


async def rejected(session, tool_name, **arguments):
    response = await session.call_tool(tool_name, arguments)
    assert response.model_dump(by_alias=True).get('isError', False) is True, tool_name


class Calls:
    def __init__(self, session):
        self.session = session
        self.names = set()

    async def __call__(self, session, tool_name, **arguments):
        assert session is self.session
        response = await session.call_tool(tool_name, arguments)
        assert not response.model_dump(by_alias=True).get('isError', False), (tool_name, response.content)
        self.names.add(tool_name)  # Positive responses only count toward coverage.
        return json.loads(next(block.text for block in response.content if block.type == 'text'))


@asynccontextmanager
async def server_session(engine, scratch):
    """Bound startup and reap the entire dedicated process group on every path."""
    manifest = scratch / 'engine.json'
    manifest.write_text(json.dumps(engine.manifest))
    token = secrets.token_urlsafe(32)
    token_file = scratch / 'token'
    token_file.write_text(token)
    token_file.chmod(0o600)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    # Never fall back to another service if the reservation races.
    assert port != 49383
    base = f'http://127.0.0.1:{port}'
    root = Path(__file__).resolve().parents[2]
    with (scratch / 'server.log').open('wb') as log:
        process = subprocess.Popen([
            sys.executable, '-m', 'snapmaker_slicer_mcp.cli', '--engine', str(manifest),
            '--artifacts', str(scratch / 'artifacts'), '--timeout', '120',
            'serve', '--no-update-check', '--transport', 'streamable-http',
            '--host', '127.0.0.1', '--port', str(port), '--auth-token-file', str(token_file),
            '--allowed-host', f'127.0.0.1:{port}',
            '--allowed-origin', 'https://native-regression.example',
        ], cwd=root, stdout=log, stderr=log, start_new_session=True)
        try:
            async with httpx2.AsyncClient(timeout=150, trust_env=False,
                                         headers={'Authorization': 'Bearer ' + token}) as http:
                deadline = time.monotonic() + 30
                while True:
                    assert process.poll() is None, 'Dedicated server exited during startup'
                    try:
                        response = await http.get(base + '/health', timeout=1)
                        if response.status_code == 200:
                            break
                    except httpx2.TransportError:
                        pass
                    assert time.monotonic() < deadline, 'Dedicated server startup timed out'
                    await asyncio.sleep(.1)
                assert (await http.get(base + '/api/v1/engine', headers={'Authorization': ''})).status_code == 401
                assert (await http.get(base + '/health', headers={'Host': 'invalid.example'})).status_code == 421
                assert (await http.get(base + '/health', headers={'Origin': 'https://invalid.example'})).status_code == 403
                async with streamable_http_client(base + '/mcp', http_client=http) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=150) as session:
                        await session.initialize()
                        yield session
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # The group may exit between wait timeout and signal.
                process.wait(timeout=10)
            # Kill native descendants even if the server already exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
