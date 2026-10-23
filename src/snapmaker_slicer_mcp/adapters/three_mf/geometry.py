"""Read-only native 3MF graph and scratch input preparation."""
from __future__ import annotations

import math
import posixpath
import zipfile
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from xml.etree import ElementTree as ET
from typing import TypeAlias
from .xml import native_xml

Point: TypeAlias = tuple[float, ...]
Triangle: TypeAlias = tuple[Point, ...]
InstanceKey: TypeAlias = tuple[int, int]
_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_PRODUCTION = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
_NS = {"c": _CORE}
_IDENTITY = [1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0.]

def matrix(value: str | None) -> list[float]:
    result = [float(n) for n in value.split()] if value else list(_IDENTITY)
    if len(result) != 12 or not all(math.isfinite(n) for n in result):
        raise ValueError("Invalid 3MF affine matrix")
    return result


def point(point: Sequence[float], matrix: Sequence[float]) -> Point:
    return tuple(sum(point[j] * matrix[3 * j + k] for j in range(3)) + matrix[9 + k] for k in range(3))


def compose(first: Sequence[float], second: Sequence[float]) -> list[float]:
    """Row-vector composition: apply first, then second."""
    return [sum(first[3 * i + k] * second[3 * k + j] for k in range(3))
            for i in range(3) for j in range(3)] + list(point(first[9:12], second))


def read_metadata(node: ET.Element) -> dict[str, str]:
    return {m.attrib["key"]: m.get("value", "") for m in node.findall("metadata")}


