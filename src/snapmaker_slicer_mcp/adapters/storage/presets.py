# Portions derived from Max Ellis, orcaslicer-mcp@ccfacb43c2fd5a23973bfdabce89c74b2f6933fe.
# SPDX-License-Identifier: AGPL-3.0-only
"""Filesystem-backed read-only preset discovery and atomic draft publication."""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
from pathlib import Path
from ...domain.types.json import JsonObject, json_object
from ...domain.settings.normalization import _TYPES, _IDS, _NATIVE_TYPES
from ...domain.settings.values import text
from ...application.contracts.presets import PresetRecord

class FilesystemPresetRepository:
    def __init__(self, resources: str | Path, extra_dirs: list[Path], drafts: Path):
        self.extra_dirs = list(dict.fromkeys(Path(path).expanduser().resolve() for path in extra_dirs))
        self.drafts = Path(drafts).expanduser().resolve()
        self.system = Path(resources).resolve() / "profiles" / "Snapmaker"
        for source in [Path(resources).resolve(), *self.extra_dirs]:
            if self.drafts.is_relative_to(source) or source.is_relative_to(self.drafts):
                raise ValueError("Drafts cannot overlap read-only resources or preset sources")

    def records(self) -> list[PresetRecord]:
        records: list[PresetRecord] = []
        seen: set[Path] = set()
        roots = [(root, "user") for root in self.extra_dirs] + [(self.system, "system"), (self.drafts, "draft")]
        for root, source in roots:
            if not root.exists():
                continue
            if not root.is_dir():
                raise ValueError(f"Preset source is not a directory: {root}")
            for path in sorted(root.rglob("*.json")):
                canonical = path.resolve()
                if canonical in seen:
                    continue
                seen.add(canonical)
                try:
                    data = path.read_bytes()
                    loaded: object = json.loads(data.decode("utf-8-sig"))
                except (OSError, ValueError) as error:
                    raise ValueError(f"Cannot read preset JSON: {path}") from error
                if not isinstance(loaded, dict) or not isinstance(loaded.get("name"), str):
                    continue
                doc = json_object(loaded)
                if doc.get("type") is not None and doc.get("type") not in _TYPES:
                    continue  # machine_model descriptors and bundles are not presets.
                kind = _TYPES.get(text(doc["type"])) if isinstance(doc.get("type"), str) else None
                if kind is None:
                    for candidate, id_key in _IDS.items():
                        if id_key in doc:
                            kind = candidate
                            break
                if kind is None:
                    kind = next((_TYPES[part] for part in reversed(path.relative_to(root).parts[:-1]) if part in _TYPES), None)
                if kind is None:
                    continue
                records.append({"name": text(doc["name"]), "type": kind, "source": source, "path": str(canonical),
                                "sha256": hashlib.sha256(data).hexdigest(), "raw": doc,
                                "read_only": source != "draft" or path.is_symlink() or not canonical.is_relative_to(self.drafts)})
        return records

    def draft_path(self, kind: str, name: str) -> str:
        path = self.drafts / _NATIVE_TYPES[kind] / (hashlib.sha256(name.encode()).hexdigest() + ".json")
        if self.drafts.resolve() != self.drafts or not path.parent.resolve().is_relative_to(self.drafts):
            raise ValueError("Draft path is a symlink outside the draft directory")
        return str(path)

    def write(self, location: str, doc: JsonObject, previous: PresetRecord | None = None) -> None:
        path = Path(location)
        if path.is_symlink() or not path.resolve().is_relative_to(self.drafts):
            raise ValueError("Refusing symlink or read-only draft path")
        if previous is not None and hashlib.sha256(path.read_bytes()).hexdigest() != previous["sha256"]:
            raise ValueError("Draft changed during editing; read it again")
        if previous is None and path.exists():
            raise ValueError("Draft path already exists")
        data = (json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".preset-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def remove(self, rec: PresetRecord, rollback_path: str | None = None, action: str = 'delete') -> None:
        path = Path(rec['path'])
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != rec['sha256']:
            if rollback_path is not None:
                Path(rollback_path).unlink()
            raise ValueError(f'Draft changed during {action}; read it again')
        path.unlink()
