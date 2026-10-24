"""Bind an operator-selected official build; updates are notices, never installs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.request import Request, urlopen

from ...domain.types.json import JsonObject, JsonValue, json_object


def _text(manifest: Mapping[str, JsonValue], key: str) -> str:
    value = manifest[key]
    if not isinstance(value, str):
        raise ValueError(f"Engine manifest {key} must be a string")
    return value


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(bytes.fromhex(file_hash(path)))
    return digest.hexdigest()


def package_resources(executable: Path) -> Path:
    """Mirror CLI::setup for standard upstream bundles (not custom FHS builds)."""
    return _package_resources(executable, sys.platform, os.name)


def _package_resources(executable: Path, platform: str, operating_system: str) -> Path:
    if platform == "darwin":
        return executable.parent.parent / "Resources"
    if operating_system == "nt":
        return executable.parent / "resources"
    path = executable.parent.parent / "resources"
    return path if path.exists() else executable.parent.parent.parent / "resources"


class Engine:
    repository = "Snapmaker/OrcaSlicer"

    @classmethod
    def bind(cls, executable: Path, source_commit: str, resources: Path) -> JsonObject:
        executable, resources = executable.resolve(), resources.resolve()
        if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
            raise ValueError("Specify the actual 40-character upstream main build commit")
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("Native Snapmaker Orca executable is unavailable")
        if not resources.is_dir():
            raise ValueError("The matching native resources directory is unavailable")
        if resources != package_resources(executable).resolve():
            raise ValueError("Declared resources do not match the native package layout; custom FHS builds are unsupported")
        return {
            "repository": cls.repository, "source_ref": "main", "source_commit": source_commit,
            "executable": str(executable), "executable_sha256": file_hash(executable),
            "resources": str(resources), "resources_sha256": tree_hash(resources),
            "provenance": "operator-declared source commit; hashes measured locally",
        }

    def __init__(self, manifest: Mapping[str, JsonValue]) -> None:
        self.manifest: JsonObject = dict(manifest)
        if manifest.get("repository") != self.repository or manifest.get("source_ref") != "main":
            raise ValueError("Only an official Snapmaker/OrcaSlicer main build can be bound")
        self.executable = Path(_text(manifest, "executable")).resolve()
        self.resources = Path(_text(manifest, "resources")).resolve()
        before = self._asset_signature()
        measured = self.bind(self.executable, _text(manifest, "source_commit"), self.resources)
        for key in ("executable_sha256", "resources_sha256"):
            if measured[key] != manifest.get(key):
                raise ValueError(f"Engine {key} changed; bind and validate the new build explicitly")
        self._signature = self._asset_signature()
        if before != self._signature:
            raise ValueError("Engine assets changed during verification; bind a stable build explicitly")
        self._upstream: str | None = None
        self._check = "not_checked"

    def _asset_signature(self) -> tuple[tuple[str, int, int, int, int], ...]:
        files = [self.executable, *sorted(p for p in self.resources.rglob("*") if p.is_file())]
        records = []
        for path in files:
            stat = path.stat()
            records.append((str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        return tuple(records)

    def verify(self) -> None:
        """Detect edits to the already hashed engine before reusing or slicing."""
        try:
            intact = (package_resources(self.executable).resolve() == self.resources
                      and self._asset_signature() == self._signature)
        except OSError:
            intact = False
        if not intact:
            raise ValueError("Engine assets changed; bind and restart with the new build explicitly")

    def identity(self) -> JsonObject:
        return {k: self.manifest[k] for k in (
            "repository", "source_ref", "source_commit", "executable_sha256", "resources_sha256"
        )}

    def _remote_head(self) -> str:
        request = Request(
            f"https://api.github.com/repos/{self.repository}/commits/main",
            headers={"User-Agent": "snapmaker-slicer-mcp", "Accept": "application/vnd.github+json"},
        )
        with urlopen(request, timeout=3) as response:
            data: object = json.load(response)
        commit = json_object(data, "Upstream response")["sha"]
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Invalid upstream commit response")
        return commit

    def status(self, check_remote: bool = False) -> JsonObject:
        self.verify()
        if check_remote:
            try:
                self._upstream = self._remote_head()
                self._check = "checked"
            except (OSError, ValueError, KeyError):
                self._check = "unavailable"
        available = (self._upstream != self.manifest["source_commit"]) if self._upstream else None
        notices: list[JsonValue] = []
        if available:
            notices.append("A newer upstream main exists. Build/install and bind it explicitly to update the MCP engine; no automatic update was performed.")
        if self._check == "unavailable":
            notices.append("Upstream update check is unavailable; the bound engine identity is unchanged.")
        return {**self.identity(), "upstream_commit": self._upstream,
                "update_available": available, "update_check": self._check, "notices": notices}
