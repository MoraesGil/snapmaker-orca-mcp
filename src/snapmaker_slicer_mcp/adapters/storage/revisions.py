"""Filesystem publication and CAS-protected immutable 3MF revision storage."""
from __future__ import annotations
import copy
import json
import re
import shutil
import threading
import uuid
from contextlib import AbstractContextManager
from pathlib import Path
from ...domain.types.json import JsonObject, JsonValue, json_object, is_json_value
from ...domain.settings.values import text, integer
from ...application.contracts.projects import ProjectDocument
from ...application.contracts.presets import EngineIdentity
from ..orca.engine import file_hash
from ..three_mf.document import Project
from .artifacts import validate_project, write_json


class StoredProjectDocument:
    """Validated JSON boundary around the concrete preserved 3MF graph."""
    def __init__(self, path: Path):
        self.project = Project(path)

    @property
    def config(self) -> JsonObject:
        return json_object(self.project.config)

    def describe(self) -> JsonObject:
        return json_object(self.project.describe())

    def get_config(self, keys: list[str] | None = None) -> JsonObject:
        return json_object(self.project.get_config(keys))

    def list_objects(self) -> list[JsonValue]:
        return [json_object(item) for item in self.project.list_objects()]

    def list_plates(self) -> list[JsonValue]:
        return [json_object(item) for item in self.project.list_plates()]

    def execute(self, name: str, arguments: JsonObject) -> JsonValue:
        # Operation names are selected by the application allowlist; validate
        # dynamic graph dispatch results before crossing the JSON port.
        function: object = getattr(self.project, name)
        if not callable(function):
            raise ValueError(f'Unsupported project operation: {name}')
        result: object = function(**arguments)
        if not is_json_value(result):
            raise ValueError('Project operation result must be JSON')
        return result

    def save(self, path: Path) -> None:
        self.project.save(path)


class FilesystemRevisionRepository:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def transaction(self) -> AbstractContextManager[object]:
        return self._lock

    def state(self, project_id: str) -> JsonObject:
        if not re.fullmatch(r'[0-9a-f]{32}', project_id):
            raise ValueError('Invalid project_id')
        path = self.root / project_id / 'state.json'
        state = json_object(json.loads(path.read_text()))
        current = Path(text(state['path']))
        if current.parent != path.parent or file_hash(current) != state['sha256']:
            raise ValueError('Working project changed outside the editor')
        return state

    def load(self, path: str, identity: JsonObject) -> JsonObject:
        source = Path(path).expanduser().resolve(strict=True)
        validate_project(source)
        project_id = uuid.uuid4().hex
        directory = self.root / project_id
        directory.mkdir()
        snapshot = directory / 'source.3mf'
        expected = file_hash(source)
        shutil.copyfile(source, snapshot)
        if file_hash(snapshot) != expected:
            raise ValueError('Source changed while loading; retry the saved file')
        StoredProjectDocument(snapshot)
        state: JsonObject = {'project_id': project_id, 'path': str(snapshot), 'sha256': expected,
            'source': {'path': str(source), 'sha256': expected}, 'revision': 0,
            'selected_plate': 1, 'engine': identity}
        write_json(directory / 'state.json', state)
        return state

    def open(self, state: JsonObject) -> StoredProjectDocument:
        return StoredProjectDocument(Path(text(state['path'])))

    def select_plate(self, state: JsonObject, plate: int) -> JsonObject:
        state['selected_plate'] = plate
        write_json(self.root / text(state['project_id']) / 'state.json', state)
        return state

    def commit(self, state: JsonObject, document: ProjectDocument, operations: list[JsonObject],
               results: list[JsonValue], engine: EngineIdentity) -> JsonObject:
        if not isinstance(document, StoredProjectDocument):
            raise ValueError('Revision document belongs to another repository')
        directory = self.root / text(state['project_id'])
        output = directory / (uuid.uuid4().hex + '.3mf')
        document.save(output)
        validate_project(output)
        state.update(path=str(output), sha256=file_hash(output), revision=integer(state['revision']) + 1,
                     selected_plate=min(integer(state['selected_plate']), len(document.list_plates())),
                     changes=[copy.deepcopy(operation) for operation in operations], results=results)
        engine.verify()
        write_json(directory / 'state.json', state)
        return {**state, 'requires_slice': True}

    def workspace(self, state: JsonObject) -> Path:
        return self.root / text(state['project_id'])
