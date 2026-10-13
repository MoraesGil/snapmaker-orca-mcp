"""Surgical native 3MF project edits, without mesh conversion or slicer re-export.

Object identifiers are scoped to this package. Geometry parts remain shared ZIP
payloads; modifying a project never grants permission to overwrite its source.
"""
from __future__ import annotations

import copy
import functools
import io
import json
import math
import posixpath
import re
import threading
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PRODUCTION = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
CONFIG = "Metadata/project_settings.config"
SETTINGS = "Metadata/model_settings.config"
RANGES = "Metadata/layer_config_ranges.xml"
PROFILE = "Metadata/layer_heights_profile.txt"
BRIM = "Metadata/brim_ear_points.txt"
CUT = "Metadata/cut_information.xml"
CUSTOM_GCODE = "Metadata/custom_gcode_per_layer.xml"
PARKED = "Metadata/slicer_mcp_parked_instances.xml"
IDENTITY = [1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0.]
_XML_LOCK = threading.RLock()
_SLICE_REFS = {
    "gcode_file", "gcode_file_md5", "thumbnail_file", "thumbnail_no_light_file",
    "top_file", "pick_file", "pattern_file", "pattern_bbox_file",
    "prediction", "weight", "is_sliced_valid",
}
_PREVIEWS = re.compile(
    r"^Metadata/(?:plate_\d+(?:_small)?\.(?:png|json)|plate_no_light_\d+\.png|"
    r"top_\d+\.png|pick_\d+\.png|pattern_\d+\.png)$", re.IGNORECASE,
)
_STRUCTURAL = {"name", "module", "matrix", "source_file", "merged_group_id"}


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(root, tag):
    return [e for e in root if _local(e.tag) == tag]


def _child(root, tag):
    return next(iter(_children(root, tag)), None)


def _metadata(root) -> dict:
    return {e.get("key"): e.get("value", "") for e in _children(root, "metadata")}


def _option_config(root) -> dict:
    return {k: v for k, v in _metadata(root).items()
            if k not in _STRUCTURAL and not k.startswith("source_")}


def _set_metadata(root, key, value) -> None:
    matches = [e for e in _children(root, "metadata") if e.get("key") == key]
    if value is None:
        for e in matches:
            root.remove(e)
        return
    text = "1" if value is True else "0" if value is False else str(value)
    if matches:
        matches[0].set("value", text)
        for e in matches[1:]:
            root.remove(e)
    else:
        root.insert(0, ET.Element("metadata", {"key": key, "value": text}))


@functools.lru_cache(maxsize=1)
def _option_types():
    from .presets import load_schema
    return {key: record["type"] for key, record in load_schema().items()}


def _escape_cstyle(value):
    # Config.cpp escape_string_cstyle, before XML attribute escaping by ET.
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")


def _native_option_value(key, value):
    """Convert validated native JSON values to ConfigOption::serialize XML text."""
    if value is None:
        return None
    kind = _option_types().get(key)

    def scalar(item):
        text = "1" if item is True else "0" if item is False else str(item)
        if kind in {"coBool", "coBools"} and text.lower() in {"true", "false"}:
            text = "1" if text.lower() == "true" else "0"
        if kind in {"coPercent", "coPercents"} and text != "nil" and not text.endswith("%"):
            text += "%"
        return text

    def point(item, delimiter, dimensions):
        parts = re.split("[x,]", item) if isinstance(item, str) else item
        if not isinstance(parts, (list, tuple)) or len(parts) != dimensions:
            raise ValueError(f"{key}: expected a {dimensions}-coordinate point")
        return delimiter.join(str(v) for v in parts)

    if kind in {"coPoint", "coPoint3"}:
        return point(value, ",", 3 if kind == "coPoint3" else 2)
    if isinstance(value, (list, tuple)):
        if kind == "coStrings":
            strings = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError(f"{key}: string vectors require strings")
                # Native strings use ';', quoted C-style when complex. Quote
                # embedded separators too so the native decoder stays lossless.
                quoted = (len(value) == 1 and not item) or any(c in item for c in ' \t\\"\r\n;')
                strings.append('"' + _escape_cstyle(item) + '"' if quoted else item)
            return ";".join(strings)
        if kind == "coPoints":
            return ",".join(point(item, "x", 2) for item in value)
        if kind in {"coFloats", "coInts", "coBools", "coPercents", "coFloatsOrPercents", "coFloatOrPercents", "coEnums"}:
            return ",".join(scalar(item) for item in value)
        raise ValueError(f"{key}: no native vector serialization is defined")
    if kind == "coString":
        return _escape_cstyle(str(value))
    if kind == "coStrings":
        return _native_option_value(key, [str(value)])
    return scalar(value)


def _read_xml(data: bytes):
    namespaces = list(dict.fromkeys(pair for _, pair in ET.iterparse(io.BytesIO(data), events=("start-ns",))))
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    return ET.fromstring(data, parser=parser), namespaces


def _tree_state(root):
    # ET's serializer uses a process-wide prefix registry. Structural comparison
    # must stay independent of a different project's namespace registrations.
    return (root.tag, tuple(root.attrib.items()), root.text, root.tail,
            tuple(_tree_state(child) for child in root))


