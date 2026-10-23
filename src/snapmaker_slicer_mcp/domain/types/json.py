"""Explicit recursive JSON values, validated at dynamic-data boundaries."""
from __future__ import annotations

from typing import TypeAlias, TypeGuard
from typing_extensions import TypeAliasType

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue = TypeAliasType('JsonValue', 'str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]')
JsonObject: TypeAlias = dict[str, JsonValue]


def is_json_value(value: object) -> TypeGuard[JsonValue]:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and is_json_value(item) for key, item in value.items())
    return False


def json_object(value: object, label: str = "JSON value") -> JsonObject:
    if not is_json_value(value) or not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value