class Geometry:
    """Read-only graph traversal matching the native importer's volume order."""

    def __init__(self, project: Path) -> None:
        with zipfile.ZipFile(project) as archive:
            self.data = {name: archive.read(name) for name in archive.namelist()}
        self.models = {name: ET.fromstring(data) for name, data in self.data.items()
                       if name.startswith("3D/") and name.endswith(".model")}
        self.root_name = "3D/3dmodel.model"
        if self.root_name not in self.models:
            raise ValueError("Native editing requires the canonical 3D/3dmodel.model root")
        self.model = self.models[self.root_name]
        if any(m.get("unit", "millimeter") != "millimeter" for m in self.models.values()):
            raise ValueError("Native transform recovery currently supports millimeter 3MF units")
        self.objects = {name: {int(obj.attrib["id"]): obj for obj in model.findall("c:resources/c:object", _NS)}
                        for name, model in self.models.items()}
        build = self.model.find("c:build", _NS)
        if build is None or not list(build):
            raise ValueError("The source project has no build instances")
        self.build = build
        self.instances: dict[int, list[list[float]]] = {}
        self.items: list[tuple[InstanceKey, ET.Element]] = []
        for item in self.build:
            if item.tag != f"{{{_CORE}}}item" or item.get(f"{{{_PRODUCTION}}}path"):
                raise ValueError("External build-item roots are unsupported for transform recovery")
            oid = int(item.attrib["objectid"])
            if oid not in self.objects[self.root_name]:
                raise ValueError("The source build references a missing root object")
            ordinal = len(self.instances.setdefault(oid, []))
            self.instances[oid].append(matrix(item.get("transform")))
            self.items.append(((oid, ordinal), item))
        data = self.data.get("Metadata/model_settings.config")
        self.settings = ET.fromstring(data) if data is not None else ET.Element("config")
        self.object_settings = {int(obj.attrib["id"]): obj for obj in self.settings.findall("object")}

    def mesh(self, oid: int) -> list[Triangle]:
        """Normal-part triangles, preserving the native breadth-first order."""
        settings = self.object_settings.get(oid)
        parts = {int(p.attrib["id"]): p for p in settings.findall("part")} if settings is not None else {}
        queue: deque[tuple[str, int, list[float], tuple[tuple[str, int], ...]]] = deque([(self.root_name, oid, list(_IDENTITY), ())])
        triangles: list[Triangle] = []
        while queue:
            name, object_id, transform, ancestors = queue.popleft()
            key = name, object_id
            if key in ancestors or len(ancestors) >= 10:
                raise ValueError("Cyclic or excessively nested 3MF component graph")
            try:
                obj = self.objects[name][object_id]
            except KeyError as error:
                raise ValueError("Missing 3MF component object") from error
            components = obj.find("c:components", _NS)
            if components is not None and len(components):
                for component in components:
                    path = component.get(f"{{{_PRODUCTION}}}path", name)
                    target = posixpath.normpath(path.lstrip("/"))
                    if target.startswith("../") or target not in self.models:
                        raise ValueError("Missing or invalid 3MF component model path")
                    queue.append((target, int(component.attrib["objectid"]),
                                  compose(matrix(component.get("transform")), transform), ancestors + (key,)))
                continue
            part = parts.get(object_id)
            subtype = part.get("subtype", "normal_part") if part is not None else "normal_part"
            if part is not None:
                meta = read_metadata(part)
                subtype = meta.get("volume_type", meta.get("part_type", subtype))
            if subtype != "normal_part":
                continue
            vertices = [tuple(float(v.attrib[k]) for k in ("x", "y", "z"))
                        for v in obj.findall("c:mesh/c:vertices/c:vertex", _NS)]
            faces = [tuple(int(t.attrib[k]) for k in ("v1", "v2", "v3"))
                     for t in obj.findall("c:mesh/c:triangles/c:triangle", _NS)]
            if not vertices or not faces:
                raise ValueError("Root object has an empty normal-part mesh")
            if not all(all(math.isfinite(x) for x in v) for v in vertices):
                raise ValueError("Non-finite source mesh coordinate")
            try:
                triangles.extend(tuple(point(vertices[i], transform) for i in face) for face in faces
                                 if all(i >= 0 for i in face))
            except IndexError as error:
                raise ValueError("Invalid source mesh vertex index") from error
            if any(any(i < 0 for i in face) for face in faces):
                raise ValueError("Invalid source mesh vertex index")
        if not triangles:
            raise ValueError("Root object has no normal-part geometry")
        return triangles

    def locked_instances(self) -> dict[InstanceKey, int]:
        result = {}
        all_keys = {key for key, _ in self.items}
        for position, node in enumerate(self.settings.findall("plate"), 1):
            if read_metadata(node).get("locked", "false").lower() != "true":
                continue
            plate = int(read_metadata(node).get("plater_id", position))
            for instance in node.findall("model_instance"):
                metadata = read_metadata(instance)
                key = int(metadata["object_id"]), int(metadata["instance_id"])
                if key not in all_keys or key in result:
                    raise ValueError("Locked plate has invalid or ambiguous instance references")
                result[key] = plate
        return result

    def selected(self, plate: int, omit_locked: bool = False) -> set[InstanceKey]:
        if not plate:
            selected = {key for key, _ in self.items}
            if omit_locked:
                selected -= set(self.locked_instances())
                if not selected:
                    raise ValueError("All source plate instances are locked")
            return selected
        nodes = [p for n, p in enumerate(self.settings.findall("plate"), 1)
                 if int(read_metadata(p).get("plater_id", n)) == plate]
        if len(nodes) != 1:
            raise ValueError("The requested plate is missing or ambiguous")
        if read_metadata(nodes[0]).get("locked", "false").lower() == "true":
            raise ValueError("The requested plate is locked; unlock it explicitly before editing")
        selected = set()
        for instance in nodes[0].findall("model_instance"):
            metadata = read_metadata(instance)
            selected.add((int(metadata["object_id"]), int(metadata["instance_id"])))
        all_keys = {key for key, _ in self.items}
        if not selected or not selected <= all_keys:
            raise ValueError("The requested plate is empty or references invalid instances")
        return selected

    def native_input(self, target: Path, selected: set[InstanceKey]) -> dict[int, list[InstanceKey]]:
        """Sanitize export names and select build items in a scratch copy only."""
        remap = {}
        groups: dict[int, list[InstanceKey]] = {}
        for key, item in self.items:
            if key not in selected:
                self.build.remove(item)
                continue
            oid, _ = key
            remap[key] = len(groups.setdefault(oid, []))
            groups[oid].append(key)
        for oid, obj in self.objects[self.root_name].items():
            obj.set("name", f"mcp_object_{oid}")
        for oid, obj in self.object_settings.items():
            names = [m for m in obj.findall("metadata") if m.get("key") == "name"]
            if not names:
                names = [ET.SubElement(obj, "metadata", {"key": "name"})]
            for name_node in names:
                name_node.set("value", f"mcp_object_{oid}")
        for plate in self.settings.findall("plate"):
            for instance in plate.findall("model_instance"):
                meta = read_metadata(instance)
                key = int(meta["object_id"]), int(meta["instance_id"])
                if key not in selected:
                    plate.remove(instance)
                else:
                    for field in instance.findall("metadata"):
                        if field.get("key") == "instance_id":
                            field.set("value", str(remap[key]))
            if selected != {key for key, _ in self.items} and not plate.findall("model_instance"):
                # Native global arrange skips locked plate slots in its own
                # bed-index postprocessing. Keep the selected plate's frame
                # without invoking select_plate, which requires wxGetApp.
                locks = [m for m in plate.findall("metadata") if m.get("key") == "locked"]
                if not locks:
                    locks = [ET.SubElement(plate, "metadata", {"key": "locked"})]
                for field in locks:
                    field.set("value", "true")
        for assemble in self.settings.findall("assemble"):
            for item in list(assemble):
                key = int(item.attrib["object_id"]), int(item.attrib["instance_id"])
                if key not in selected:
                    assemble.remove(item)
                else:
                    item.set("instance_id", str(remap[key]))
        # Retain all plate ids/origins and their settings. Empty other plates
        # contain no build instances and therefore cannot be oriented/arranged.
        changed = {self.root_name: native_xml(self.model, _CORE, _PRODUCTION),
                   "Metadata/model_settings.config": native_xml(self.settings, _CORE, _PRODUCTION)}
        with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in self.data.items():
                archive.writestr(name, changed.get(name, data))
            if "Metadata/model_settings.config" not in self.data:
                archive.writestr("Metadata/model_settings.config", changed["Metadata/model_settings.config"])
        return groups

