# Portions derived from Max Ellis, orcaslicer-mcp@ccfacb43c2fd5a23973bfdabce89c74b2f6933fe.
# SPDX-License-Identifier: AGPL-3.0-only
"""Read native/SOT presets; write only explicit artifact-local drafts.

Resolved configurations contain file inheritance, not synthesized engine defaults.
Project application and per-slot expansion belong to the 3MF editor, not here.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
import os
import re
import tempfile
import threading
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from pathlib import Path


_TYPES = {"print": "print", "process": "print", "printer": "printer", "machine": "printer", "filament": "filament"}
_NATIVE_TYPES = {"print": "process", "printer": "machine", "filament": "filament"}
_IDS = {"print": "print_settings_id", "printer": "printer_settings_id", "filament": "filament_settings_id"}
_METADATA = frozenset({"name", "type", "from", "version", "inherits", "instantiation", "description",
                       "setting_id", "filament_id", "renamed_from", "url", "is_custom_defined"})
_TRANSPORT = frozenset({"printer_technology", "bbl_use_printhost", "host_type", "printer_agent", "print_host",
                        "print_host_webui", "flashforge_serial_number", "printer_model", "filename_format"})
_CREDENTIALS = frozenset({"printhost_apikey", "printhost_user", "printhost_password"})
_IDENTITY = frozenset({"printer_model", "printer_technology"})
_URL_KEYS = frozenset({"print_host", "print_host_webui"})
_REDACTED = "<redacted>"
_VECTORS = frozenset({"coFloats", "coInts", "coBools", "coStrings", "coPercents", "coFloatsOrPercents", "coEnums", "coPoints"})
_SCALAR_TYPES = {"coFloats": "coFloat", "coInts": "coInt", "coBools": "coBool", "coStrings": "coString",
                 "coPercents": "coPercent", "coFloatsOrPercents": "coFloatOrPercent", "coEnums": "coEnum", "coPoints": "coPoint"}


@functools.lru_cache(maxsize=1)
def _schema_doc() -> dict:
    return json.loads(files("snapmaker_slicer_mcp").joinpath("data/settings.json").read_text(encoding="utf-8"))


def load_schema() -> dict:
    """Return independent key->metadata records from the pinned native source."""
    return copy.deepcopy({key: value for key, value in _schema_doc().items() if not key.startswith("_")})


def normalize_filament_profile(config: dict) -> dict:
    """Pad only native per-profile flow variants, as PresetBundle::full_config does.

