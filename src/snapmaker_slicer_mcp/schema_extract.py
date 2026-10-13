# Copyright (c) 2026 Max Ellis and Snapmaker Slicer MCP contributors.
# SPDX-License-Identifier: AGPL-3.0-only
# Adapted from MaxEllis/orcaslicer-mcp, commit ccfacb43c2fd5a23973bfdabce89c74b2f6933fe.
"""Extract setting documentation, not compiled runtime defaults, from native source.

Regenerate explicitly with --source-commit; the wrapper's Git HEAD is not the
native engine's commit. No C++ expressions are evaluated by this extractor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


_ADD_RE = re.compile(r'def\s*=\s*this->add\(\s*"([^"]+)"\s*,\s*(co\w+)\s*\)')
_BOUND_RE = re.compile(r'def\s*=\s*this->add(?:_nullable)?\(')
_STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_FIELD_RE = re.compile(r'def->(\w+)\s*=\s*(.*)', re.DOTALL)
_DEFAULT_RE = re.compile(r'set_default_value\(\s*new\s+\w+(?:<[^;]+?>)?\s*(?:\((.*)\)|\{(.*)\})\s*\)\s*$', re.DOTALL)
_ALIAS_RE = re.compile(r'auto\s+(\w+)\s*=\s*def\s*=\s*this->add\(\s*"([^"]+)"')
_ENUM_COPY_RE = re.compile(r'def->enum_(values|labels)\s*=\s*(\w+)->enum_(?:values|labels)')
_NULLABLE_LOOP_RE = re.compile(
    r'for\s*\(\s*const char\s*\*\s*opt_key\s*:\s*\{(.*?)\}\s*\)\s*\{'
    r'[^{}]*?this->add_nullable\(\s*std::string\("([^"]+)"\)\s*\+\s*opt_key', re.DOTALL)
_STRING_FIELDS = {"label": "label", "full_label": "full_label", "category": "category",
                  "tooltip": "tooltip", "sidetext": "unit"}


def _unescape(value: str) -> str:
    return (value.replace('\\"', '"').replace("\\n", "\n")
            .replace("\\t", " ").replace("\\\\", "\\"))


def _join_strings(statement: str) -> str | None:
    parts = _STR_RE.findall(statement)
    return _unescape("".join(parts)).strip() if parts else None


def _strip_comments(source: str) -> str:
    """Remove line/block comments without treating URLs inside strings as comments."""
    out, in_string, escape, i = [], False, False, 0
    while i < len(source):
        ch = source[i]
        if escape:
            out.append(ch)
            escape = False
        elif in_string and ch == "\\":
            out.append(ch)
            escape = True
        elif ch == '"':
            in_string = not in_string
            out.append(ch)
        elif not in_string and source.startswith("//", i):
            end = source.find("\n", i)
            i = len(source) if end < 0 else end
            out.append("\n")
            continue
        elif not in_string and source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                raise ValueError("Unterminated C++ block comment")
            out.append(" " + "\n" * source[i:end + 2].count("\n"))
            i = end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _split_statements(block: str) -> list[str]:
    out, buf, in_string, escape = [], [], False, False
    for ch in block:
        if escape:
            buf.append(ch)
            escape = False
        elif in_string and ch == "\\":
            buf.append(ch)
            escape = True
        elif ch == '"':
            in_string = not in_string
            buf.append(ch)
        elif ch == ";" and not in_string:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        out.append("".join(buf))
    return out


def _num(raw: str):
    raw = raw.strip()
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw or None


def _expand_axes(text: str) -> str:
    """Unroll the literal native AxisDefault table without evaluating C++."""
    table = re.search(r'std::vector<AxisDefault>\s+axes\s*\{(.*?)\};', text, re.DOTALL)
    loop = re.search(r'for\s*\(\s*const AxisDefault\s*&\s*axis\s*:\s*axes\s*\)\s*\{(.*?)^\s*\}',
                     text, re.DOTALL | re.MULTILINE)
    if not loop:
        return text
    if not table:
        raise ValueError("Native axis loop has no supported literal table")
    rows = re.findall(r'\{\s*"([^"\\]+)"\s*,\s*\{([^{}]*)\}\s*,\s*\{([^{}]*)\}\s*,\s*\{([^{}]*)\}\s*\}', table.group(1))
    if not rows:
        raise ValueError("Cannot extract native machine axes")
    expanded = []
    for name, feedrate, acceleration, jerk in rows:
        body = re.sub(r'"([^"\n]+)"\s*\+\s*axis\.name', lambda m: json.dumps(m.group(1) + name), loop.group(1))
        body = body.replace("axis.max_feedrate", feedrate).replace("axis.max_acceleration", acceleration).replace("axis.max_jerk", jerk)
        body = re.sub(r'\(boost::format\("([^"\n]+)"\)\s*%\s*axis_upper\)\.str\(\)',
                      lambda m: json.dumps(m.group(1).replace("%1%", name.upper())), body)
        expanded.append(body)
    return text[:loop.start()] + "\n".join(expanded) + text[loop.end():]


def parse_print_config(text: str, constants: dict | None = None,
                       project_schema: tuple[str, int] | None = None) -> tuple[dict[str, dict], list[str]]:
    """Parse native setting records; CLI flags and commented definitions are excluded."""
    text = _expand_axes(_strip_comments(text).split("CLIActionsConfigDef::", 1)[0])
    constants = dict(constants or {})
    constants.update({key: _num(value) for key, value in re.findall(
        r'\bconst\s+(?:int|float|double)\s+(\w+)\s*=\s*([-+0-9.eE]+)\s*;', text)})
    if project_schema:
        text = text.replace("this->add(project_schema.config_key,", "this->add(" + json.dumps(project_schema[0]) + ",")
        text = text.replace("project_schema.current_version", str(project_schema[1]))
    create = list(_ADD_RE.finditer(text))
    bounds = sorted(m.start() for m in _BOUND_RE.finditer(text))
    aliases = {m.group(1): m.group(2) for m in _ALIAS_RE.finditer(text)}
    settings: dict[str, dict] = {}
    for match in create:
        key, ctype = match.group(1), match.group(2)
        end = next((bound for bound in bounds if bound > match.start()), len(text))
        rec = {"label": None, "category": None, "tooltip": None, "unit": None,
               "type": ctype, "min": None, "max": None, "mode": None,
               "enum_values": None, "enum_labels": None, "default": None, "nullable": False}
        for statement in _split_statements(text[match.end():end]):
            statement = statement.strip()
            if statement.startswith(("def->enum_values.push_back", "def->enum_values.emplace_back")):
                value = _join_strings(statement)
                if value is None:
                    symbol = re.search(r'\(\s*(\w+)\s*\)', statement)
                    value = constants.get(symbol.group(1)) if symbol else None
                if value is not None:
                    rec["enum_values"] = (rec["enum_values"] or []) + [value]
                continue
            if statement.startswith(("def->enum_labels.push_back", "def->enum_labels.emplace_back")):
                value = _join_strings(statement)
                if value is not None:
                    rec["enum_labels"] = (rec["enum_labels"] or []) + [value]
                continue
            copied = _ENUM_COPY_RE.match(statement)
            if copied:
                rec["_copy_%s_from" % copied.group(1)] = copied.group(2)
                continue
            field = _FIELD_RE.match(statement)
            if field:
                name, rhs = field.group(1), field.group(2)
                if name in _STRING_FIELDS:
                    value = _join_strings(statement)
                    if value is not None:
                        rec[_STRING_FIELDS[name]] = value
                elif name in ("min", "max"):
                    rec[name] = constants.get(rhs.strip(), _num(rhs))
                elif name == "mode":
                    rec[name] = rhs.strip() or None
                continue
            default = _DEFAULT_RE.search(statement)
            if default:
                rec["default"] = (default.group(1) or default.group(2) or "").strip() or None
        settings.setdefault(key, rec)
    for rec in settings.values():
        for which in ("values", "labels"):
            alias = rec.pop("_copy_%s_from" % which, None)
            source = settings.get(aliases.get(alias, ""))
            if source and source.get("enum_%s" % which) is not None:
                rec["enum_%s" % which] = list(source["enum_%s" % which])
    # The native filament overrides are created dynamically, not named add() calls.
    for loop in _NULLABLE_LOOP_RE.finditer(text):
        for key in _STR_RE.findall(loop.group(1)):
            if key not in settings:
                raise ValueError(f"Missing native base setting for nullable {key}")
            settings[loop.group(2) + key] = {**settings[key], "nullable": True}
    unparsed = [key for key, rec in settings.items() if not rec["label"] and not rec["tooltip"]]
    return settings, unparsed


def generate_schema(source: Path, source_commit: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("Specify the full native engine source commit")
    source = source.resolve()
    root = source.parents[2]
    inputs = [source, source.with_suffix(".hpp"), source.with_name("ProjectSchemaVersion.cpp"), root / "version.inc"]
    dependencies = {}
    contents = {}
    for path in inputs:
        data = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        expected = subprocess.run(["git", "-C", str(root), "rev-parse", f"{source_commit}:{relative}"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        measured = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if measured != expected:
            raise ValueError(f"Source does not match declared native commit: {relative}")
        dependencies[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "git_blob": measured}
        contents[path.name] = data.decode("utf-8-sig")
    constants = dict(re.findall(r'constexpr\s+const char\s*\*\s*(\w+)\s*=\s*"([^"\\]+)"', contents["PrintConfig.hpp"]))
    registry = re.search(r'constexpr ProjectSchemaDefinition\s+PROJECT_SCHEMA\s*\{\s*"([^"\\]+)"\s*,\s*(\d+)',
                         contents["ProjectSchemaVersion.cpp"])
    if registry is None:
        raise ValueError("Cannot extract native project schema registry")
    version = re.search(r'set\(Snapmaker_VERSION\s+"([^"\\]+)"\)', contents["version.inc"])
    settings, unparsed = parse_print_config(contents[source.name], constants, (registry.group(1), int(registry.group(2))))
    flow_keys = {}
    native_text = _strip_comments(contents[source.name])
    for domain, function in (('filament', 'filament'), ('process', 'process'), ('printer', 'machine')):
        definition = re.search(
            rf'\b{function}_flow_variant_options\(\)\s*\{{[^{{}}]*?'
            r'static const std::vector<std::string>\s+options\s*\{(.*?)\};', native_text, re.DOTALL)
        if definition is None:
            raise ValueError(f"Cannot extract native {domain} flow-variant keys")
        flow_keys[domain] = _STR_RE.findall(definition.group(1))
        for key in flow_keys[domain]:
            if key not in settings:
                raise ValueError(f"Unknown native {domain} flow-variant setting: {key}")
            settings[key]['flow_variant_domain'] = domain
    if len(settings) < 600:
        raise ValueError("Native schema unexpectedly contains fewer than 600 settings")
    return {"_meta": {
        "repository": "Snapmaker/OrcaSlicer", "source": "src/libslic3r/PrintConfig.cpp",
        "source_commit": source_commit, "source_sha256": dependencies["src/libslic3r/PrintConfig.cpp"]["sha256"],
        "source_dependencies": dependencies, "native_version": version.group(1) if version else None,
        "setting_count": len(settings), "unparsed_keys": sorted(unparsed),
        "flow_variant_keys": flow_keys,
        "scope": "PrintConfigDef source metadata; default expressions are not runtime values",
        "extractor_origin": "MaxEllis/orcaslicer-mcp@ccfacb43c2fd5a23973bfdabce89c74b2f6933fe",
    }, **dict(sorted(settings.items()))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    doc = generate_schema(args.src, args.source_commit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Extracted {doc['_meta']['setting_count']} native settings at {args.source_commit}")


if __name__ == "__main__":
    main()
