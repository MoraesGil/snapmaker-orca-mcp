"""Versioned project edits; source projects and installed presets stay read-only."""
from __future__ import annotations

import copy
import json
import re
import shutil
import threading
import uuid
from pathlib import Path

from .engine import file_hash
from .presets import Catalog, load_schema, normalize_filament_profile, validate_changes, _redact, _transport, _VECTORS
from .project import Project
from .service import validate_project, write_json

_ADAPTIVE_SOURCE = '9bf2f18eaff7cc0655cbdeef6a215660085ecb0f'
_PRESET_COMPATIBILITY = frozenset({'compatible_printers', 'compatible_prints',
                                   'compatible_printers_condition', 'compatible_prints_condition'})


def _public(config):
    return {k: ('[redacted]' if re.search(r'password|api_?key|access_token|printhost', k, re.I) else v)
            for k, v in _redact(config).items()}


def _filament_layout(config):
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
            'declarations': declarations, 'volume_types': list(volume_types), 'segmented': segmented}


def _flow_index(layout, slot, mode):
    start, end = layout['offsets'][slot:slot + 2]
    if not layout['segmented']:
        return slot
    modes = layout['declarations'][start:end]
    return start + (modes.index(mode) if mode in modes else 0)


def _project_vector(config, key, length):
    value = config.get(key)
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f'Project vector {key} must preserve its native cardinality {length}')
    return list(value)


def _native_default_vector(key, length, rec, schema_status):
    """Expand only an unambiguous literal from this bound native source."""
    if rec.get('nullable'):
        raise ValueError(f'{key}: missing nullable vector has no safe native default')
    commit = schema_status.get('source_commit')
    if not commit or commit != schema_status.get('engine_commit'):
        raise ValueError(f'{key}: native default requires matching schema and engine commits')
    if rec.get('type') not in _VECTORS:
        raise ValueError(f'{key}: native default requires a vector setting')
    try:
        value = json.loads(rec.get('default'))
    except (TypeError, ValueError) as error:
        raise ValueError(f'{key}: native default is not a JSON literal') from error
    if type(value) not in (str, int, float, bool):
        raise ValueError(f'{key}: native default must be a non-null JSON primitive')
    # This also rejects non-finite JSON extensions, wrong types and native ranges.
    return validate_changes({key: [value]}, {})[key] * length


def _validate_flow_vectors(config, schema, layout):
    # These are the source-defined variant keys, not a heuristic on list length.
    for key, rec in schema.items():
        if rec.get('flow_variant_domain') == 'filament' and key in config:
            _project_vector(config, key, layout['total'])