This never resizes project material slots or mutates the source profile. Missing
values stay missing; no compiled defaults are invented.
"""
    result = copy.deepcopy(config)
    modes = result.get('filament_flow_support', ['standard'])
    if isinstance(modes, str):
        modes = [modes]
    if (not isinstance(modes, list) or not modes or any(not isinstance(mode, str) for mode in modes)
            or len(set(modes)) != len(modes)
            or any(mode not in _schema_doc()['filament_volume_type']['enum_values'] for mode in modes)):
        raise ValueError('Filament preset flow declarations are ambiguous')
    if 'filament_flow_support' in result:
        result['filament_flow_support'] = modes
    for key in _schema_doc()['_meta']['flow_variant_keys']['filament']:
        if key not in result:
            continue
        value = result[key]
        if not isinstance(value, list):
            value = value.split(',') if isinstance(value, str) else [value]
        if not value or len(value) > len(modes):
            raise ValueError(f'Filament preset vector {key} has ambiguous flow cardinality')
        result[key] = value + [value[0]] * (len(modes) - len(value))
    return result


def _type(value: str) -> str:
    if not isinstance(value, str) or value not in _TYPES:
        raise ValueError("Preset type must be print/process, filament, or printer/machine")
    return _TYPES[value]


def _transport(key: str) -> bool:
    return key in _TRANSPORT or key.startswith("printhost_")


def _gcode(key: str) -> bool:
    return key.endswith("_gcode") and key != "emit_machine_limits_to_gcode"


def _placeholder(value) -> bool:
    if isinstance(value, str):
        return _REDACTED in value
    if isinstance(value, (list, tuple)):
        return any(_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(_placeholder(item) for item in value.values())
    return False


def _redact(value, key: str = ""):
    """Handle native JSON arrays too; never return credentials in raw_config."""
    if key in _CREDENTIALS and value not in (None, "", []):
        return _REDACTED
    if isinstance(value, dict):
        return {name: _redact(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if key in _URL_KEYS and isinstance(value, str) and "@" in value:
        scheme = re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", value)
        return (scheme.group(0) if scheme else "") + _REDACTED + value[value.rfind("@"):]
    return value


def _number(value, key: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{key}: expected a finite number")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as error:
        raise ValueError(f"{key}: expected a finite number") from error
    if not number.is_finite() or not math.isfinite(float(number)):
        raise ValueError(f"{key}: expected a finite number")
    return number


def _scalar(value, rec: dict, key: str) -> str:
    kind = _SCALAR_TYPES.get(rec["type"], rec["type"])
    if value == "nil" and rec.get("nullable"):
        return "nil"
    if kind == "coBool":
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (str, int)) and str(value).lower() in ("0", "1", "true", "false"):
            return "1" if str(value).lower() in ("1", "true") else "0"
        raise ValueError(f"{key}: expected boolean or 0/1")
    if kind in ("coFloat", "coInt", "coPercent", "coFloatOrPercent"):
        percent = isinstance(value, str) and value.strip().endswith("%")
        if percent and kind not in ("coPercent", "coFloatOrPercent"):
            raise ValueError(f"{key}: percent is invalid for this type")
        number = _number(value.strip()[:-1] if percent else value, key)
        if kind == "coInt" and number != number.to_integral_value():
            raise ValueError(f"{key}: expected an integer")
        if kind == "coInt" and not -(2 ** 31) <= number < 2 ** 31:
            raise ValueError(f"{key}: outside native integer range")
        for field, compare in (("min", lambda n, limit: n < limit), ("max", lambda n, limit: n > limit)):
            limit = rec.get(field)
            if isinstance(limit, (int, float)) and math.isfinite(limit) and compare(number, Decimal(str(limit))):
                raise ValueError(f"{key}: outside native {field} {limit}")
        result = format(number, "f")
        if "." in result:
            result = result.rstrip("0").rstrip(".")
        return result + ("%" if percent or kind == "coPercent" else "")
    if kind in ("coString", "coEnum"):
        if not isinstance(value, str):
            raise ValueError(f"{key}: expected a string")
        if kind == "coEnum" and rec.get("enum_values") and value not in rec["enum_values"]:
            raise ValueError(f"{key}: not one of the native enum values")
        return value
    if kind in ("coPoint", "coPoint3"):
        dimensions = 3 if kind == "coPoint3" else 2
        parts = re.split(r"[x,]", value) if isinstance(value, str) else value
        if not isinstance(parts, (list, tuple)) or len(parts) != dimensions:
            raise ValueError(f"{key}: expected {dimensions} coordinates")
        coords = [format(_number(part, key), "f") for part in parts]
        return ("," if dimensions == 3 else "x").join(coords)
    raise ValueError(f"{key}: unsupported native setting type {kind}")


def validate_changes(changes: dict, current: dict, allow_custom_gcode: bool = False) -> dict:
    """Normalize native JSON strings/arrays without broadcasting or resizing slots.

