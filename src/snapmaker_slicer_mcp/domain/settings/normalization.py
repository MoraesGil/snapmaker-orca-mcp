# Portions derived from Max Ellis, orcaslicer-mcp@ccfacb43c2fd5a23973bfdabce89c74b2f6933fe.
# SPDX-License-Identifier: AGPL-3.0-only
"""Pure native setting validation with explicitly supplied source metadata."""
from __future__ import annotations
import copy
import math
import re
from decimal import Decimal, InvalidOperation
from ..types.json import JsonObject, JsonValue, json_object, is_json_value
from .values import record, text, array, strings

_TYPES = {"print": "print", "process": "print", "printer": "printer", "machine": "printer", "filament": "filament"}
_NATIVE_TYPES = {"print": "process", "printer": "machine", "filament": "filament"}
_IDS = {"print": "print_settings_id", "printer": "printer_settings_id", "filament": "filament_settings_id"}
_METADATA = frozenset({"name", "type", "from", "version", "inherits", "instantiation", "description",
                       "setting_id", "filament_id", "renamed_from", "url", "is_custom_defined"})
_TRANSPORT = frozenset({"printer_technology", "bbl_use_printhost", "host_type", "printer_agent", "print_host",
                        "print_host_webui", "flashforge_serial_number", "printer_model", "filename_format"})
_CREDENTIALS = frozenset({"printhost_apikey", "printhost_user", "printhost_password"})
_IDENTITY = frozenset({"printer_model", "printer_technology"})
_URL_KEYS = frozenset({"print_host", "print_host_webui"})
_REDACTED = "<redacted>"
_VECTORS = frozenset({"coFloats", "coInts", "coBools", "coStrings", "coPercents", "coFloatsOrPercents", "coEnums", "coPoints"})
_SCALAR_TYPES = {"coFloats": "coFloat", "coInts": "coInt", "coBools": "coBool", "coStrings": "coString",
                 "coPercents": "coPercent", "coFloatsOrPercents": "coFloatOrPercent", "coEnums": "coEnum", "coPoints": "coPoint"}


def normalize_filament_profile(config: JsonObject, schema: JsonObject) -> JsonObject:
    """Pad only native per-profile flow variants, as PresetBundle::full_config does.

This never resizes project material slots or mutates the source profile. Missing
values stay missing; no compiled defaults are invented.
"""
    result = copy.deepcopy(config)
    modes = result.get('filament_flow_support', ['standard'])
    if isinstance(modes, str):
        modes = [modes]
    if (not isinstance(modes, list) or not modes or any(not isinstance(mode, str) for mode in modes)
            or len(set(modes)) != len(modes)
            or any(mode not in array(record(schema['filament_volume_type'])['enum_values']) for mode in modes)):
        raise ValueError('Filament preset flow declarations are ambiguous')
    if 'filament_flow_support' in result:
        result['filament_flow_support'] = modes
    for key in strings(record(record(schema['_meta'])['flow_variant_keys'])['filament']):
        if key not in result:
            continue
        supplied = result[key]
        vector: list[JsonValue]
        if isinstance(supplied, list):
            vector = supplied
        elif isinstance(supplied, str):
            vector = [item for item in supplied.split(',')]
        else:
            vector = [supplied]
        if not vector or len(vector) > len(modes):
            raise ValueError(f'Filament preset vector {key} has ambiguous flow cardinality')
        result[key] = vector + [vector[0]] * (len(modes) - len(vector))
    return result


def _type(value: str) -> str:
    if not isinstance(value, str) or value not in _TYPES:
        raise ValueError("Preset type must be print/process, filament, or printer/machine")
    return _TYPES[value]


def _transport(key: str) -> bool:
    return key in _TRANSPORT or key.startswith("printhost_")


def _gcode(key: str) -> bool:
    return key.endswith("_gcode") and key != "emit_machine_limits_to_gcode"


