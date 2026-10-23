# Portions derived from Max Ellis, orcaslicer-mcp@ccfacb43c2fd5a23973bfdabce89c74b2f6933fe.
# SPDX-License-Identifier: AGPL-3.0-only
"""Compatibility preset facade and default filesystem composition."""
from __future__ import annotations
import copy
import functools
import json
from importlib.resources import files
from pathlib import Path
from typing import Protocol
from .domain.types.json import JsonObject, JsonValue, json_object
from .domain.settings.normalization import (
    _redact as _redact, _transport as _transport, _VECTORS as _VECTORS,
    normalize_filament_profile as normalize_profile,
    validate_changes as normalize_changes,
)
from .application.contracts.presets import EngineIdentity
from .application.services.presets import PresetService
from .adapters.storage.presets import FilesystemPresetRepository

class PresetEngine(EngineIdentity, Protocol):
    @property
    def resources(self) -> Path: ...

@functools.lru_cache(maxsize=1)
def _schema_doc() -> JsonObject:
    return json_object(json.loads(files('snapmaker_slicer_mcp').joinpath('data/settings.json').read_text(encoding='utf-8')))

def load_schema() -> dict[str, JsonObject]:
    return copy.deepcopy({key: json_object(value) for key, value in _schema_doc().items() if not key.startswith('_')})

def normalize_filament_profile(config: JsonObject) -> JsonObject:
    return normalize_profile(config, _schema_doc())

def validate_changes(changes: object, current: object, allow_custom_gcode: bool = False) -> JsonObject:
    return normalize_changes(changes, current, _schema_doc(), allow_custom_gcode)

class Catalog(PresetService):
    def __init__(self, engine: PresetEngine, extra_dirs: list[Path], drafts: Path):
        repository = FilesystemPresetRepository(engine.resources, extra_dirs, drafts)
        self.extra_dirs = repository.extra_dirs
        self.drafts = repository.drafts
        self.system = repository.system
        super().__init__(engine, repository, _schema_doc())
