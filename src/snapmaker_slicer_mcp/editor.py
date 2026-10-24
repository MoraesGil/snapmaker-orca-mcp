"""Compatibility editor facade composed with concrete 3MF adapters."""
from __future__ import annotations
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol
from .application.services.projects import ProjectService
from .application.contracts.projects import ProjectSlicer
from .adapters.orca.editing import NativeHost
from .bootstrap import project_parts
from .presets import Catalog
from . import presets
from .engine import Engine

class EditorHost(ProjectSlicer, NativeHost, Protocol):
    @property
    def root(self) -> Path: ...
    @property
    def engine(self) -> Engine: ...

class Editor(ProjectService):
    catalog: Catalog

    def __init__(self, slicer: EditorHost, preset_dirs: Iterable[str | Path] = ()):
        parts = project_parts(slicer, slicer.root, preset_dirs)
        self.root = parts.revisions.root
        super().__init__(slicer, parts.revisions, parts.catalog, parts.native, presets._schema_doc())
