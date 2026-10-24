"""Narrow capabilities used by the native slicing use case."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...domain.types.json import JsonObject
from .presets import EngineIdentity


@dataclass(frozen=True)
class SliceRequest:
    plate: int = 0
    force: bool = False
    allow_newer_file: bool = True

    def __post_init__(self) -> None:
        if type(self.plate) is not int or self.plate < 0:
            raise ValueError('plate must be 0 (all) or a positive plate number')
        if type(self.force) is not bool:
            raise ValueError('force must be a boolean')
        if type(self.allow_newer_file) is not bool:
            raise ValueError('allow_newer_file must be a boolean')


@dataclass(frozen=True)
class SourceSnapshot:
    path: str
    sha256: str


@dataclass(frozen=True)
class SliceWorkspace:
    job_id: str
    directory: str
    source: SourceSnapshot


class SliceEngine(EngineIdentity, Protocol):
    def status(self) -> JsonObject: ...


class ArtifactRepository(Protocol):
    def prepare_source(self, input_3mf: str) -> SourceSnapshot: ...
    def cached(self, key: str, request: SliceRequest, source: SourceSnapshot,
               engine: JsonObject) -> JsonObject | None: ...
    def create(self, source: SourceSnapshot) -> SliceWorkspace: ...
    def publish(self, workspace: SliceWorkspace, key: str, request: SliceRequest,
                engine: JsonObject, engine_status: JsonObject, native_seconds: float,
                request_seconds: float) -> JsonObject: ...


class NativeRunner(Protocol):
    def run(self, workspace: SliceWorkspace, request: SliceRequest,
            engine: JsonObject, timeout: float) -> float: ...
