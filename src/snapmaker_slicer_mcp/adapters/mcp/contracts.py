"""Preserve the published SDK-v1 wire contract on standalone FastMCP.

Schemas come from typed callables; validation and execution share one adapter.
FastMCP's presentation metadata and output-schema inference are deliberately
omitted because existing consumers never received those optional fields.
"""
from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import ParamSpec, cast
from typing_extensions import TypeForm
from pydantic_core import InitErrorDetails

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError, ResourceError, ToolError
from fastmcp.resources import ResourceResult
from fastmcp.resources.template import FunctionResourceTemplate
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool, ToolResult
from mcp import types
from mcp.shared.exceptions import MCPError
from pydantic import Field, TypeAdapter, ValidationError
from pydantic.json_schema import SkipJsonSchema

from ...domain.types.json import JsonObject, JsonValue, json_object

ToolValue = JsonObject | None
P = ParamSpec('P')
R = ParamSpec('R')


class ContractTool(Tool):
    invoke: SkipJsonSchema[Callable[[JsonObject], Awaitable[ToolValue]]] = Field(exclude=True)

    def to_mcp_tool(self, **overrides: JsonValue) -> types.Tool:
        return types.Tool(name=self.name, description=self.description, input_schema=self.parameters)

    async def run(self, arguments: JsonObject) -> ToolResult:
        try:
            value = await self.invoke(arguments)
        except Exception as error:
            raise ToolError(f'Error executing tool {self.name}: {error}') from error
        if value is None:
            return ToolResult(content=[])
        return ToolResult(content=[types.TextContent(type='text', text=json.dumps(value, indent=2, ensure_ascii=False))])


class ContractTemplate(FunctionResourceTemplate):
    def to_mcp_template(self, **overrides: JsonValue) -> types.ResourceTemplate:
        return types.ResourceTemplate(name=self.name, uri_template=self.uri_template,
                                      description=self.description, mime_type=self.mime_type)


class LegacyErrors(Middleware):
    async def on_call_tool(self, context: MiddlewareContext[types.CallToolRequestParams],
                           call_next: CallNext[types.CallToolRequestParams, ToolResult]) -> ToolResult:
        try:
            return await call_next(context)
        except NotFoundError:
            return ToolResult(content=[types.TextContent(type='text', text=f'Unknown tool: {context.message.name}')],
                              is_error=True)


    async def on_read_resource(self, context: MiddlewareContext[types.ReadResourceRequestParams],
                               call_next: CallNext[types.ReadResourceRequestParams, ResourceResult]) -> ResourceResult:
        try:
            return await call_next(context)
        except NotFoundError as error:
            raise MCPError(0, f'Unknown resource: {context.message.uri}') from error
        except ResourceError as error:
            # SDK-v1 wrapped template failures at creation and manager boundaries.
            # Unwrap framework envelopes only, retaining application error text.
            cause: BaseException = error
            while isinstance(cause, ResourceError) and cause.__cause__ is not None:
                cause = cause.__cause__
            raise MCPError(0, 'Error creating resource from template: '
                           f'Error creating resource from template: {cause}') from error


def register_tool(server: FastMCP[None]) -> Callable[[Callable[P, ToolValue | Awaitable[ToolValue]]], Callable[P, ToolValue | Awaitable[ToolValue]]]:
    def decorate(function: Callable[P, ToolValue | Awaitable[ToolValue]]) -> Callable[P, ToolValue | Awaitable[ToolValue]]:
        name = function.__name__
        # Pydantic accepts function call schemas at runtime, although its
        # TypeForm-only constructor annotation cannot express that documented API.
        adapter: TypeAdapter[ToolValue | Awaitable[ToolValue]] = TypeAdapter(cast(TypeForm[ToolValue | Awaitable[ToolValue]], function))
        schema = json_object(adapter.json_schema())
        schema.pop('additionalProperties', None)
        schema['title'] = f'{name}Arguments'

        properties = json_object(schema['properties'])

        async def invoke(arguments: JsonObject) -> ToolValue:
            # SDK-v1 ignored unknown keys and decoded JSON collections supplied
            # as strings by clients, while plain string arguments stayed literal.
            converted: JsonObject = {}
            for key, value in arguments.items():
                if key not in properties:
                    continue
                field = json_object(properties[key])
                if isinstance(value, str) and field.get('type') != 'string':
                    try:
                        parsed: JsonValue = json.loads(value)
                    except ValueError:
                        pass
                    else:
                        if not isinstance(parsed, (str, int, float)):
                            value = parsed
                converted[key] = value
            try:
                result = adapter.validate_python(converted)
            except ValidationError as error:
                details: list[InitErrorDetails] = []
                for detail in error.errors(include_url=False):
                    item: InitErrorDetails = {'type': 'missing' if detail['type'] == 'missing_argument' else detail['type'],
                                             'loc': detail['loc'], 'input': detail.get('input')}
                    if 'ctx' in detail:
                        item['ctx'] = detail['ctx']
                    details.append(item)
                raise ValidationError.from_exception_data(f'{name}Arguments', details) from error
            if inspect.isawaitable(result):
                return await result
            return result

        server.add_tool(ContractTool(name=name, description=inspect.cleandoc(function.__doc__ or '') + ('\n' if '\n' in (function.__doc__ or '') else ''),
                                     parameters=schema, invoke=invoke))
        return function
    return decorate


def register_resource(server: FastMCP[None], uri: str) -> Callable[[Callable[R, str]], Callable[R, str]]:
    def decorate(function: Callable[R, str]) -> Callable[R, str]:
        server.add_template(ContractTemplate.from_function(function, uri_template=uri, description=''))
        return function
    return decorate
