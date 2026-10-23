"""Compatibility editor facade composed with concrete 3MF adapters."""
from __future__ import annotations
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol
from .application.services.projects import ProjectService
from .application.contracts.projects import ProjectSlicer
from .adapters.storage.revisions import FilesystemRevisionRepository
from .adapters.orca.editing import NativeHost, OrcaNativeEditing
from .presets import Catalog
from . import presets
from .engine import Engine

class EditorHost(ProjectSlicer, NativeHost, Protocol):
    @property
    def root(self) -> Path: ...
    @property
    def engine(self) -> Engine: ...

class Editor(ProjectService):
    def __init__(self, slicer: EditorHost, preset_dirs: Iterable[str | Path] = ()):
        repository = FilesystemRevisionRepository(slicer.root / 'projects')
        self.root = repository.root
        catalog = Catalog(slicer.engine, [Path(path) for path in preset_dirs], slicer.root / 'preset-drafts')
        super().__init__(slicer, repository, catalog, OrcaNativeEditing(slicer, repository), presets._schema_doc())