class Editor:
    def __init__(self, slicer, preset_dirs=()):
        self.slicer = slicer
        self.root = slicer.root / 'projects'
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog = Catalog(slicer.engine, [Path(p) for p in preset_dirs], slicer.root / 'preset-drafts')
        self._lock = threading.RLock()

    def _state(self, project_id):
        if not re.fullmatch(r'[0-9a-f]{32}', project_id):
            raise ValueError('Invalid project_id')
        path = self.root / project_id / 'state.json'
        state = json.loads(path.read_text())
        current = Path(state['path'])
        if current.parent != path.parent or file_hash(current) != state['sha256']:
            raise ValueError('Working project changed outside the editor')
        return state

    def load_model(self, path: str) -> dict:
        """Load a saved project into an isolated editing workspace."""
        source = Path(path).expanduser().resolve(strict=True)
        validate_project(source)
        with self._lock:
            project_id = uuid.uuid4().hex
            directory = self.root / project_id
            directory.mkdir()
            snapshot = directory / 'source.3mf'
            expected = file_hash(source)
            shutil.copyfile(source, snapshot)
            if file_hash(snapshot) != expected:
                raise ValueError('Source changed while loading; retry the saved file')
            Project(snapshot)
            state = {'project_id': project_id, 'path': str(snapshot), 'sha256': expected,
                     'source': {'path': str(source), 'sha256': expected}, 'revision': 0,
                     'selected_plate': 1, 'engine': self.slicer.engine.identity()}
            write_json(directory / 'state.json', state)
            return state

    def get_status(self, project_id: str) -> dict:
        with self._lock:
            state = self._state(project_id)
            return {**state, 'project': Project(Path(state['path'])).describe()}

    def get_config(self, project_id: str, keys=None) -> dict:
        with self._lock:
            state = self._state(project_id)
            config = Project(Path(state['path'])).get_config(keys)
            return {**state, 'config': _public(config)}

    def list_objects(self, project_id: str) -> dict:
        with self._lock:
            state = self._state(project_id)
            return {**state, 'objects': Project(Path(state['path'])).list_objects()}

    def list_plates(self, project_id: str) -> dict:
        with self._lock:
            state = self._state(project_id)
            return {**state, 'plates': Project(Path(state['path'])).list_plates()}

    def select_plate(self, project_id: str, plate: int) -> dict:
        with self._lock:
            state = self._state(project_id)
            count = len(Project(Path(state['path'])).list_plates())
            if type(plate) is not int or not 1 <= plate <= count:
                raise ValueError('plate must identify an existing plate (1-based)')
            state['selected_plate'] = plate
            write_json(self.root / project_id / 'state.json', state)
            return state

    def edit_project(self, project_id: str, operations: list[dict], expected_sha256=None,
                     allow_custom_gcode=False) -> dict:
        """Apply a batch atomically and return a new immutable working revision."""
        if not isinstance(operations, list) or not operations:
            raise ValueError('operations must be a nonempty list')
        with self._lock:
            self.slicer.engine.verify()
            state = self._state(project_id)
            if expected_sha256 is not None and expected_sha256 != state['sha256']:
                raise ValueError('Stale project revision; reload the current sha256')
            project = Project(Path(state['path']))
            directory = self.root / project_id
            results = []
            for requested in operations:
                if not isinstance(requested, dict):
                    raise ValueError('Each operation must be an object')
                operation = copy.deepcopy(requested)
                name = operation.pop('operation', None)
                if name in ('set_config', 'set_object_config'):
                    changes = operation.pop('changes', None)
                    normalized = validate_changes(changes, project.config, allow_custom_gcode)
                    if name == 'set_config':
                        if operation:
                            raise ValueError(f'Unexpected set_config arguments: {sorted(operation)}')
                        result = project.set_config(normalized)
                    else:
                        result = project.set_object_config(changes=normalized, **operation)
                elif name in ('duplicate_object', 'delete_object', 'transform_object', 'add_plate',
                              'delete_plate', 'move_object_to_plate', 'set_height_range', 'reset_layer_height'):
                    result = getattr(project, name)(**operation)
                elif name in ('arrange_plate', 'auto_orient'):
                    from .native_ops import native_deltas
                    from .project import compose_transform
                    work = directory / ('native-' + uuid.uuid4().hex)
                    work.mkdir()
                    staged = work / 'input.3mf'
                    project.save(staged)
                    selected_plate = operation.get('plate', state['selected_plate'])
                    with self.slicer._lock:
                        deltas = native_deltas(self.slicer.engine, staged, work,
                                               arrange=name == 'arrange_plate', orient=name == 'auto_orient',
                                               plate=selected_plate,
                                               timeout=self.slicer.timeout)
                    audit = json.loads((work / 'native_ops.json').read_text())
                    if name == 'arrange_plate' and selected_plate == 0:
                        result = project.apply_native_layout(audit.get('layout'), deltas)
                    else:
                        ordinals = {}
                        for item in project.model.findall('.//{*}build/{*}item'):
                            oid = int(item.get('objectid'))
                            idx = ordinals.get(oid, 0)
                            ordinals[oid] = idx + 1
                            if (oid, idx) in deltas:
                                before = [float(v) for v in item.get('transform', '1 0 0 0 1 0 0 0 1 0 0 0').split()]
                                item.set('transform', ' '.join(format(v, '.12g') for v in compose_transform(before, deltas[oid, idx])))
                        result = {'changed_instances': len(deltas)}
                    result['diagnostics'] = str(work)
                elif name == 'set_layer_height':
                    mode = operation.pop('mode')
                    oid = operation.pop('object_id')
                    if mode in ('reset', 'default', 'none'):
                        result = project.reset_layer_height(oid)
                    elif mode == 'adaptive':
                        from .native_ops import adaptive_layer_profile
                        if self.slicer.engine.identity()['source_commit'] != _ADAPTIVE_SOURCE:
                            raise ValueError('Adaptive generator source commit differs from the engine; update and validate the MCP adapter explicitly')
                        staged = directory / ('adaptive-' + uuid.uuid4().hex + '.3mf')
                        project.save(staged)
                        quality = operation.get('quality', 0.5)
                        profile = adaptive_layer_profile(staged, oid, project.config, quality)
                        project.set_layer_profile(oid, profile)
                        result = {'object_id': oid, 'quality': quality, 'profile': profile,
                                  'generator_source_commit': _ADAPTIVE_SOURCE}
                    else:
                        raise ValueError('mode must be adaptive or reset')
                else:
                    raise ValueError(f'Unknown editing operation: {name}')
                results.append({'operation': name, 'result': result})
            output = directory / (uuid.uuid4().hex + '.3mf')
            project.save(output)
            validate_project(output)
            state.update(path=str(output), sha256=file_hash(output), revision=state['revision'] + 1,
                         selected_plate=min(state['selected_plate'], len(project.list_plates())),
                         changes=copy.deepcopy(operations), results=results)
            self.slicer.engine.verify()
            write_json(directory / 'state.json', state)
            return {**state, 'requires_slice': True}

    def set_config(self, project_id: str, changes: dict, expected_sha256=None, allow_custom_gcode=False):
        return self.edit_project(project_id, [{'operation': 'set_config', 'changes': changes}],
                                 expected_sha256, allow_custom_gcode)

    def select_preset(self, project_id: str, type: str, name: str, filament_slot=0,
                      flow_mode='standard', expected_sha256=None):
        with self._lock:
            state = self._state(project_id)
            project = Project(Path(state['path']))
            preset = self.catalog.get_preset_config(type, name)
            schema = load_schema()
            changes = {k: v for k, v in preset['config'].items() if k in schema}
            ignored = sorted(set(preset['config']) - set(changes))
            for key in list(changes):
                if key in ('printer_model', 'printer_technology'):
                    if changes[key] != project.config.get(key, 'FFF' if key == 'printer_technology' else None):
                        raise ValueError('Selected preset changes the target machine; use a matching saved project')
                if key == 'post_process' or _transport(key) or key in _PRESET_COMPATIBILITY:
                    if key == 'post_process' and changes[key] not in ([], [''], ''):
                        raise ValueError('Selected preset contains unsupported post-processing scripts')
                    ignored.append(key)
                    changes.pop(key)
            normalized_vectors = []
            for key, value in list(changes.items()):
                kind = schema[key]['type']
                if kind in _VECTORS and not isinstance(value, list):
                    vector = value.split(',') if isinstance(value, str) and kind != 'coStrings' else [value]
                    previous = project.config.get(key)
                    if type != 'filament' and len(vector) == 1 and isinstance(previous, list):
                        vector *= len(previous)
                    changes[key] = vector
                    normalized_vectors.append(key)
            flow_trace = {}
            native_defaults = []
            if type == 'filament':
                layout = _filament_layout(project.config)
                count = layout['count']
                if not isinstance(filament_slot, int) or isinstance(filament_slot, bool) or not 0 <= filament_slot < count:
                    raise ValueError('filament_slot must identify an existing slot (0-based)')
                if flow_mode not in ('standard', 'high_flow'):
                    raise ValueError('flow_mode must be standard or high_flow')
                _validate_flow_vectors(project.config, schema, layout)
                # Step sizes and declarations belong to the loaded project.
                changes = {key: value for key, value in changes.items()
                           if key not in ('filament_flow_step_size', 'filament_volume_type')}
                profile = normalize_filament_profile(changes)
                modes = profile.get('filament_flow_support', ['standard'])
                index = modes.index(flow_mode) if flow_mode in modes else 0
                target = _flow_index(layout, filament_slot, flow_mode)
                selected = {}
                for key, value in profile.items():
                    if key in ('filament_flow_support', 'filament_settings_id'):
                        continue
                    if isinstance(value, list):
                        if not value:
                            continue
                        variant = schema[key].get('flow_variant_domain') == 'filament'
                        length = layout['total'] if variant else count
                        if key in project.config:
                            vector = _project_vector(project.config, key, length)
                        else:
                            vector = _native_default_vector(key, length, schema[key], preset['schema'])
                            native_defaults.append({
                                'key': key, 'default': vector[0], 'cardinality': length,
                                'source': {'repository': self.slicer.engine.identity()['repository'],
                                           'path': 'src/libslic3r/PrintConfig.cpp',
                                           'source_commit': preset['schema']['source_commit']},
                            })
                        vector[target if variant else filament_slot] = value[index if variant else 0]
                        selected[key] = vector
                    else:
                        selected[key] = value
                ids = list(project.config['filament_settings_id'])
                ids[filament_slot] = name
                selected['filament_settings_id'] = ids
                volume_types = layout['volume_types']
                volume_types[filament_slot] = flow_mode
                selected['filament_volume_type'] = volume_types
                flow_trace = {'project_flow_index': target, 'preset_flow_index': index,
                              'project_flow_offset': layout['offsets'][filament_slot],
                              'project_flow_step_size': layout['steps'][filament_slot],
                              'project_flow_layout': 'segmented' if layout['segmented'] else 'legacy_flat',
                              'preset_flow_mode': modes[index],
                              'preset_flow_fallback': flow_mode not in modes}
                changes = selected
            elif type not in ('process', 'machine', 'print', 'printer'):
                raise ValueError('Preset type must be filament, process or machine')
            else:
                changes['print_settings_id' if type in ('process', 'print') else 'printer_settings_id'] = name
            result = self.set_config(project_id, changes, expected_sha256, allow_custom_gcode=True)
            return {**result, 'selected_preset': {'type': type, 'name': name, 'filament_slot': filament_slot,
                                                 'flow_mode': flow_mode, 'source': preset.get('source'), **flow_trace},
                    'ignored_config_keys': sorted(set(ignored)), 'normalized_vector_keys': normalized_vectors,
                    'native_defaults_applied': native_defaults,
                    'schema': preset.get('schema'),
                    'configuration_scope': 'Resolved JSON inheritance; native defaults and final region settings require slicing'}

    def save_preset(self, project_id: str, type: str, name: str, filament_slot=0, detach=False,
                    allow_custom_gcode=False):
        with self._lock:
            state = self._state(project_id)
            project_config = Project(Path(state['path'])).config
            schema = load_schema()
            allowed = self.catalog.preset_keys(type)
            config = {k: v for k, v in project_config.items()
                      if k in allowed and k != 'post_process' and k not in _PRESET_COMPATIBILITY}
            if type == 'filament':
                layout = _filament_layout(project_config)
                count = layout['count']
                if not isinstance(filament_slot, int) or isinstance(filament_slot, bool) or not 0 <= filament_slot < count:
                    raise ValueError('Invalid filament_slot')
                _validate_flow_vectors(project_config, schema, layout)
                active_mode = layout['volume_types'][filament_slot]
                active_index = _flow_index(layout, filament_slot, active_mode)
                extracted = {}
                for key, value in config.items():
                    if key in ('filament_flow_step_size', 'filament_flow_support', 'filament_volume_type'):
                        continue
                    if isinstance(value, list):
                        variant = schema[key].get('flow_variant_domain') == 'filament'
                        vector = _project_vector(project_config, key, layout['total'] if variant else count)
                        extracted[key] = [vector[active_index if variant else filament_slot]]
                config = {**extracted, 'filament_flow_support': [active_mode]}
            id_key = {'filament': 'filament_settings_id', 'process': 'print_settings_id', 'print': 'print_settings_id',
                      'machine': 'printer_settings_id', 'printer': 'printer_settings_id'}[type]
            parent = project_config.get(id_key, '')
            if type == 'filament':
                parent = parent[filament_slot] if isinstance(parent, list) else ''
            available = self.catalog.list_presets(type, include_system=True)['presets']
            if any(p['name'] == parent for p in available):
                if parent != name:
                    config['inherits'] = parent
                if type == 'filament':
                    inherited = normalize_filament_profile(self.catalog.get_preset_config(type, parent)['config'])
                    modes = inherited.get('filament_flow_support', ['standard'])
                    config['filament_flow_support'] = modes
                    index = modes.index(active_mode) if active_mode in modes else 0
                    for key, value in list(config.items()):
                        previous = inherited.get(key)
                        if key == 'filament_flow_support' or not isinstance(value, list):
                            continue
                        if schema[key].get('flow_variant_domain') == 'filament':
                            if not isinstance(previous, list) or not previous:
                                if len(modes) > 1:
                                    raise ValueError(f'Parent filament vector {key} has no values for its other flow modes')
                                continue
                            preserved = list(previous)
                            preserved[index] = value[0]
                            config[key] = preserved
                        elif isinstance(previous, list) and previous:
                            # Nonvariant named-preset vectors are read at source index0.
                            config[key] = [value[0], *previous[1:]]
            else:
                config = {k: v for k, v in config.items() if k not in ('printer_model', 'printer_technology')}
            return self.catalog.save_preset(type, name, config, detach, allow_custom_gcode)

    def slice_project(self, project_id: str, plate=None, force=False, allow_newer_file=True):
        with self._lock:
            state = self._state(project_id)
            return self.slicer.slice_3mf(state['path'], state['selected_plate'] if plate is None else plate,
                                         force, allow_newer_file)

    def apply_and_slice(self, project_id: str, changes: dict, plate=None, expected_sha256=None):
        with self._lock:
            edited = self.set_config(project_id, changes, expected_sha256)
            return {'project': edited, 'slice': self.slice_project(project_id, plate, force=True)}
