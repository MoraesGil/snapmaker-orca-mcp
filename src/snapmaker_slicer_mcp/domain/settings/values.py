"""Small validators for typed JSON records at configuration boundaries."""
from __future__ import annotations
from ..types.json import JsonValue, JsonObject, json_object


def text(value: JsonValue, label: str = 'value') -> str:
    if not isinstance(value, str):
        raise ValueError(f'{label} must be a string')
    return value


def array(value: JsonValue, label: str = 'value') -> list[JsonValue]:
    if not isinstance(value, list):
        raise ValueError(f'{label} must be an array')
    return value


def strings(value: JsonValue, label: str = 'value') -> list[str]:
    return [text(item, label) for item in array(value, label)]


def integer(value: JsonValue, label: str = 'value') -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f'{label} must be an integer')
    return value


def record(value: JsonValue) -> JsonObject:
    return json_object(value)
