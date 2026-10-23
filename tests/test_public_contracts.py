"""Freeze wire-visible consumer contracts; no SDK server internals are inspected.

Capture deliberately with PUBLIC_CONTRACT_CAPTURE=1 pytest tests/test_public_contracts.py.
Review the fixture diff before accepting any contract change.
"""
import copy
import json
import os
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from test_http import make_app, session_at
from test_protocol import command
from test_rest import client

FIXTURE = Path(__file__).parent / 'fixtures' / 'public-contracts.json'


def assert_contract(actual, expected):
    assert actual == expected, 'Public wire contract changed; review discovery/schema/error differences'


async def discovery(session):
    return {
        'tools': sorted([tool.model_dump(mode='json', by_alias=True, exclude_none=True)
                         for tool in (await session.list_tools()).tools], key=lambda t: t['name']),
        'resources': [resource.model_dump(mode='json', by_alias=True, exclude_none=True)
                      for resource in (await session.list_resources()).resources],
        'resource_templates': sorted([
            template.model_dump(mode='json', by_alias=True, exclude_none=True)
            for template in (await session.list_resource_templates()).resourceTemplates],
            key=lambda t: t['uriTemplate']),
    }


async def error_contract(session):
    errors = {}
    for label, name, arguments in [
        ('invalid_plate', 'slice_3mf', {'input_3mf': 'unused.3mf', 'plate': -1}),
        ('unknown_project', 'get_status', {'project_id': 'missing'}),
        ('missing_argument', 'load_model', {}),
        ('unknown_tool', 'contract_unknown_tool', {}),
    ]:
        result = await session.call_tool(name, arguments)
        errors[label] = result.model_dump(mode='json', by_alias=True, exclude_none=True)
    return errors


async def capture(tmp_path):
    _, argv = command(tmp_path / 'stdio')
    params = StdioServerParameters(command=argv[0], args=[*argv[1:], 'serve', '--no-update-check'])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        stdio = await discovery(session)
        errors = await error_contract(session)
    _, app = make_app(tmp_path / 'http')
    async with app.router.lifespan_context(app):
        async with session_at('http://testserver/mcp', httpx.ASGITransport(app=app)) as session:
            http = await discovery(session)
            assert await error_contract(session) == errors
        async with client(app) as rest:
            schema = (await rest.get('/api/v1/openapi.json')).json()
            rest_errors = {}
            for label, method, url, kwargs in [
                ('invalid_schema', 'post', '/api/v1/slices', {'json': {}}),
                ('invalid_job', 'get', '/api/v1/jobs/not-an-id', {}),
                ('unknown_job', 'get', '/api/v1/jobs/' + 'f' * 32, {}),
                ('unauthorized', 'get', '/api/v1/engine', {'headers': {'Authorization': ''}}),
                ('wrong_host', 'get', '/api/v1/engine', {'headers': {'Host': 'evil.example'}}),
                ('wrong_origin', 'get', '/api/v1/engine', {'headers': {'Origin': 'https://evil.example'}}),
            ]:
                response = await rest.request(method, url, **kwargs)
                rest_errors[label] = {'status': response.status_code, 'body': response.json()}
    assert stdio == http
    assert len(stdio['tools']) == 34
    assert len(stdio['resource_templates']) == 3
    assert len(schema['paths']) == 8
    return {'mcp': stdio, 'mcp_errors': errors, 'rest_openapi': schema, 'rest_errors': rest_errors}


async def test_public_wire_contracts(tmp_path):
    actual = await capture(tmp_path)
    if os.environ.get('PUBLIC_CONTRACT_CAPTURE') == '1':
        FIXTURE.write_text(json.dumps(actual, indent=2, sort_keys=True) + '\n')
    assert_contract(actual, json.loads(FIXTURE.read_text()))


@pytest.mark.parametrize('mutation', ['tool_name', 'schema'])
def test_contract_rejects_tool_name_or_schema_drift(mutation):
    expected = json.loads(FIXTURE.read_text())
    changed = copy.deepcopy(expected)
    tool = changed['mcp']['tools'][0]
    if mutation == 'tool_name':
        tool['name'] += '_breaking_change'
    else:
        tool['inputSchema']['properties']['contract_drift'] = {'type': 'boolean', 'default': True}
    with pytest.raises(AssertionError, match='Public wire contract changed'):
        assert_contract(changed, expected)
