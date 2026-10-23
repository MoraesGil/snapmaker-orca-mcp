"""Project edit use cases coordinated through explicit document and storage ports."""
from __future__ import annotations
import copy
from ...domain.types.json import JsonObject, JsonValue
from ...domain.settings.values import record, text, array, strings, integer
from ...domain.settings.normalization import normalize_filament_profile, validate_changes, _transport, _VECTORS
from ...domain.settings.filament import (_public, _filament_layout, _flow_index, _project_vector,
                                        _native_default_vector, _validate_flow_vectors)
from ..contracts.projects import ProjectSlicer, RevisionRepository, NativeEditing
from ..contracts.presets import PresetCatalog
_PRESET_COMPATIBILITY = frozenset({'compatible_printers', 'compatible_prints',
                                  'compatible_printers_condition', 'compatible_prints_condition'})

class ProjectService:
    def __init__(self, slicer: ProjectSlicer, repository: RevisionRepository,
                 catalog: PresetCatalog, native: NativeEditing, schema: JsonObject) -> None:
        self.slicer = slicer
        self.repository = repository
        self.catalog = catalog
        self.native = native
        self.schema = schema

    def _state(self, project_id: str) -> JsonObject:
        return self.repository.state(project_id)

    def load_model(self, path: str) -> JsonObject:
        with self.repository.transaction():
            return self.repository.load(path, self.slicer.engine.identity())

    def get_status(self, project_id: str) -> JsonObject:
        with self.repository.transaction():
            state = self._state(project_id)
            return {**state, 'project': self.repository.open(state).describe()}

    def get_config(self, project_id: str, keys: list[str] | None = None) -> JsonObject:
        with self.repository.transaction():
            state = self._state(project_id)
            config = self.repository.open(state).get_config(keys)
            return {**state, 'config': _public(config)}

    def list_objects(self, project_id: str) -> JsonObject:
        with self.repository.transaction():
            state = self._state(project_id)
            return {**state, 'objects': self.repository.open(state).list_objects()}

    def list_plates(self, project_id: str) -> JsonObject:
        with self.repository.transaction():
            state = self._state(project_id)
            return {**state, 'plates': self.repository.open(state).list_plates()}

    def select_plate(self, project_id: str, plate: int) -> JsonObject:
        with self.repository.transaction():
            state = self._state(project_id)
            count = len(self.repository.open(state).list_plates())
            if type(plate) is not int or not 1 <= plate <= count:
                raise ValueError('plate must identify an existing plate (1-based)')
            return self.repository.select_plate(state, plate)

    def edit_project(self, project_id: str, operations: list[JsonObject], expected_sha256: str | None = None,
                     allow_custom_gcode: bool = False) -> JsonObject:
        """Apply a batch atomically and return a new immutable working revision."""
        if not isinstance(operations, list) or not operations:
            raise ValueError('operations must be a nonempty list')
        with self.repository.transaction():
            self.slicer.engine.verify()
            state = self._state(project_id)
            if expected_sha256 is not None and expected_sha256 != state['sha256']:
                raise ValueError('Stale project revision; reload the current sha256')
            project = self.repository.open(state)
            results: list[JsonValue] = []
            for requested in operations:
                if not isinstance(requested, dict):
                    raise ValueError('Each operation must be an object')
                operation = copy.deepcopy(requested)
                name = operation.pop('operation', None)
                if name in ('set_config', 'set_object_config'):
                    changes = operation.pop('changes', None)
                    normalized = validate_changes(changes, project.config, self.schema, allow_custom_gcode)
                    if name == 'set_config':
                        if operation:
                            raise ValueError(f'Unexpected set_config arguments: {sorted(operation)}')
                        result: JsonValue = project.execute('set_config', {'changes': normalized})
                    else:
                        result = project.execute('set_object_config', {'changes': normalized, **operation})
                elif name in ('add_modifier', 'duplicate_object', 'delete_object', 'transform_object', 'add_plate',
                              'delete_plate', 'move_object_to_plate', 'set_height_range', 'reset_layer_height'):
                    result = project.execute(name, operation)
                elif name in ('arrange_plate', 'auto_orient', 'set_layer_height'):
                    result = self.native.execute(project, name, operation, state)
                else:
                    raise ValueError(f'Unknown editing operation: {name}')
                results.append({'operation': name, 'result': result})
            return self.repository.commit(state, project, operations, results, self.slicer.engine)

    def add_modifier(self, project_id: str, object_id: int, center: list[float], dimensions: list[float],
                     changes: JsonObject, name: str = "Modifier", expected_sha256: str | None = None) -> JsonObject:
        return self.edit_project(project_id, [{"operation": "add_modifier", "object_id": object_id,
            "center": [value for value in center], "dimensions": [value for value in dimensions], "changes": changes, "name": name}], expected_sha256)

    def set_config(self, project_id: str, changes: JsonObject, expected_sha256: str | None = None, allow_custom_gcode: bool = False) -> JsonObject:
        return self.edit_project(project_id, [{'operation': 'set_config', 'changes': changes}],
                                 expected_sha256, allow_custom_gcode)

    def select_preset(self, project_id: str, type: str, name: str, filament_slot: int = 0,
                      flow_mode: str = 'standard', expected_sha256: str | None = None) -> JsonObject:
        with self.repository.transaction():
            state = self._state(project_id)
            project = self.repository.open(state)
            preset = self.catalog.get_preset_config(type, name)
            schema = {key: record(value) for key, value in self.schema.items() if not key.startswith('_')}
            changes = {k: v for k, v in record(preset['config']).items() if k in schema}
            ignored = sorted(set(record(preset['config'])) - set(changes))
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
                    expanded: list[JsonValue] = [item for item in value.split(',')] if isinstance(value, str) and kind != 'coStrings' else [value]
                    previous = project.config.get(key)
                    if type != 'filament' and len(expanded) == 1 and isinstance(previous, list):
                        expanded *= len(previous)
                    changes[key] = expanded
                    normalized_vectors.append(key)
            flow_trace: JsonObject = {}
            native_defaults: list[JsonValue] = []
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
                profile = normalize_filament_profile(changes, self.schema)
                modes = strings(profile.get('filament_flow_support', ['standard']))
                index = modes.index(flow_mode) if flow_mode in modes else 0
                target = _flow_index(layout, filament_slot, flow_mode)
                selected: JsonObject = {}
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
                            vector = _native_default_vector(key, length, schema[key], record(preset['schema']), self.schema)
                            native_defaults.append({
                                'key': key, 'default': vector[0], 'cardinality': length,
                                'source': {'repository': self.slicer.engine.identity()['repository'],
                                           'path': 'src/libslic3r/PrintConfig.cpp',
                                           'source_commit': record(preset['schema'])['source_commit']},
                            })
                        vector[target if variant else filament_slot] = value[index if variant else 0]
                        selected[key] = vector
                    else:
                        selected[key] = value
                ids = list(array(project.config['filament_settings_id']))
                ids[filament_slot] = name
                selected['filament_settings_id'] = ids
                volume_types = layout['volume_types']
                volume_types[filament_slot] = flow_mode
                selected['filament_volume_type'] = [mode for mode in volume_types]
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
                    'ignored_config_keys': [key for key in sorted(set(ignored))], 'normalized_vector_keys': [key for key in normalized_vectors],
                    'native_defaults_applied': native_defaults,
                    'schema': preset.get('schema'),
                    'configuration_scope': 'Resolved JSON inheritance; native defaults and final region settings require slicing'}

    def save_preset(self, project_id: str, type: str, name: str, filament_slot: int = 0, detach: bool = False,
                    allow_custom_gcode: bool = False) -> JsonObject:
        with self.repository.transaction():
            state = self._state(project_id)
            project_config = self.repository.open(state).config
            schema = {key: record(value) for key, value in self.schema.items() if not key.startswith('_')}
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
            available = array(self.catalog.list_presets(type, include_system=True)['presets'])
            if any(record(p)['name'] == parent for p in available):
                if parent != name:
                    config['inherits'] = parent
                if type == 'filament':
                    inherited = normalize_filament_profile(record(self.catalog.get_preset_config(type, text(parent))['config']), self.schema)
                    modes = strings(inherited.get('filament_flow_support', ['standard']))
                    config['filament_flow_support'] = [mode for mode in modes]
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

    def slice_project(self, project_id: str, plate: int | None = None, force: bool = False, allow_newer_file: bool = True) -> JsonObject:
        with self.repository.transaction():
            state = self._state(project_id)
            target = integer(state['selected_plate']) if plate is None else plate
        # Each saved path is an immutable revision; later edits create another file.
        return self.slicer.slice_3mf(text(state['path']), target, force, allow_newer_file)

    def apply_and_slice(self, project_id: str, changes: JsonObject, plate: int | None = None, expected_sha256: str | None = None) -> JsonObject:
        with self.repository.transaction():
            edited = self.set_config(project_id, changes, expected_sha256)
            target = integer(edited['selected_plate']) if plate is None else plate
        return {'project': edited, 'slice': self.slicer.slice_3mf(text(edited['path']), target, force=True)}
