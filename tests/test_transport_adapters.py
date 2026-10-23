"""Transport migration requirements independent of SDK implementation details."""
from importlib.metadata import version

import pytest

from test_http import make_app, session_at
import httpx2 as httpx


def test_server_uses_standalone_fastmcp_v4(tmp_path):
    from snapmaker_slicer_mcp.server import build_server
    from test_service import boundary
    slicer, _ = boundary(tmp_path)
    server = build_server(slicer)
    assert type(server).__module__.startswith('fastmcp.')
    assert version('fastmcp').split('.')[0] == '4'
    assert version('mcp').split('.')[0] == '2'


@pytest.mark.parametrize('headers,status', [
    ([('Host', 'testserver'), ('Host', 'testserver')], 421),
    ([('Origin', 'https://trusted.example'), ('Origin', 'https://trusted.example')], 403),
    ([('Authorization', 'Bearer shared-test-token'), ('Authorization', 'Bearer shared-test-token')], 401),
])
async def test_duplicate_headers_fail_closed_on_rest_and_mcp(tmp_path, headers, status):
    _, app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            for path in ['/mcp', '/api/v1/engine']:
                response = await client.get(path, headers=headers)
                assert response.status_code == status


async def test_http_lifespan_restarts_cleanly_and_preserves_tool_coercion(tmp_path):
    _, app = make_app(tmp_path)
    for _ in range(2):
        async with app.router.lifespan_context(app):
            async with session_at('http://testserver/mcp', httpx.ASGITransport(app=app)) as session:
                result = await session.call_tool('slice_3mf', {'input_3mf': 'unused.3mf', 'plate': '-1'})
                assert result.is_error
                assert 'plate must be 0 (all) or a positive plate number' in result.content[0].text


async def test_legacy_json_encoded_collections_and_ignored_extra_arguments(tmp_path):
    import json
    from test_editor import project
    _, app = make_app(tmp_path)
    source = project(tmp_path / 'input.3mf')
    async with app.router.lifespan_context(app):
        async with session_at('http://testserver/mcp', httpx.ASGITransport(app=app)) as session:
            loaded = await session.call_tool('load_model', {'path': str(source), 'legacy_extra': True})
            assert not loaded.is_error, loaded.content
            state = json.loads(loaded.content[0].text)
            edited = await session.call_tool('set_config', {'project_id': state['project_id'],
                                                           'changes': '{"layer_height":0.12}'})
            assert not edited.is_error, edited.content
            config = await session.call_tool('get_config', {'project_id': state['project_id'],
                                                            'keys': '["layer_height"]'})
            assert not config.is_error, config.content
            assert json.loads(config.content[0].text)['config']['layer_height'] == '0.12'


async def test_unknown_setting_keeps_legacy_empty_success_content(tmp_path):
    _, app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        async with session_at('http://testserver/mcp', httpx.ASGITransport(app=app)) as session:
            result = await session.call_tool('describe_setting', {'key': 'contract_unknown_setting'})
            assert not result.is_error
            assert result.content == []
