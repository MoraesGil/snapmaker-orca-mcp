# Portions derived from Max Ellis, orcaslicer-mcp@ccfacb43c2fd5a23973bfdabce89c74b2f6933fe.
# SPDX-License-Identifier: AGPL-3.0-only
"""Preset use cases over injected catalog storage and engine identity ports."""
from __future__ import annotations
import copy
import threading
from ...domain.types.json import JsonObject, JsonValue, json_object
from ...domain.settings.normalization import (_type, _redact, _transport, _placeholder, _gcode,
    _IDS, _METADATA, _NATIVE_TYPES, _IDENTITY, normalize_filament_profile, validate_changes)
from ...domain.settings.values import text, record
from ..contracts.presets import EngineIdentity, PresetRepository, PresetRecord

class PresetService:
    def __init__(self, engine: EngineIdentity, repository: PresetRepository, schema: JsonObject):
        self.engine = engine
        self.repository = repository
        self.schema = schema
        self.settings = {key: json_object(value) for key, value in schema.items() if not key.startswith('_')}
        self._lock = threading.RLock()

    def _schema_status(self) -> JsonObject:
        source_commit = record(self.schema["_meta"])["source_commit"]
        engine_commit = self.engine.identity()["source_commit"]
        return {"source_commit": source_commit, "engine_commit": engine_commit,
                "matches_engine": source_commit == engine_commit,
                "scope": "Source metadata and file inheritance; not runtime defaults or final region configuration"}

    def _records(self) -> list[PresetRecord]:
        self.engine.verify()
        result = self.repository.records()
        self.engine.verify()
        return result

    @staticmethod
    def _find(records: list[PresetRecord], kind: str, name: str) -> PresetRecord:
        hits = [rec for rec in records if rec["type"] == kind and rec["name"] == name]
        if len(hits) > 1:
            raise ValueError(f"Ambiguous preset name {name}: " + ", ".join(rec["path"] for rec in hits))
        if not hits:
            raise ValueError(f"Unknown preset or missing inheritance parent: {kind}/{name}")
        return hits[0]

    def _resolve(self, records: list[PresetRecord], rec: PresetRecord, trail: tuple[tuple[str, str], ...] = ()) -> tuple[JsonObject, list[JsonObject]]:
        marker = (rec["type"], rec["name"])
        if marker in trail:
            raise ValueError(f"Preset inheritance cycle at {rec['name']}")
        parent = rec["raw"].get("inherits", "")
        if not isinstance(parent, str):
            raise ValueError(f"Invalid inheritance parent for {rec['name']}")
        config: JsonObject = {}
        chain: list[JsonObject] = []
        if parent:
            config, chain = self._resolve(records, self._find(records, rec["type"], parent), (*trail, marker))
        config.update({key: copy.deepcopy(value) for key, value in rec["raw"].items() if key not in _METADATA})
        chain.append({key: rec[key] for key in ("name", "type", "source", "path", "sha256", "read_only")})
        return config, chain

    def _value(self, records: list[PresetRecord], rec: PresetRecord) -> JsonObject:
        config, chain = self._resolve(records, rec)
        id_key = _IDS[rec["type"]]
        if id_key not in rec["raw"]:
            config[id_key] = [rec["name"]] if rec["type"] == "filament" else rec["name"]
        return record(_redact({**{key: rec[key] for key in ("name", "type", "source", "path", "sha256", "read_only")},
                        "config": config, "raw_config": rec["raw"], "inherits": rec["raw"].get("inherits", ""),
                        "inheritance": [item for item in chain], "engine": self.engine.identity(), "schema": self._schema_status(),
                        "unknown_keys": [key for key in sorted(set(config) - set(self.settings))]}))

    def list_presets(self, type: str | None = None, include_system: bool = False) -> JsonObject:
        kind = _type(type) if type is not None else None
        records = self._records()
        result: list[JsonValue] = []
        for rec in records:
            if (kind is None or rec["type"] == kind) and (include_system or rec["source"] != "system"):
                item: JsonObject = {key: rec[key] for key in ("name", "type", "source", "path", "sha256", "read_only")}
                item["inherits"] = rec["raw"].get("inherits", "")
                item["ambiguous"] = sum(other["name"] == rec["name"] and other["type"] == rec["type"] for other in records) > 1
                result.append(item)
        return {"presets": result, "engine": self.engine.identity(), "schema": self._schema_status()}

    def get_preset_config(self, type: str, name: str) -> JsonObject:
        records = self._records()
        return self._value(records, self._find(records, _type(type), name))

    def preset_keys(self, type: str) -> set[str]:
        """Native Snapmaker group membership, including inherited resource fields."""
        kind = _type(type)
        records = self._records()
        keys = {_IDS[kind]}
        for rec in records:
            if rec["source"] == "system" and rec["type"] == kind:
                keys.update(key for key in rec["raw"] if key not in _METADATA and key in self.settings)
        return keys

    def describe_setting(self, key: str) -> JsonObject | None:
        self.engine.verify()
        rec = self.settings.get(key)
        return {"key": key, **copy.deepcopy(rec), "schema": self._schema_status()} if rec is not None else None

    def search_settings(self, query: str, limit: int = 25) -> JsonObject:
        self.engine.verify()
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Setting search limit must be 1..100")
        q = query.lower().strip()
        hits = []
        words = [word for word in q.replace("_", " ").split() if len(word) > 2]
        for key, rec in self.settings.items():
            fields = [key.lower(), text(rec.get("label") or "").lower(), text(rec.get("tooltip") or "").lower()]
            rank = next((index for index, field in enumerate(fields) if q and q in field), 3)
            score = sum(3 if word in fields[0] else 2 if word in fields[1] else 1 if word in fields[2] else 0 for word in words)
            if q and (rank < 3 or score):
                hits.append(((-1 if key.lower() == q else rank, -score, key),
                             {"key": key, "label": rec.get("label"), "category": rec.get("category"),
                              "tooltip": text(rec.get("tooltip") or "")[:160]}))
        hits.sort(key=lambda item: item[0])
        return {"settings": [item[1] for item in hits[:limit]], "schema": self._schema_status()}

    @staticmethod
    def _name(name: str) -> str:
        if not isinstance(name, str) or not name.strip() or len(name) > 256 or any(ord(ch) < 32 for ch in name):
            raise ValueError("Preset name must be 1..256 printable characters")
        return name

    def _draft_path(self, kind: str, name: str) -> str:
        return self.repository.draft_path(kind, name)

    @staticmethod
    def _editable(rec: PresetRecord) -> None:
        if rec["source"] != "draft" or rec["read_only"]:
            raise ValueError("This preset is read-only; save_preset with another name in drafts")

    def _write(self, path: str, doc: JsonObject, previous: PresetRecord | None = None) -> None:
        self.engine.verify()
        self.repository.write(path, doc, previous)

    def save_preset(self, type: str, name: str, config: JsonObject, detach: bool = False,
                    allow_custom_gcode: bool = False) -> JsonObject:
        with self._lock:
            kind, name = _type(type), self._name(name)
            if not isinstance(config, dict):
                raise ValueError("Preset configuration must be an object")
            records = self._records()
            hits = [rec for rec in records if rec["type"] == kind and rec["name"] == name]
            existing = self._find(records, kind, name) if hits else None
            if existing:
                self._editable(existing)
            raw = copy.deepcopy(existing["raw"]) if existing else {}
            parent = config.get("inherits", raw.get("inherits", ""))
            if not isinstance(parent, str):
                raise ValueError("Preset inherits must name one parent")
            baseline = self._resolve(records, existing)[0] if existing else {}
            parent_config: JsonObject = {}
            if parent:
                if parent == name:
                    raise ValueError("Preset inheritance cycle")
                parent_rec = self._find(records, kind, parent)
                parent_config, chain = self._resolve(records, parent_rec)
                if any(item["name"] == name for item in chain):
                    raise ValueError("Preset inheritance cycle")
                if not existing:
                    baseline = parent_config
                elif parent != raw.get("inherits", ""):
                    baseline = parent_config | {key: value for key, value in raw.items() if key not in _METADATA}
            allowed = self.preset_keys(kind)
            if kind == 'filament':
                baseline = normalize_filament_profile(baseline, self.schema)
            supplied = {key: value for key, value in config.items() if key not in _METADATA}
            # Refuse protected writes before filtering other preset groups.
            for key, value in supplied.items():
                if (key == "post_process" or _transport(key)) and (key == "post_process" or value != baseline.get(key)):
                    raise ValueError(f"{key}: prohibited preset write")
                if key not in self.settings:
                    raise ValueError(f"Unknown native setting: {key}")
                if _placeholder(value):
                    raise ValueError(f"{key}: a redacted placeholder cannot be written")
            ignored = sorted(set(supplied) - allowed)
            changes = {key: value for key, value in supplied.items() if key in allowed and value != baseline.get(key)}
            normalized = validate_changes(changes, baseline, self.schema, allow_custom_gcode)
            if detach:
                raw = {key: copy.deepcopy(value) for key, value in (baseline | normalized).items() if key in allowed}
                for key, value in raw.items():
                    if key == "post_process" and value not in ([], [""], ""):
                        raise ValueError("post_process: prohibited preset write")
                    if _transport(key) and key not in _IDENTITY and value not in ([], [""], "", None):
                        raise ValueError(f"{key}: transport cannot be copied into a detached draft")
                    if _gcode(key) and value not in ([], [""], "") and not allow_custom_gcode:
                        raise ValueError(f"{key}: detaching G-code requires allow_custom_gcode=True")
                parent = ""
            else:
                raw.update(normalized)
            # A named filament preset represents one material, not a project slot map.
            supplied_id = supplied.get(_IDS[kind])
            if kind == "filament" and isinstance(supplied_id, list) and len(supplied_id) != 1:
                raise ValueError("A filament preset ID must have one slot; project slots are not resized")
            raw.update({"name": name, "type": _NATIVE_TYPES[kind], "from": "User", "inherits": parent,
                        "version": raw.get("version", text(record(self.schema["_meta"]).get("native_version", "2.4.1")) + ".0"), "instantiation": "true",
                        _IDS[kind]: [name] if kind == "filament" else name})
            path = existing["path"] if existing else self._draft_path(kind, name)
            self._write(path, raw, existing)
            result = self.get_preset_config(kind, name)
            return {**result, "saved": True, "ignored_keys": [key for key in ignored]}

    def edit_preset(self, type: str, name: str, changes: JsonObject, allow_custom_gcode: bool = False) -> JsonObject:
        with self._lock:
            kind = _type(type)
            records = self._records()
            rec = self._find(records, kind, name)
            self._editable(rec)
            if not isinstance(changes, dict):
                raise ValueError("Preset changes must be an object")
            wrong = set(changes) - self.preset_keys(kind)
            if wrong:
                raise ValueError("Settings do not belong to this preset group/type: " + ", ".join(sorted(wrong)))
            return self.save_preset(kind, name, changes, allow_custom_gcode=allow_custom_gcode)

    @staticmethod
    def _unreferenced(records: list[PresetRecord], rec: PresetRecord) -> None:
        children = [item["name"] for item in records if item["type"] == rec["type"] and item["raw"].get("inherits") == rec["name"]]
        if children:
            raise ValueError("Preset is referenced by inheritance dependents: " + ", ".join(children))

    def rename_preset(self, type: str, name: str, new_name: str) -> JsonObject:
        with self._lock:
            kind, new_name = _type(type), self._name(new_name)
            records = self._records()
            rec = self._find(records, kind, name)
            self._editable(rec)
            if any(item["type"] == kind and item["name"] == new_name for item in records):
                raise ValueError("Preset name already exists; choose another name")
            self._unreferenced(records, rec)
            raw = copy.deepcopy(rec["raw"])
            raw.update({"name": new_name, _IDS[kind]: [new_name] if kind == "filament" else new_name})
            path = self._draft_path(kind, new_name)
            self._write(path, raw)
            self.repository.remove(rec, rollback_path=path, action='rename')
            return {**self.get_preset_config(kind, new_name), "renamed_from": name}

    def delete_preset(self, type: str, name: str) -> JsonObject:
        with self._lock:
            records = self._records()
            rec = self._find(records, _type(type), name)
            self._editable(rec)
            self._unreferenced(records, rec)
            path = rec['path']
            self.repository.remove(rec, action='delete')
            return {"name": name, "type": rec["type"], "deleted": True, "sha256": rec["sha256"], "path": str(path)}
