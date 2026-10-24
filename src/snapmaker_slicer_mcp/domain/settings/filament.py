"""Pure material-slot and native-flow configuration rules."""
from __future__ import annotations
import json
import re
from typing import TypedDict
from ..types.json import JsonObject, JsonValue
from .values import record, text, array, strings
from .normalization import _redact, _VECTORS, validate_changes

class FilamentLayout(TypedDict):
    count: int
    steps: list[int]
    offsets: list[int]
    total: int
    declarations: list[str]
    volume_types: list[str]
    segmented: bool

def _public(config: JsonObject) -> JsonObject:
    return {k: ('[redacted]' if re.search(r'password|api_?key|access_token|printhost', k, re.I) else v)
            for k, v in record(_redact(config)).items()}


def _filament_layout(config: JsonObject) -> FilamentLayout:
    """Native 9bf PrintConfig.cpp get_config_idx, without changing stored shapes."""
    ids = config.get('filament_settings_id', [])
    if not isinstance(ids, list) or not ids:
        raise ValueError('Project must identify its existing material slots')
    count = len(ids)
    raw_steps = config.get('filament_flow_step_size')
    segmented = raw_steps is not None and raw_steps != []
    if segmented:
        if not isinstance(raw_steps, list) or len(raw_steps) != count:
            raise ValueError('filament_flow_step_size must have one integer per material')
        steps = []
        for value in raw_steps:
            if not isinstance(value, str):
                raise ValueError('filament_flow_step_size requires a native 3MF string-array')
            if not re.fullmatch(r'\d+', value) or int(value) <= 0:
                raise ValueError('filament_flow_step_size must contain positive integers')
            steps.append(int(value))
    else:
        steps = [1] * count  # Native legacy: get_config_idx returns filament_id.
    offsets = [0]
    for step in steps:
        offsets.append(offsets[-1] + step)
    declarations = config.get('filament_flow_support')
    if declarations is None or declarations == []:
        if any(step != 1 for step in steps):
            raise ValueError('Segmented filament flow declarations are missing')
        declarations = ['standard'] * count
    if (not isinstance(declarations, list) or len(declarations) != offsets[-1]
            or any(not isinstance(mode, str) for mode in declarations)):
        raise ValueError('filament_flow_support cardinality differs from its native segments')
    for slot in range(count):
        modes = declarations[offsets[slot]:offsets[slot + 1]]
        if len(set(modes)) != len(modes) or any(mode not in ('standard', 'high_flow') for mode in modes):
            raise ValueError('Project filament flow declarations are ambiguous')
    volume_types = config.get('filament_volume_type')
    if volume_types is None or volume_types == []:
        volume_types = ['standard'] * count
    if (not isinstance(volume_types, list) or len(volume_types) != count
            or any(mode not in ('standard', 'high_flow') for mode in volume_types)):
        raise ValueError('filament_volume_type must identify one native mode per material')
    return {'count': count, 'steps': steps, 'offsets': offsets, 'total': offsets[-1],
            'declarations': strings(declarations), 'volume_types': strings(volume_types), 'segmented': segmented}


def _flow_index(layout: FilamentLayout, slot: int, mode: str) -> int:
    start, end = layout['offsets'][slot:slot + 2]
    if not layout['segmented']:
        return slot
    modes = layout['declarations'][start:end]
    return start + (modes.index(mode) if mode in modes else 0)


def _project_vector(config: JsonObject, key: str, length: int) -> list[JsonValue]:
    value = config.get(key)
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f'Project vector {key} must preserve its native cardinality {length}')
    return list(value)


def _native_default_vector(key: str, length: int, rec: JsonObject, schema_status: JsonObject, schema: JsonObject) -> list[JsonValue]:
    """Expand only an unambiguous literal from this bound native source."""
    if rec.get('nullable'):
        raise ValueError(f'{key}: missing nullable vector has no safe native default')
    commit = schema_status.get('source_commit')
    if not commit or commit != schema_status.get('engine_commit'):
        raise ValueError(f'{key}: native default requires matching schema and engine commits')
    if rec.get('type') not in _VECTORS:
        raise ValueError(f'{key}: native default requires a vector setting')
    try:
        value = json.loads(text(rec.get('default')))
    except (TypeError, ValueError) as error:
        raise ValueError(f'{key}: native default is not a JSON literal') from error
    if type(value) not in (str, int, float, bool):
        raise ValueError(f'{key}: native default must be a non-null JSON primitive')
    # This also rejects non-finite JSON extensions, wrong types and native ranges.
    return array(validate_changes({key: [value]}, {}, schema)[key]) * length


def _validate_flow_vectors(config: JsonObject, schema: dict[str, JsonObject], layout: FilamentLayout) -> None:
    # These are the source-defined variant keys, not a heuristic on list length.
    for key, rec in schema.items():
        if rec.get('flow_variant_domain') == 'filament' and key in config:
            _project_vector(config, key, layout['total'])
