"""Persisted native slice records; file paths are descriptive, never authority."""
from typing import TypedDict

from ..types.json import JsonObject


class CompatibilityFlags(TypedDict):
    allow_newer_file: bool


class SourceRecord(TypedDict):
    path: str
    sha256: str


class PlateRecord(TypedDict):
    plate: int
    gcode_path: str
    sha256: str
    size_bytes: int
    gcode_lines_uri: str


class JobManifest(TypedDict):
    status: str
    job_id: str
    cached: bool
    cache_key: str
    requested_plate: int
    compatibility_flags: CompatibilityFlags
    source: SourceRecord
    engine: JsonObject
    engine_status: JsonObject
    plates: list[PlateRecord]
    props_path: str
    props_sha256: str
    manifest_uri: str
    props_uri: str
    log_path: str
    native_elapsed_seconds: float
    request_elapsed_seconds: float