def _xml_bytes(root, namespaces=()) -> bytes:
    # ElementTree otherwise drops a prefix used only by requiredextensions.
    # Work on a copy: output preparation must not mutate the editor's trees.
    with _XML_LOCK:
        tree = copy.deepcopy(root)
        for prefix, uri in namespaces:
            if not re.fullmatch(r"ns\d+", prefix):
                ET.register_namespace(prefix, uri)
        preliminary = ET.tostring(tree, encoding="unicode")
        declared = set(re.findall(r'\sxmlns(?::([^=\s]+))?=', preliminary))
        for prefix, uri in namespaces:
            if prefix not in declared and prefix != "xml":
                tree.set("xmlns" + (":" + prefix if prefix else ""), uri)
        return ET.tostring(tree, encoding="utf-8", xml_declaration=True)


def _transform(text: str | None) -> list[float]:
    values = list(map(float, text.split())) if text else IDENTITY.copy()
    if len(values) != 12 or not all(math.isfinite(v) for v in values):
        raise ValueError("A 3MF transform must contain 12 finite numbers")
    return values


def compose_transform(a: list[float], b: list[float]) -> list[float]:
    """Compose affine 3MF row-vector transforms: apply a, then b."""
    if len(a) != 12 or len(b) != 12 or not all(math.isfinite(float(v)) for v in (*a, *b)):
        raise ValueError("A 3MF transform must contain 12 finite numbers")
    linear = [sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3))
              for r in range(3) for c in range(3)]
    translation = [sum(a[9 + k] * b[k * 3 + c] for k in range(3)) + b[9 + c]
                   for c in range(3)]
    return linear + translation


