"""Slice orchestration through engine, artifact and process capabilities."""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable

from ...domain.types.json import JsonObject
from ..contracts.slicing import ArtifactRepository, NativeRunner, SliceEngine, SliceRequest
from .admission import NativeAdmission


class Slicing:
    def __init__(self, engine: SliceEngine, artifacts: ArtifactRepository, runner: NativeRunner,
                 admission: NativeAdmission, clock: Callable[[], float] = time.perf_counter) -> None:
        self.engine, self.artifacts, self.runner = engine, artifacts, runner
        self.admission, self.clock = admission, clock

    def slice_3mf(self, input_3mf: str, plate: int = 0, force: bool = False,
                  allow_newer_file: bool = True, *, timeout: float = 1800) -> JsonObject:
        request = SliceRequest(plate, force, allow_newer_file)
        started = self.clock()
        source = self.artifacts.prepare_source(input_3mf)
        identity = self.engine.identity()
        key = hashlib.sha256(json.dumps({'contract': 1, 'input': source.sha256,
            'plate': plate, 'allow_newer_file': allow_newer_file, 'engine': identity},
            sort_keys=True).encode()).hexdigest()
        with self.admission.session():
            self.engine.verify()
            cached = None if force else self.artifacts.cached(key, request, source, identity)
            if cached is not None:
                return {**cached, 'cached': True, 'request_elapsed_seconds': self.clock() - started,
                        'engine_status': self.engine.status()}
            workspace = self.artifacts.create(source)
            self.engine.verify()
            native_seconds = self.runner.run(workspace, request, identity, timeout)
            self.engine.verify()
            return self.artifacts.publish(workspace, key, request, identity, self.engine.status(),
                                          native_seconds, self.clock() - started)