def _placeholder(value: JsonValue) -> bool:
    if isinstance(value, str):
        return _REDACTED in value
    if isinstance(value, (list, tuple)):
        return any(_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(_placeholder(item) for item in value.values())
    return False


def _redact(value: JsonValue, key: str = "") -> JsonValue:
    """Handle native JSON arrays too; never return credentials in raw_config."""
    if key in _CREDENTIALS and value not in (None, "", []):
        return _REDACTED
    if isinstance(value, dict):
        return {name: _redact(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if key in _URL_KEYS and isinstance(value, str) and "@" in value:
        scheme = re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", value)
        return (scheme.group(0) if scheme else "") + _REDACTED + value[value.rfind("@"):]
    return value


def _number(value: JsonValue, key: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{key}: expected a finite number")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as error:
        raise ValueError(f"{key}: expected a finite number") from error
    if not number.is_finite() or not math.isfinite(float(number)):
        raise ValueError(f"{key}: expected a finite number")
    return number


def _scalar(value: JsonValue, rec: JsonObject, key: str) -> str:
    kind = _SCALAR_TYPES.get(text(rec["type"]), text(rec["type"]))
    if value == "nil" and rec.get("nullable"):
        return "nil"
    if kind == "coBool":
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (str, int)) and str(value).lower() in ("0", "1", "true", "false"):
            return "1" if str(value).lower() in ("1", "true") else "0"
        raise ValueError(f"{key}: expected boolean or 0/1")
    if kind in ("coFloat", "coInt", "coPercent", "coFloatOrPercent"):
        percent = isinstance(value, str) and value.strip().endswith("%")
        if percent and kind not in ("coPercent", "coFloatOrPercent"):
            raise ValueError(f"{key}: percent is invalid for this type")
        number = _number(value.strip()[:-1] if isinstance(value, str) and percent else value, key)
        if kind == "coInt" and number != number.to_integral_value():
            raise ValueError(f"{key}: expected an integer")
        if kind == "coInt" and not -(2 ** 31) <= number < 2 ** 31:
            raise ValueError(f"{key}: outside native integer range")
        for field in ("min", "max"):
            limit = rec.get(field)
            if isinstance(limit, (int, float)) and math.isfinite(limit) and (number < Decimal(str(limit)) if field == "min" else number > Decimal(str(limit))):
                raise ValueError(f"{key}: outside native {field} {limit}")
        result = format(number, "f")
        if "." in result:
            result = result.rstrip("0").rstrip(".")
        return result + ("%" if percent or kind == "coPercent" else "")
    if kind in ("coString", "coEnum"):
        if not isinstance(value, str):
            raise ValueError(f"{key}: expected a string")
        if kind == "coEnum" and rec.get("enum_values") and value not in array(rec["enum_values"]):
            raise ValueError(f"{key}: not one of the native enum values")
        return value
    if kind in ("coPoint", "coPoint3"):
        dimensions = 3 if kind == "coPoint3" else 2
        parts = re.split(r"[x,]", value) if isinstance(value, str) else value
        if not isinstance(parts, (list, tuple)) or len(parts) != dimensions:
            raise ValueError(f"{key}: expected {dimensions} coordinates")
        coords = [format(_number(part, key), "f") for part in parts]
        return ("," if dimensions == 3 else "x").join(coords)
    raise ValueError(f"{key}: unsupported native setting type {kind}")


def _input_changes(value: object, schema: JsonObject) -> JsonObject:
    # JSON callers already have the internal representation. Python callers
    # additionally support tuples for an individual point, never for a vector.
    if is_json_value(value) and isinstance(value, dict):
        return value
    if not isinstance(value, dict):
        raise ValueError('Changes must be a JSON object')
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f'Unknown native setting: {key}')
        kind = record(schema[key]).get('type') if key in schema else None
        converted: object = item
        if kind in ('coPoint', 'coPoint3') and isinstance(item, tuple):
            converted = list(item)
        elif kind in ('coPoints', 'coPoints3') and isinstance(item, list):
            converted = [list(point) if isinstance(point, tuple) else point for point in item]
        result[key] = converted
    return json_object(result, 'Changes')


def validate_changes(changes: object, current: object, schema: JsonObject, allow_custom_gcode: bool = False) -> JsonObject:
    """Normalize native JSON strings/arrays without broadcasting or resizing slots.

This is an early type/range check, not Print::validate or a physical-print proof.
Unknown historical fields are preserved by editors but cannot acquire new values.
"""
    if not isinstance(changes, dict) or not isinstance(current, dict):
        raise ValueError("Changes and current configuration must be objects")
    changes = _input_changes(changes, schema)
    current = json_object(current, "Current configuration")
    normalized: JsonObject = {}
    for key, value in changes.items():
        if not isinstance(key, str) or key.startswith("_") or key not in schema:
            raise ValueError(f"Unknown native setting: {key}")
        if key == "post_process" or _transport(key):
            raise ValueError(f"{key}: writing scripts, transport, secrets, or printer identity is prohibited")
        if _gcode(key) and not allow_custom_gcode:
            raise ValueError(f"{key}: custom G-code requires allow_custom_gcode=True")
        if _placeholder(value):
            raise ValueError(f"{key}: a redacted placeholder cannot be written")
        rec = record(schema[key])
        if rec["type"] in _VECTORS:
            if not isinstance(value, list):
                raise ValueError(f"{key}: a vector requires an explicit array; slots are never broadcast")
            previous = current.get(key)
            if isinstance(previous, list) and previous and len(previous) != len(value):
                raise ValueError(f"{key}: array length/cardinality must preserve existing slots")
            normalized[key] = [_scalar(item, rec, key) for item in value]
        else:
            normalized[key] = _scalar(value, rec, key)
    return normalized