This is an early type/range check, not Print::validate or a physical-print proof.
Unknown historical fields are preserved by editors but cannot acquire new values.
"""
    if not isinstance(changes, dict) or not isinstance(current, dict):
        raise ValueError("Changes and current configuration must be objects")
    schema = _schema_doc()
    normalized = {}
    for key, value in changes.items():
        if not isinstance(key, str) or key.startswith("_") or key not in schema:
            raise ValueError(f"Unknown native setting: {key}")
        if key == "post_process" or _transport(key):
            raise ValueError(f"{key}: writing scripts, transport, secrets, or printer identity is prohibited")
        if _gcode(key) and not allow_custom_gcode:
            raise ValueError(f"{key}: custom G-code requires allow_custom_gcode=True")
        if _placeholder(value):
            raise ValueError(f"{key}: a redacted placeholder cannot be written")
        rec = schema[key]
        if rec["type"] in _VECTORS:
            if not isinstance(value, list):
                raise ValueError(f"{key}: a vector requires an explicit array; slots are never broadcast")
            previous = current.get(key)
            if isinstance(previous, list) and previous and len(previous) != len(value):
                raise ValueError(f"{key}: array length/cardinality must preserve existing slots")
            normalized[key] = [_scalar(item, rec, key) for item in value]
        else:
            normalized[key] = _scalar(value, rec, key)
    return normalized


class Catalog:
    def __init__(self, engine, extra_dirs: list[Path], drafts: Path):
        self.engine = engine
        self.extra_dirs = list(dict.fromkeys(Path(path).expanduser().resolve() for path in extra_dirs))
        self.drafts = Path(drafts).expanduser().resolve()
        self.system = Path(engine.resources).resolve() / "profiles" / "Snapmaker"
        for source in [Path(engine.resources).resolve(), *self.extra_dirs]:
            if self.drafts.is_relative_to(source) or source.is_relative_to(self.drafts):
                raise ValueError("Drafts cannot overlap read-only resources or preset sources")
        self.settings = load_schema()
        self._lock = threading.RLock()

    def _schema_status(self) -> dict:
        source_commit = _schema_doc()["_meta"]["source_commit"]
        engine_commit = self.engine.identity()["source_commit"]
        return {"source_commit": source_commit, "engine_commit": engine_commit,
                "matches_engine": source_commit == engine_commit,
                "scope": "Source metadata and file inheritance; not runtime defaults or final region configuration"}

    def _records(self) -> list[dict]:
        self.engine.verify()
        records, seen = [], set()
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
                    doc = json.loads(data.decode("utf-8-sig"))
                except (OSError, ValueError) as error:
                    raise ValueError(f"Cannot read preset JSON: {path}") from error
                if not isinstance(doc, dict) or not isinstance(doc.get("name"), str):
                    continue
                if doc.get("type") is not None and doc.get("type") not in _TYPES:
                    continue  # machine_model descriptors and bundles are not presets.
                kind = _TYPES.get(doc.get("type"))
                if kind is None:
                    for candidate, id_key in _IDS.items():
                        if id_key in doc:
                            kind = candidate
                            break
                if kind is None:
                    kind = next((_TYPES[part] for part in reversed(path.relative_to(root).parts[:-1]) if part in _TYPES), None)
                if kind is None:
                    continue
                records.append({"name": doc["name"], "type": kind, "source": source, "path": str(canonical),
                                "sha256": hashlib.sha256(data).hexdigest(), "raw": doc,
                                "read_only": source != "draft" or path.is_symlink() or not canonical.is_relative_to(self.drafts)})
        self.engine.verify()
        return records

    @staticmethod
    def _find(records: list[dict], kind: str, name: str) -> dict:
        hits = [rec for rec in records if rec["type"] == kind and rec["name"] == name]
        if len(hits) > 1:
            raise ValueError(f"Ambiguous preset name {name}: " + ", ".join(rec["path"] for rec in hits))
        if not hits:
            raise ValueError(f"Unknown preset or missing inheritance parent: {kind}/{name}")
        return hits[0]

    def _resolve(self, records: list[dict], rec: dict, trail: tuple = ()) -> tuple[dict, list[dict]]:
        marker = (rec["type"], rec["name"])
        if marker in trail:
            raise ValueError(f"Preset inheritance cycle at {rec['name']}")
        parent = rec["raw"].get("inherits", "")
        if not isinstance(parent, str):
            raise ValueError(f"Invalid inheritance parent for {rec['name']}")
        config, chain = {}, []
        if parent:
            config, chain = self._resolve(records, self._find(records, rec["type"], parent), (*trail, marker))
        config.update({key: copy.deepcopy(value) for key, value in rec["raw"].items() if key not in _METADATA})
        chain.append({key: rec[key] for key in ("name", "type", "source", "path", "sha256", "read_only")})
        return config, chain

    def _value(self, records: list[dict], rec: dict) -> dict:
        config, chain = self._resolve(records, rec)
        id_key = _IDS[rec["type"]]
        if id_key not in rec["raw"]:
            config[id_key] = [rec["name"]] if rec["type"] == "filament" else rec["name"]
        return _redact({**{key: rec[key] for key in ("name", "type", "source", "path", "sha256", "read_only")},
                        "config": config, "raw_config": rec["raw"], "inherits": rec["raw"].get("inherits", ""),
                        "inheritance": chain, "engine": self.engine.identity(), "schema": self._schema_status(),
                        "unknown_keys": sorted(set(config) - set(self.settings))})

    def list_presets(self, type: str | None = None, include_system: bool = False) -> dict:
        kind = _type(type) if type is not None else None
        records = self._records()
        result = []
        for rec in records:
            if (kind is None or rec["type"] == kind) and (include_system or rec["source"] != "system"):
                item = {key: rec[key] for key in ("name", "type", "source", "path", "sha256", "read_only")}
                item["inherits"] = rec["raw"].get("inherits", "")
                item["ambiguous"] = sum(other["name"] == rec["name"] and other["type"] == rec["type"] for other in records) > 1
                result.append(item)
        return {"presets": result, "engine": self.engine.identity(), "schema": self._schema_status()}

    def get_preset_config(self, type: str, name: str) -> dict:
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

    def describe_setting(self, key: str) -> dict | None:
        self.engine.verify()
        rec = self.settings.get(key)
        return {"key": key, **copy.deepcopy(rec), "schema": self._schema_status()} if rec is not None else None

    def search_settings(self, query: str, limit: int = 25) -> dict:
        self.engine.verify()
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("Setting search limit must be 1..100")
        q = query.lower().strip()
        hits = []
        words = [word for word in q.replace("_", " ").split() if len(word) > 2]
        for key, rec in self.settings.items():
            fields = [key.lower(), (rec.get("label") or "").lower(), (rec.get("tooltip") or "").lower()]
            rank = next((index for index, field in enumerate(fields) if q and q in field), 3)
            score = sum(3 if word in fields[0] else 2 if word in fields[1] else 1 if word in fields[2] else 0 for word in words)
            if q and (rank < 3 or score):
                hits.append(((-1 if key.lower() == q else rank, -score, key),
                             {"key": key, "label": rec.get("label"), "category": rec.get("category"),
                              "tooltip": (rec.get("tooltip") or "")[:160]}))
        hits.sort(key=lambda item: item[0])
        return {"settings": [item[1] for item in hits[:limit]], "schema": self._schema_status()}

    @staticmethod
    def _name(name: str) -> str:
        if not isinstance(name, str) or not name.strip() or len(name) > 256 or any(ord(ch) < 32 for ch in name):
            raise ValueError("Preset name must be 1..256 printable characters")
        return name

    def _draft_path(self, kind: str, name: str) -> Path:
        path = self.drafts / _NATIVE_TYPES[kind] / (hashlib.sha256(name.encode()).hexdigest() + ".json")
        if self.drafts.resolve() != self.drafts or not path.parent.resolve().is_relative_to(self.drafts):
            raise ValueError("Draft path is a symlink outside the draft directory")
        return path

    @staticmethod
    def _editable(rec: dict) -> None:
        if rec["source"] != "draft" or rec["read_only"]:
            raise ValueError("This preset is read-only; save_preset with another name in drafts")

    def _write(self, path: Path, doc: dict, previous: dict | None = None) -> None:
        self.engine.verify()
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

    def save_preset(self, type: str, name: str, config: dict, detach: bool = False,
                    allow_custom_gcode: bool = False) -> dict:
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
            parent_config = {}
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
                baseline = normalize_filament_profile(baseline)
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
            normalized = validate_changes(changes, baseline, allow_custom_gcode)
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
                        "version": raw.get("version", _schema_doc()["_meta"].get("native_version", "2.4.1") + ".0"), "instantiation": "true",
                        _IDS[kind]: [name] if kind == "filament" else name})
            path = Path(existing["path"]) if existing else self._draft_path(kind, name)
            self._write(path, raw, existing)
            result = self.get_preset_config(kind, name)
            return {**result, "saved": True, "ignored_keys": ignored}

    def edit_preset(self, type: str, name: str, changes: dict, allow_custom_gcode: bool = False) -> dict:
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
    def _unreferenced(records: list[dict], rec: dict) -> None:
        children = [item["name"] for item in records if item["type"] == rec["type"] and item["raw"].get("inherits") == rec["name"]]
        if children:
            raise ValueError("Preset is referenced by inheritance dependents: " + ", ".join(children))

    def rename_preset(self, type: str, name: str, new_name: str) -> dict:
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
            old = Path(rec["path"])
            if old.is_symlink() or hashlib.sha256(old.read_bytes()).hexdigest() != rec["sha256"]:
                path.unlink()
                raise ValueError("Draft changed during rename; read it again")
            old.unlink()
            return {**self.get_preset_config(kind, new_name), "renamed_from": name}

    def delete_preset(self, type: str, name: str) -> dict:
        with self._lock:
            records = self._records()
            rec = self._find(records, _type(type), name)
            self._editable(rec)
            self._unreferenced(records, rec)
            path = Path(rec["path"])
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != rec["sha256"]:
                raise ValueError("Draft changed during delete; read it again")
            path.unlink()
            return {"name": name, "type": rec["type"], "deleted": True, "sha256": rec["sha256"], "path": str(path)}