class Project:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        with zipfile.ZipFile(self.path) as archive:
            names = archive.namelist()
            if len(set(names)) != len(names):
                raise ValueError("Duplicate ZIP members make project edits ambiguous")
            self.data: dict[str, bytes] = {n: archive.read(n) for n in names}
            self._infos = {e.filename: e for e in archive.infolist()}
            self._comment = archive.comment
        self._model_name = "3D/3dmodel.model"
        if "_rels/.rels" in self.data:
            rels, _ = _read_xml(self.data["_rels/.rels"])
            for rel in rels:
                if rel.get("Type", "").endswith("/3dmodel") and rel.get("TargetMode") != "External":
                    self._model_name = rel.get("Target", "").lstrip("/")
                    break
        if self._model_name not in self.data:
            raise ValueError("The package has no root 3MF model")
        self.model, model_ns = _read_xml(self.data[self._model_name])
        self.settings, settings_ns = _read_xml(self.data.get(SETTINGS, b"<config/>"))
        self._namespaces = {self._model_name: model_ns, SETTINGS: settings_ns}
        self._initial_xml = {self._model_name: _tree_state(self.model), SETTINGS: _tree_state(self.settings)}
        self.config: dict = json.loads(self.data.get(CONFIG, b"{}"))
        if not isinstance(self.config, dict):
            raise ValueError("Project settings must be a JSON object")
        self._initial_config = copy.deepcopy(self.config)

    def _resources(self):
        value = _child(self.model, "resources")
        if value is None:
            raise ValueError("The root model has no resources")
        return value

    def _items(self, object_id=None):
        build = _child(self.model, "build")
        items = _children(build, "item") if build is not None else []
        return items if object_id is None else [e for e in items if e.get("objectid") == str(object_id)]

    def _objects(self):
        active = {e.get("objectid") for e in self._items()}
        return [e for e in _children(self._resources(), "object") if e.get("id") in active]

    def _object(self, object_id):
        obj = next((e for e in self._objects() if e.get("id") == str(object_id)), None)
        if obj is None:
            raise ValueError(f"Unknown root object ID: {object_id}")
        return obj

    def _object_settings(self, object_id, create=False):
        value = next((e for e in _children(self.settings, "object") if e.get("id") == str(object_id)), None)
        if value is None and create:
            value = ET.Element("object", {"id": str(object_id)})
            self.settings.insert(0, value)
        return value

    def list_plates(self) -> list[dict]:
        result = []
        for position, element in enumerate(_children(self.settings, "plate"), 1):
            meta = _metadata(element)
            objects = []
            for item in _children(element, "model_instance"):
                ids = _metadata(item)
                objects.append({"object_id": int(ids["object_id"]), "instance_id": int(ids["instance_id"])})
            result.append({"plate": int(meta.get("plater_id", position)), "name": meta.get("plater_name", ""),
                           "locked": meta.get("locked", "false").lower() in {"true", "1"}, "objects": objects})
        return result

    def list_objects(self) -> list[dict]:
        membership = {(item["object_id"], item["instance_id"]): plate["plate"]
                      for plate in self.list_plates() for item in plate["objects"]}
        result = []
        for obj in self._objects():
            oid = int(obj.get("id"))
            settings = self._object_settings(oid)
            meta = _metadata(settings) if settings is not None else {}
            instances = [{"instance_id": i, "transform": _transform(e.get("transform")),
                          "plate": membership.get((oid, i)), "printable": e.get("printable", "1") != "0"}
                         for i, e in enumerate(self._items(oid))]
            parts = [{"part_id": int(e.get("id")), "name": _metadata(e).get("name", ""), "config": _option_config(e)}
                     for e in _children(settings, "part")] if settings is not None else []
            result.append({"object_id": oid, "name": meta.get("name", obj.get("name", f"Object {oid}")),
                           "config": _option_config(settings) if settings is not None else {},
                           "instances": instances, "parts": parts})
        return result

    def describe(self) -> dict:
        return {"objects": self.list_objects(), "plates": self.list_plates()}

    def get_config(self, keys=None) -> dict:
        return copy.deepcopy(self.config if keys is None else {k: self.config[k] for k in keys if k in self.config})

    def set_config(self, changes: dict) -> None:
        slots = len(self.config.get("filament_colour", self.config.get("filament_settings_id", [])))
        for key, value in changes.items():
            if key in {"printer_model", "printer_variant", "printer_technology"}:
                if key in self.config and value != self.config[key]:
                    raise ValueError("Changing the project's machine is a separate operation")
            old = self.config.get(key)
            if slots and isinstance(value, list):
                expected = slots * slots if key == "flush_volumes_matrix" else slots * 2 if key == "flush_volumes_vector" else None
                if expected is None and (key.startswith("filament_") or key in {"nozzle_temperature", "nozzle_temperature_initial_layer", "additional_cooling_fan_speed"}):
                    if isinstance(old, list) and len(old) == slots:
                        expected = slots
                if expected is not None and len(value) != expected:
                    raise ValueError("Changing the material slot count is unsupported")
        self.config.update(copy.deepcopy(changes))

    def set_object_config(self, object_id, changes, part_id=None):
        self._object(object_id)
        root = self._object_settings(object_id)
        if part_id is not None:
            root = next((e for e in _children(root, "part") if e.get("id") == str(part_id)), None) if root is not None else None
            if root is None:
                raise ValueError(f"Unknown part ID {part_id} in object {object_id}")
        for key, value in changes.items():
            if key in {"matrix", "source_file", "merged_group_id"} or key.startswith("source_"):
                raise ValueError("Source and geometry metadata are not print overrides")
            if key == "extruder" and value is not None:
                slots = len(self.config.get("filament_colour", self.config.get("filament_settings_id", [])))
                if (type(value) is not int and not (isinstance(value, str) and re.fullmatch(r"\d+", value))
                        or not 0 <= int(value) <= slots):
                    raise ValueError(f"extruder must be 0 (inherit) or an existing material slot 1..{slots}")
        serialized = {key: _native_option_value(key, value) for key, value in changes.items()}
        if root is None:
            root = self._object_settings(object_id, create=True)
        for key, value in serialized.items():
            _set_metadata(root, key, value)

    def _plate(self, plate):
        if type(plate) is not int:
            raise ValueError("plate must be an existing 1-based plate ID")
        for position, element in enumerate(_children(self.settings, "plate"), 1):
            if int(_metadata(element).get("plater_id", position)) == plate:
                return element
        raise ValueError(f"Unknown plate ID: {plate}")

    def _bed_dimensions(self):
        try:
            points = [[float(v) for v in p.split("x")] for p in self.config["printable_area"]]
            if len(points) < 3 or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
                raise ValueError
            width = max(p[0] for p in points) - min(p[0] for p in points)
            depth = max(p[1] for p in points) - min(p[1] for p in points)
            if width <= 0 or depth <= 0:
                raise ValueError
            return width, depth
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ValueError("Plate operations require an explicit, valid printable_area") from None

    @staticmethod
    def _plate_origin(position, count, dimensions):
        # PartPlate.cpp compute_shape_position: 20% logical bed gap, -Y rows.
        columns = math.ceil(math.sqrt(count))
        return [(position % columns) * dimensions[0] * 1.2,
                -(position // columns) * dimensions[1] * 1.2, 0.]

    @staticmethod
    def _unprintable_origin(count, dimensions):
        columns = math.ceil(math.sqrt(count))
        position = count
        if count == columns * columns:
            position += columns - 1
            columns += 1
        return [(position % columns) * dimensions[0] * 1.2,
                -(position // columns) * dimensions[1] * 1.2, 0.]

    def _parked_tree(self):
        return _read_xml(self.data[PARKED])[0] if PARKED in self.data else ET.Element("parked_instances")

    def _write_parked(self, root):
        if _children(root, "model_instance"):
            self.data[PARKED] = _xml_bytes(root)
        else:
            self.data.pop(PARKED, None)

    def _identify_seed(self, parked):
        return max([int(e.get("value", "0")) for root in (self.settings, parked) for e in root.iter()
                    if _local(e.tag) == "metadata" and e.get("key") == "identify_id"] or [0])

    def _grid_updates(self, old_plates, new_plates):
        updates = []
        dimensions = None
        for new_index, plate in enumerate(new_plates):
            if plate not in old_plates:
                continue
            old_index = old_plates.index(plate)
            if (old_index == new_index
                    and math.ceil(math.sqrt(len(old_plates))) == math.ceil(math.sqrt(len(new_plates)))):
                continue
            members = _children(plate, "model_instance")
            if not members:
                continue
            if dimensions is None:
                dimensions = self._bed_dimensions()
            before = self._plate_origin(old_index, len(old_plates), dimensions)
            after = self._plate_origin(new_index, len(new_plates), dimensions)
            for member in members:
                ids = _metadata(member)
                items = self._items(ids["object_id"])
                index = int(ids["instance_id"])
                if not 0 <= index < len(items):
                    raise ValueError("Plate metadata references an unknown instance")
                value = _transform(items[index].get("transform"))
                value[9:] = [value[9 + i] + after[i] - before[i] for i in range(3)]
                updates.append((items[index], value))
        return updates

    @staticmethod
    def _apply_placements(updates):
        for item, value in updates:
            item.set("transform", " ".join("0" if abs(v) < 1e-14 else format(v, ".17g") for v in value))

    def _new_plate(self, number, name=""):
        added = ET.Element("plate")
        source = next(iter(_children(self.settings, "plate")), None)
        if source is not None:
            inherited = {"filament_maps", "filament_map_mode", "bed_type", "curr_bed_type", "print_sequence", "spiral_mode"}
            for element in _children(source, "metadata"):
                if element.get("key") in inherited:
                    added.append(copy.deepcopy(element))
        for key, value in (("plater_id", number), ("plater_name", name), ("locked", "false")):
            _set_metadata(added, key, value)
        return added

    def add_plate(self, name=""):
        if not isinstance(name, str):
            raise ValueError("Plate name must be a string")
        plates = _children(self.settings, "plate")
        if len(plates) >= 36:
            raise ValueError("Snapmaker Orca supports at most 36 plates")
        added = self._new_plate(len(plates) + 1, name)
        updates = self._grid_updates(plates, plates + [added])
        self.settings.append(added)
        self._apply_placements(updates)
        return len(plates) + 1

    def _custom_gcode_after_plate_delete(self, plate, mapping):
        if CUSTOM_GCODE not in self.data:
            return None
        root, namespaces = _read_xml(self.data[CUSTOM_GCODE])
        plates = _children(root, "plate")
        changed = False
        if not plates:
            return b"" if plate == 1 else None  # Legacy single-plate format.
        for entry in plates:
            info = _child(entry, "plate_info")
            if info is None:
                continue
            old = int(info.get("id"))
            if old == plate:
                root.remove(entry)
                changed = True
            elif old in mapping and old != mapping[old]:
                info.set("id", str(mapping[old]))
                changed = True
        return _xml_bytes(root, namespaces) if changed else None

    def delete_plate(self, plate):
        target = self._plate(plate)
        plates = _children(self.settings, "plate")
        if len(plates) <= 1:
            raise ValueError("The last plate cannot be deleted")
        remaining = [p for p in plates if p is not target]
        mapping = {int(_metadata(p).get("plater_id", plates.index(p) + 1)): i
                   for i, p in enumerate(remaining, 1)}
        updates = self._grid_updates(plates, remaining)
        codes = self._custom_gcode_after_plate_delete(plate, mapping)
        parked = self._parked_tree()
        members = _children(target, "model_instance")
        if members:
            dimensions = self._bed_dimensions()
            before = self._plate_origin(plates.index(target), len(plates), dimensions)
            after = self._unprintable_origin(len(remaining), dimensions)
            for member in members:
                ids = _metadata(member)
                items = self._items(ids["object_id"])
                index = int(ids["instance_id"])
                if not 0 <= index < len(items):
                    raise ValueError("Plate metadata references an unknown instance")
                value = _transform(items[index].get("transform"))
                value[9:] = [value[9 + i] + after[i] - before[i] for i in range(3)]
                updates.append((items[index], value))
                parked.append(copy.deepcopy(member))
        self.settings.remove(target)
        for i, element in enumerate(remaining, 1):
            _set_metadata(element, "plater_id", i)
        self._apply_placements(updates)
        self._write_parked(parked)
        # Native PartPlateList::delete_plate compacts these per-plate arrays.
        for key in ("wipe_tower_x", "wipe_tower_y"):
            values = self.config.get(key)
            if isinstance(values, list) and len(values) > plates.index(target):
                del values[plates.index(target)]
        if codes is not None:
            if codes:
                self.data[CUSTOM_GCODE] = codes
            else:
                self.data.pop(CUSTOM_GCODE, None)
        return {"deleted": True, "plate": plate, "count": len(remaining),
                "objects_moved_off_plate": len(members)}

    def move_object_to_plate(self, object_id, plate, instance_id=None):
        self._object(object_id)
        target = self._plate(plate)
        plates = _children(self.settings, "plate")
        items = self._items(object_id)
        indices = list(range(len(items))) if instance_id is None else [instance_id]
        if any(type(i) is not int or not 0 <= i < len(items) for i in indices):
            raise ValueError(f"Unknown instance ID: {instance_id}")
        dimensions = self._bed_dimensions()
        after = self._plate_origin(plates.index(target), len(plates), dimensions)
        parked = self._parked_tree()
        identify = self._identify_seed(parked)
        moves = []
        for index in indices:
            membership = [(p, e) for p in plates for e in _children(p, "model_instance")
                          if _metadata(e).get("object_id") == str(object_id)
                          and _metadata(e).get("instance_id") == str(index)]
            value = _transform(items[index].get("transform"))
            if len(membership) > 1:
                raise ValueError("An instance cannot belong to more than one plate")
            if membership:
                source, member = membership[0]
                before = self._plate_origin(plates.index(source), len(plates), dimensions)
                value[9:] = [value[9 + i] + after[i] - before[i] for i in range(3)]
            else:
                source = parked
                member = next((e for e in _children(parked, "model_instance")
                               if _metadata(e).get("object_id") == str(object_id)
                               and _metadata(e).get("instance_id") == str(index)), None)
                if member is None:
                    member = ET.Element("model_instance")
                    identify += 1
                    for key, v in (("object_id", object_id), ("instance_id", index), ("identify_id", identify)):
                        _set_metadata(member, key, v)
                    source = None
                # Native add_instance(move_position=true) centres the instance
                # origin and keeps its Z and complete linear transformation.
                value[9:11] = [after[0] + dimensions[0] / 2, after[1] + dimensions[1] / 2]
            moves.append((source, member, items[index], value))
        for source, member, item, value in moves:
            if source is not target:
                if source is not None:
                    source.remove(member)
                target.append(member)
                self._apply_placements([(item, value)])
        self._write_parked(parked)

    def apply_native_layout(self, layout, deltas):
        """Apply audited native memberships and WORLD deltas as one in-memory edit.

        Selected poses are composed from their original matrices. Only instances
        excluded from the native selection receive the plate grid's own reflow.
        """
        working = copy.copy(self)
        working.model = copy.deepcopy(self.model)
        working.settings = copy.deepcopy(self.settings)
        working.config = copy.deepcopy(self.config)
        working.data = dict(self.data)
        result = working._apply_native_layout(layout, deltas)
        self.model, self.settings, self.config, self.data = working.model, working.settings, working.config, working.data
        return result

    def _apply_native_layout(self, layout, deltas):
        count = layout.get("plate_count") if isinstance(layout, dict) else None
        entries = layout.get("instances") if isinstance(layout, dict) else None
        if type(count) is not int or not 1 <= count <= 36 or not isinstance(entries, list) or not isinstance(deltas, dict):
            raise ValueError("Native layout needs plate_count 1..36, instance records and WORLD deltas")
        originals = {}
        for oid in self._root_ids():
            for index, item in enumerate(self._items(oid)):
                originals[oid, index] = (item, _transform(item.get("transform")))
        selected = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Native layout instances must be objects")
            oid, index, target = entry.get("object_id"), entry.get("instance_id"), entry.get("plate")
            if any(type(v) is not int for v in (oid, index, target)):
                raise ValueError("Native layout instance IDs and plate must be integers")
            key = oid, index
            if key not in originals or key in selected or not 1 <= target <= count:
                raise ValueError("Native layout contains an unknown/duplicate instance or plate")
            selected[key] = target
        if set(selected) != set(deltas):
            raise ValueError("Native layout memberships must match the WORLD delta identities")
        poses = {}
        for key, delta in deltas.items():
            try:
                values = [float(v) for v in delta]
                poses[key] = compose_transform(originals[key][1], values)
            except (TypeError, ValueError, OverflowError):
                raise ValueError("Native WORLD deltas must contain 12 finite values") from None
        old_plates = _children(self.settings, "plate")
        if [p["plate"] for p in self.list_plates()] != list(range(1, len(old_plates) + 1)):
            raise ValueError("Native layout requires contiguous 1-based source plate IDs")
        if any(p["locked"] for p in self.list_plates()[count:]):
            raise ValueError("Native layout cannot remove a locked source plate")
        parked = self._parked_tree()
        identify = self._identify_seed(parked)
        members = {}
        for plate in old_plates + [parked]:
            locked = _metadata(plate).get("locked", "false").lower() in {"true", "1"}
            for member in list(_children(plate, "model_instance")):
                meta = _metadata(member)
                key = int(meta["object_id"]), int(meta["instance_id"])
                if key in selected:
                    if key in members or locked:
                        raise ValueError("Native layout cannot move a locked or multiply assigned instance")
                    members[key] = member
                    plate.remove(member)
        if any(_children(p, "model_instance") for p in old_plates[count:]):
            raise ValueError("Native layout would remove a plate containing unselected instances")
        new_plates = old_plates[:count]
        for index in range(len(old_plates), count):
            new_plates.append(self._new_plate(index + 1))
        updates = self._grid_updates(old_plates, new_plates)
        for number in range(len(old_plates), count, -1):
            codes = self._custom_gcode_after_plate_delete(number, {i: i for i in range(1, count + 1)})
            if codes is not None:
                if codes:
                    self.data[CUSTOM_GCODE] = codes
                else:
                    self.data.pop(CUSTOM_GCODE, None)
        for plate in old_plates[count:]:
            self.settings.remove(plate)
        for plate in new_plates[len(old_plates):]:
            self.settings.append(plate)
        self._apply_placements(updates)
        for key, target in selected.items():
            member = members.get(key)
            if member is None:
                member = ET.Element("model_instance")
                identify += 1
                for name, value in (("object_id", key[0]), ("instance_id", key[1]), ("identify_id", identify)):
                    _set_metadata(member, name, value)
            new_plates[target - 1].append(member)
            self._apply_placements([(originals[key][0], poses[key])])
        self._write_parked(parked)
        for name in ("wipe_tower_x", "wipe_tower_y"):
            if isinstance(self.config.get(name), list) and count < len(old_plates):
                self.config[name] = self.config[name][:count]
        return {"plate_count": count, "changed_instances": len(selected)}

    def transform_object(self, object_id, translate=None, rotate=None, scale=None, instance_id=None):
        """Apply XYZ deltas around each instance origin, preserving assembly poses."""
        self._object(object_id)

        def vector(value, default):
            if value is None:
                return default
            if not isinstance(value, (list, tuple)) or len(value) != 3:
                raise ValueError("Transform vectors must contain three finite numbers")
            result = [float(v) for v in value]
            if not all(math.isfinite(v) for v in result):
                raise ValueError("Transform vectors must contain three finite numbers")
            return result

        offset = vector(translate, [0., 0., 0.])
        factors = vector(scale, [1., 1., 1.])
        angles = vector(rotate, [0., 0., 0.])
        if any(v == 0 for v in factors):
            raise ValueError("Zero scale would make the object degenerate")
        delta = [factors[0], 0., 0., 0., factors[1], 0., 0., 0., factors[2], 0., 0., 0.]
        for axis, degrees in enumerate(angles):
            c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
            rotation = ([1., 0., 0., 0., c, s, 0., -s, c, 0., 0., 0.] if axis == 0 else
                        [c, 0., -s, 0., 1., 0., s, 0., c, 0., 0., 0.] if axis == 1 else
                        [c, s, 0., -s, c, 0., 0., 0., 1., 0., 0., 0.])
            delta = compose_transform(delta, rotation)
        items = self._items(object_id)
        if instance_id is not None:
            if type(instance_id) is not int or not 0 <= instance_id < len(items):
                raise ValueError(f"Unknown instance ID: {instance_id}")
            items = [items[instance_id]]
        updates = []
        for item in items:
            original = _transform(item.get("transform"))
            linear = compose_transform(original[:9] + [0., 0., 0.], delta)
            updates.append((item, linear[:9] + [original[9 + i] + offset[i] for i in range(3)]))
        for item, value in updates:
            item.set("transform", " ".join("0" if abs(v) < 1e-14 else format(v, ".17g") for v in value))

    def _root_ids(self):
        # The importer adds ModelObjects at each root's first BUILD occurrence.
        return list(dict.fromkeys(int(e.get("objectid")) for e in self._items()))

    def _read_profiles(self):
        if PROFILE not in self.data:
            return {}
        lines = self.data[PROFILE].decode("utf-8-sig").splitlines()
        modern = False
        if lines and lines[0].startswith("layer_heights_profile_format_version="):
            modern = int(lines.pop(0).split("=", 1)[1]) >= 2
        ids = self._root_ids()
        profiles = {}
        for line in lines:
            if not line.strip():
                continue
            match = re.fullmatch(r"object_id=(\d+)\|(.*)", line)
            if not match:
                raise ValueError("Invalid native layer height profile record")
            key = int(match[1])
            if not modern:
                if not 1 <= key <= len(ids):
                    raise ValueError("Layer profile ordinal does not identify a root object")
                key = ids[key - 1]
            if key in profiles:
                raise ValueError("Duplicate layer height profile record")
            profiles[key] = [float(v) for v in match[2].split(";")]
        return profiles

    def _write_profiles(self, profiles):
        if not profiles:
            self.data.pop(PROFILE, None)
            return
        text = "layer_heights_profile_format_version=2\n"
        order = self._root_ids() + sorted(set(profiles) - set(self._root_ids()))
        for oid in order:
            if oid in profiles:
                text += f"object_id={oid}|" + ";".join(format(v, ".17g") for v in profiles[oid]) + "\n"
        self.data[PROFILE] = text.encode()

    def get_layer_profile(self, object_id):
        self._object(object_id)
        return self._read_profiles().get(int(object_id), []).copy()

    def set_layer_profile(self, object_id, profile: list[float]):
        self._object(object_id)
        values = [float(v) for v in profile]
        zs, heights = values[::2], values[1::2]
        if (len(values) < 6 or len(values) % 2 or not all(math.isfinite(v) for v in values)
                or any(v < 0 for v in zs) or any(v <= 0 for v in heights)
                or any(b < a for a, b in zip(zs, zs[1:])) or zs[-1] <= zs[0]):
            raise ValueError("A native layer profile needs at least three ordered Z/positive-height pairs")
        profiles = self._read_profiles()
        profiles[int(object_id)] = values
        self._write_profiles(profiles)

    def _range_tree(self):
        return _read_xml(self.data[RANGES]) if RANGES in self.data else (ET.Element("objects"), [])

    def _write_ranges(self, root, namespaces):
        if _children(root, "object"):
            self.data[RANGES] = _xml_bytes(root, namespaces)
        else:
            self.data.pop(RANGES, None)

    def set_height_range(self, object_id, min_z=None, max_z=None, layer_height=None, clear=False):
        """Update an exact band, add a disjoint band, or clear all bands of an object."""
        self._object(object_id)
        ordinal = self._root_ids().index(int(object_id)) + 1
        root, namespaces = self._range_tree()
        entry = next((e for e in _children(root, "object") if e.get("id") == str(ordinal)), None)
        if clear:
            if entry is not None:
                root.remove(entry)
                self._write_ranges(root, namespaces)
            return
        try:
            lo, hi, height = float(min_z), float(max_z), float(layer_height)
        except (TypeError, ValueError):
            raise ValueError("A band needs finite min_z, max_z and a positive layer_height") from None
        if not all(math.isfinite(v) for v in (lo, hi, height)) or lo < 0 or hi <= lo or height <= 0:
            raise ValueError("A band needs 0 <= min_z < max_z and positive layer_height")
        selected = None
        if entry is not None:
            for band in _children(entry, "range"):
                old_lo, old_hi = float(band.get("min_z")), float(band.get("max_z"))
                if (old_lo, old_hi) == (lo, hi):
                    selected = band
                elif max(old_lo, lo) < min(old_hi, hi):
                    raise ValueError("Height ranges must be disjoint; clear the object's bands first")
        profiles = self._read_profiles()  # Validate before changing either payload.
        if entry is None:
            entry = ET.SubElement(root, "object", {"id": str(ordinal)})
        if selected is None:
            selected = ET.SubElement(entry, "range", {"min_z": str(min_z), "max_z": str(max_z)})
        options = [e for e in _children(selected, "option") if e.get("opt_key") == "layer_height"]
        if options:
            options[0].text = str(layer_height)
            for option in options[1:]:
                selected.remove(option)
        else:
            ET.SubElement(selected, "option", {"opt_key": "layer_height"}).text = str(layer_height)
        # Range heights are ignored by the engine when a continuous profile exists.
        profiles.pop(int(object_id), None)
        self._write_ranges(root, namespaces)
        self._write_profiles(profiles)

    def reset_layer_height(self, object_id):
        self._object(object_id)
        profiles = self._read_profiles()
        self.set_height_range(object_id, clear=True)
        profiles.pop(int(object_id), None)
        self._write_profiles(profiles)

    def _remap_ranges(self, old_ids, clone=None):
        for name in (RANGES, CUT):
            self._remap_xml_ordinals(name, old_ids, clone)
        if BRIM not in self.data:
            return
        new_ids = self._root_ids()
        lines, copied = [], None
        for line in self.data[BRIM].splitlines(keepends=True):
            if line.startswith(b"brim_points_format_version=") or not line.strip():
                lines.append(line)
                continue
            match = re.fullmatch(rb"object_id=(\d+)\|(.*)", line, re.DOTALL)
            if not match or not 1 <= int(match[1]) <= len(old_ids):
                raise ValueError("Brim point ordinal does not identify a root object")
            oid = old_ids[int(match[1]) - 1]
            if clone is not None and oid == clone[0]:
                copied = match[2]
            if oid in new_ids:
                lines.append(f"object_id={new_ids.index(oid) + 1}|".encode() + match[2])
        if copied is not None:
            if lines and not lines[-1].endswith(b"\n"):
                lines[-1] += b"\n"
            lines.append(f"object_id={new_ids.index(clone[1]) + 1}|".encode() + copied)
        if any(line.startswith(b"object_id=") for line in lines):
            self.data[BRIM] = b"".join(lines)
        else:
            self.data.pop(BRIM, None)

    def _remap_xml_ordinals(self, name, old_ids, clone):
        if name not in self.data:
            return
        root, namespaces = _read_xml(self.data[name])
        original = _tree_state(root)
        new_ids = self._root_ids()
        copied = None
        for element in list(_children(root, "object")):
            ordinal = int(element.get("id"))
            if not 1 <= ordinal <= len(old_ids):
                raise ValueError(f"{name}: ordinal does not identify a root object")
            oid = old_ids[ordinal - 1]
            if clone is not None and oid == clone[0]:
                copied = copy.deepcopy(element)
            if oid in new_ids:
                element.set("id", str(new_ids.index(oid) + 1))
            else:
                root.remove(element)
        if copied is not None:
            copied.set("id", str(new_ids.index(clone[1]) + 1))
            root.append(copied)
        if _children(root, "object"):
            if _tree_state(root) != original:
                self.data[name] = _xml_bytes(root, namespaces)
        else:
            self.data.pop(name, None)

    @staticmethod
    def _renew_uuids(element):
        for child in element.iter():
            for key in list(child.attrib):
                if _local(key).upper() == "UUID":
                    child.set(key, str(uuid.uuid4()))

    def duplicate_object(self, object_id):
        obj = self._object(object_id)
        old_ids = self._root_ids()
        profiles = self._read_profiles()
        new_id = max(int(e.get("id")) for e in _children(self._resources(), "object")) + 1
        cloned = copy.deepcopy(obj)
        cloned.set("id", str(new_id))
        if _child(obj, "mesh") is not None:
            # A flat core 3MF mesh must become a reference, never a second copy
            # of the painted triangles in the root document.
            cloned.remove(_child(cloned, "mesh"))
            components = ET.SubElement(cloned, f"{{{CORE}}}components")
            ET.SubElement(components, f"{{{CORE}}}component", {"objectid": str(object_id)})
        self._renew_uuids(cloned)
        self._resources().append(cloned)
        source_items = self._items(object_id)
        build = _child(self.model, "build")
        for item in source_items:
            new_item = copy.deepcopy(item)
            new_item.set("objectid", str(new_id))
            self._renew_uuids(new_item)
            build.append(new_item)
        settings = self._object_settings(object_id)
        if settings is not None:
            new_settings = copy.deepcopy(settings)
            new_settings.set("id", str(new_id))
            self.settings.append(new_settings)
        parked = self._parked_tree()
        identify = self._identify_seed(parked)
        for plate in _children(self.settings, "plate") + [parked]:
            for item in list(_children(plate, "model_instance")):
                if _metadata(item).get("object_id") == str(object_id):
                    new_item = copy.deepcopy(item)
                    _set_metadata(new_item, "object_id", new_id)
                    identify += 1
                    _set_metadata(new_item, "identify_id", identify)
                    plate.append(new_item)
        self._write_parked(parked)
        assemble = _child(self.settings, "assemble")
        if assemble is not None:
            for item in list(_children(assemble, "assemble_item")):
                if item.get("object_id") == str(object_id):
                    new_item = copy.deepcopy(item)
                    new_item.set("object_id", str(new_id))
                    assemble.append(new_item)
        self._remap_ranges(old_ids, (int(object_id), new_id))
        if int(object_id) in profiles:
            profiles[new_id] = profiles[int(object_id)].copy()
        self._write_profiles(profiles)
        return new_id

    def delete_object(self, object_id):
        obj = self._object(object_id)
        old_ids = self._root_ids()
        profiles = self._read_profiles()
        build = _child(self.model, "build")
        for item in self._items(object_id):
            build.remove(item)
        referenced = any(e.get("objectid") == str(object_id) and
                         e.get(f"{{{PRODUCTION}}}path", self._model_name).lstrip("/") == self._model_name
                         for other in _children(self._resources(), "object") if other is not obj
                         for e in other.iter() if _local(e.tag) == "component")
        if not referenced:
            self._resources().remove(obj)
        settings = self._object_settings(object_id)
        if settings is not None:
            self.settings.remove(settings)
        parked = self._parked_tree()
        for plate in _children(self.settings, "plate") + [parked]:
            for item in list(_children(plate, "model_instance")):
                if _metadata(item).get("object_id") == str(object_id):
                    plate.remove(item)
        self._write_parked(parked)
        assemble = _child(self.settings, "assemble")
        if assemble is not None:
            for item in list(_children(assemble, "assemble_item")):
                if item.get("object_id") == str(object_id):
                    assemble.remove(item)
        self._remap_ranges(old_ids)
        profiles.pop(int(object_id), None)
        self._write_profiles(profiles)

    def _output_tree(self, output, name, root):
        if _tree_state(root) != self._initial_xml.get(name):
            output[name] = _xml_bytes(root, self._namespaces.get(name, ()))

    def save(self, path: Path) -> None:
        destination = Path(path)
        if destination.resolve() == self.path:
            raise ValueError("Project.save never overwrites its source")
        if destination.exists():
            raise FileExistsError(destination)
        output = dict(self.data)
        if self.config != self._initial_config:
            output[CONFIG] = (json.dumps(self.config, ensure_ascii=False, indent=2) + "\n").encode()
        self._output_tree(output, self._model_name, self.model)
        settings = copy.deepcopy(self.settings)
        removed = set(self._infos) - set(output)
        removed.update(n for n in output if n.lower().endswith((".gcode", ".gcode.md5")) and not n.startswith("Auxiliaries/")
                       or n == "Metadata/slice_info.config" or _PREVIEWS.fullmatch(n))
        for parent in settings.iter():
            for element in list(parent):
                if _local(element.tag) == "metadata" and element.get("key") in _SLICE_REFS:
                    target = element.get("value", "").lstrip("/")
                    if target in output and not target.startswith("Auxiliaries/"):
                        removed.add(target)
                    parent.remove(element)
        self._output_tree(output, SETTINGS, settings)
        # Covers may have a relationship without a corresponding plate key.
        for name in [n for n in output if n.endswith(".rels")]:
            rels, namespaces = _read_xml(output[name])
            base = posixpath.dirname(posixpath.dirname(name))
            changed = False
            for rel in list(rels):
                if rel.get("TargetMode") == "External":
                    continue
                value = rel.get("Target", "")
                target = value.lstrip("/") if value.startswith("/") else posixpath.normpath(posixpath.join(base, value))
                thumbnail = "thumbnail" in rel.get("Type", "") and target.startswith("Metadata/")
                if thumbnail:
                    removed.add(target)
                if target in removed:
                    rels.remove(rel)
                    changed = True
            if changed:
                output[name] = _xml_bytes(rels, namespaces)
        for name in removed:
            output.pop(name, None)
        if "[Content_Types].xml" in output:
            types, namespaces = _read_xml(output["[Content_Types].xml"])
            changed = False
            extensions = {n.rsplit(".", 1)[-1].lower() for n in output if "." in n}
            for element in list(types):
                if (element.get("PartName", "").lstrip("/") in removed
                        or element.get("Extension", "").lower() not in extensions and _local(element.tag) == "Default"):
                    types.remove(element)
                    changed = True
            for name, content_type in ((RANGES, "application/xml"), (PROFILE, "text/plain"), (BRIM, "text/plain"),
                                       (CUT, "application/xml"), (PARKED, "application/xml")):
                if name in output:
                    extension = name.rsplit(".", 1)[-1]
                    if not any(e.get("PartName") == "/" + name or e.get("Extension") == extension for e in types):
                        tag = types.tag.rsplit("}", 1)[0] + "}Override" if "}" in types.tag else "Override"
                        ET.SubElement(types, tag, {"PartName": "/" + name, "ContentType": content_type})
                        changed = True
            if changed:
                output["[Content_Types].xml"] = _xml_bytes(types, namespaces)
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.comment = self._comment
            for name, data in output.items():
                archive.writestr(copy.copy(self._infos[name]) if name in self._infos else name, data)
