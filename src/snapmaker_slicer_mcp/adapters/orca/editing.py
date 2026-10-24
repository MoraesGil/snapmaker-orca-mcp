"""Official native editing bridge with shared public process admission."""
from __future__ import annotations
import json
import uuid
from pathlib import Path
from contextlib import AbstractContextManager
from typing import Protocol
from ...domain.types.json import JsonObject, JsonValue, json_object
from ...domain.settings.values import text, integer, record
from ...application.contracts.projects import ProjectDocument
from ...adapters.storage.revisions import StoredProjectDocument, FilesystemRevisionRepository
from ...engine import Engine

_ADAPTIVE_SOURCE = '9bf2f18eaff7cc0655cbdeef6a215660085ecb0f'

class NativeHost(Protocol):
    @property
    def engine(self) -> Engine: ...
    @property
    def timeout(self) -> float: ...
    def native_session(self) -> AbstractContextManager[None]: ...

class OrcaNativeEditing:
    def __init__(self, host: NativeHost, revisions: FilesystemRevisionRepository):
        self.host = host
        self.revisions = revisions

    def execute(self, document: ProjectDocument, name: str, arguments: JsonObject,
                state: JsonObject) -> JsonValue:
        if not isinstance(document, StoredProjectDocument):
            raise ValueError('Native editing requires a stored 3MF document')
        project = document.project
        directory = self.revisions.workspace(state)
        if name == 'set_layer_height':
            mode = arguments.pop('mode')
            oid = integer(arguments.pop('object_id'))
            if mode in ('reset', 'default', 'none'):
                project.reset_layer_height(oid)
                return None
            if mode != 'adaptive':
                raise ValueError('mode must be adaptive or reset')
            from ...native_ops import adaptive_layer_profile
            if self.host.engine.identity()['source_commit'] != _ADAPTIVE_SOURCE:
                raise ValueError('Adaptive generator source commit differs from the engine; update and validate the MCP adapter explicitly')
            staged = directory / ('adaptive-' + uuid.uuid4().hex + '.3mf')
            document.save(staged)
            quality = arguments.get('quality', 0.5)
            if not isinstance(quality, (int, float)) or isinstance(quality, bool):
                raise ValueError('quality must be a number')
            profile = adaptive_layer_profile(staged, oid, document.config, quality)
            project.set_layer_profile(oid, profile)
            return {'object_id': oid, 'quality': quality, 'profile': [value for value in profile],
                    'generator_source_commit': _ADAPTIVE_SOURCE}
        from ...native_ops import native_deltas
        from ...project import compose_transform
        work = directory / ('native-' + uuid.uuid4().hex)
        work.mkdir()
        staged = work / 'input.3mf'
        document.save(staged)
        selected_plate = integer(arguments.get('plate', state['selected_plate']))
        with self.host.native_session():
            deltas = native_deltas(self.host.engine, staged, work, arrange=name == 'arrange_plate',
                                  orient=name == 'auto_orient', plate=selected_plate, timeout=self.host.timeout)
        audit = record(json.loads((work / 'native_ops.json').read_text()))
        if name == 'arrange_plate' and selected_plate == 0:
            result = json_object(project.apply_native_layout(audit.get('layout'), deltas))
        else:
            ordinals: dict[int, int] = {}
            for item in project.model.findall('.//{*}build/{*}item'):
                oid = int(item.get('objectid', ''))
                idx = ordinals.get(oid, 0)
                ordinals[oid] = idx + 1
                if (oid, idx) in deltas:
                    before = [float(v) for v in item.get('transform', '1 0 0 0 1 0 0 0 1 0 0 0').split()]
                    item.set('transform', ' '.join(format(v, '.12g') for v in compose_transform(before, deltas[oid, idx])))
            result = {'changed_instances': len(deltas)}
        result['diagnostics'] = str(work)
        return result
