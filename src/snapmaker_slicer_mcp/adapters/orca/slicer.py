"""Compatibility host for the composed headless slicing application."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import ParamSpec, TypeVar

from ...application.contracts.artifacts import GcodeLines
from ...application.contracts.slicing import NativeRunner
from ...application.services.admission import NativeAdmission
from ...application.services.slicing import Slicing
from ...domain.entities.slicing import JobManifest
from ...domain.types.json import JsonObject
from ..storage.artifacts import FilesystemArtifacts
from .engine import Engine

P = ParamSpec('P')
T = TypeVar('T')


class Slicer:
    def __init__(self, engine: Engine, root: Path, timeout: float = 1800, *,
                 admission: NativeAdmission | None = None,
                 artifacts: FilesystemArtifacts | None = None,
                 runner: NativeRunner | None = None) -> None:
        from ...bootstrap import slicing_parts
        parts = slicing_parts(engine, root)
        self.engine, self.timeout = engine, timeout
        self.artifacts = artifacts if artifacts is not None else parts.artifacts
        self.root = self.artifacts.root
        self.admission = admission if admission is not None else parts.admission
        self.application = Slicing(engine, self.artifacts, runner if runner is not None else parts.runner, self.admission)

    @contextmanager
    def native_session(self) -> Iterator[None]:
        with self.admission.session():
            yield

    async def run_slice(self, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        return await self.admission.run(function, *args, **kwargs)

    def job(self, job_id: str) -> JobManifest:
        return self.artifacts.job(job_id)

    def _intact(self, result: JobManifest) -> bool:
        return self.artifacts.intact(result)

    def slice_3mf(self, input_3mf: str, plate: int = 0, force: bool = False,
                  allow_newer_file: bool = True) -> JsonObject:
        return self.application.slice_3mf(input_3mf, plate, force, allow_newer_file, timeout=self.timeout)

    def recover_3mf(self, gcode_file: str) -> JsonObject:
        return self.artifacts.recover_3mf(gcode_file)

    def read_lines(self, job_id: str, plate: int, start: int = 1, count: int = 200) -> GcodeLines:
        return self.artifacts.read_lines(job_id, plate, start, count)
