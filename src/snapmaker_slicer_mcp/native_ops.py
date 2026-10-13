"""Recover verified world transforms from official CPU-only native operations.

STLs are measurements in an isolated scratch directory, never replacement 3MF
assets. The caller applies the returned deltas to its preserved project graph.
"""
from __future__ import annotations

import itertools
import json
import math
import os
import posixpath
import re
import signal
import struct
import subprocess
import time
import zipfile
from collections import deque
from pathlib import Path
from xml.etree import ElementTree as ET

from .engine import Engine, file_hash
from .project import _XML_LOCK


_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_PRODUCTION = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
_NS = {"c": _CORE}
_IDENTITY = [1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0.]


def _native_xml(tree):
    # ElementTree's namespace registry is process-global. Project serialization
    # also uses this lock; restore its registry after each native preparation.
    with _XML_LOCK:
        previous = dict(ET._namespace_map)
        try:
            ET.register_namespace("", _CORE)
            ET.register_namespace("p", _PRODUCTION)
            return ET.tostring(tree, encoding="utf-8", xml_declaration=True)
        finally:
            ET._namespace_map.clear()
            ET._namespace_map.update(previous)


def _matrix(value: str | None) -> list[float]:
    result = [float(n) for n in value.split()] if value else list(_IDENTITY)
    if len(result) != 12 or not all(math.isfinite(n) for n in result):
        raise ValueError("Invalid 3MF affine matrix")
    return result


def _point(point, matrix):
    return tuple(sum(point[j] * matrix[3 * j + k] for j in range(3)) + matrix[9 + k] for k in range(3))


def _compose(first, second):
    """Row-vector composition: apply first, then second."""
    return [sum(first[3 * i + k] * second[3 * k + j] for k in range(3))
            for i in range(3) for j in range(3)] + list(_point(first[9:12], second))


def _metadata(node):
    return {m.get("key"): m.get("value", "") for m in node.findall("metadata")}


class _Geometry:
    """Read-only graph traversal matching the native importer's volume order."""

    def __init__(self, project: Path):
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
        self.objects = {name: {int(obj.get("id")): obj for obj in model.findall("c:resources/c:object", _NS)}
                        for name, model in self.models.items()}
        self.build = self.model.find("c:build", _NS)
        if self.build is None or not list(self.build):
            raise ValueError("The source project has no build instances")
        self.instances = {}
        self.items = []
        for item in self.build:
            if item.tag != f"{{{_CORE}}}item" or item.get(f"{{{_PRODUCTION}}}path"):
                raise ValueError("External build-item roots are unsupported for transform recovery")
            oid = int(item.get("objectid"))
            if oid not in self.objects[self.root_name]:
                raise ValueError("The source build references a missing root object")
            ordinal = len(self.instances.setdefault(oid, []))
            self.instances[oid].append(_matrix(item.get("transform")))
            self.items.append(((oid, ordinal), item))
        data = self.data.get("Metadata/model_settings.config")
        self.settings = ET.fromstring(data) if data is not None else ET.Element("config")
        self.object_settings = {int(obj.get("id")): obj for obj in self.settings.findall("object")}

    def mesh(self, oid: int):
        """Normal-part triangles, preserving the native breadth-first order."""
        settings = self.object_settings.get(oid)
        parts = {int(p.get("id")): p for p in settings.findall("part")} if settings is not None else {}
        queue = deque([(self.root_name, oid, list(_IDENTITY), ())])
        triangles = []
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
                    queue.append((target, int(component.get("objectid")),
                                  _compose(_matrix(component.get("transform")), transform), ancestors + (key,)))
                continue
            part = parts.get(object_id)
            subtype = part.get("subtype", "normal_part") if part is not None else "normal_part"
            if part is not None:
                meta = _metadata(part)
                subtype = meta.get("volume_type", meta.get("part_type", subtype))
            if subtype != "normal_part":
                continue
            vertices = [tuple(float(v.get(k)) for k in ("x", "y", "z"))
                        for v in obj.findall("c:mesh/c:vertices/c:vertex", _NS)]
            faces = [tuple(int(t.get(k)) for k in ("v1", "v2", "v3"))
                     for t in obj.findall("c:mesh/c:triangles/c:triangle", _NS)]
            if not vertices or not faces:
                raise ValueError("Root object has an empty normal-part mesh")
            if not all(all(math.isfinite(x) for x in v) for v in vertices):
                raise ValueError("Non-finite source mesh coordinate")
            try:
                triangles.extend(tuple(_point(vertices[i], transform) for i in face) for face in faces
                                 if all(i >= 0 for i in face))
            except IndexError as error:
                raise ValueError("Invalid source mesh vertex index") from error
            if any(any(i < 0 for i in face) for face in faces):
                raise ValueError("Invalid source mesh vertex index")
        if not triangles:
            raise ValueError("Root object has no normal-part geometry")
        return triangles

    def locked_instances(self):
        result = {}
        all_keys = {key for key, _ in self.items}
        for position, node in enumerate(self.settings.findall("plate"), 1):
            if _metadata(node).get("locked", "false").lower() != "true":
                continue
            plate = int(_metadata(node).get("plater_id", position))
            for instance in node.findall("model_instance"):
                metadata = _metadata(instance)
                key = int(metadata["object_id"]), int(metadata["instance_id"])
                if key not in all_keys or key in result:
                    raise ValueError("Locked plate has invalid or ambiguous instance references")
                result[key] = plate
        return result

    def selected(self, plate: int, omit_locked=False):
        if not plate:
            selected = {key for key, _ in self.items}
            if omit_locked:
                selected -= set(self.locked_instances())
                if not selected:
                    raise ValueError("All source plate instances are locked")
            return selected
        nodes = [p for n, p in enumerate(self.settings.findall("plate"), 1)
                 if int(_metadata(p).get("plater_id", n)) == plate]
        if len(nodes) != 1:
            raise ValueError("The requested plate is missing or ambiguous")
        if _metadata(nodes[0]).get("locked", "false").lower() == "true":
            raise ValueError("The requested plate is locked; unlock it explicitly before editing")
        selected = set()
        for instance in nodes[0].findall("model_instance"):
            metadata = _metadata(instance)
            selected.add((int(metadata["object_id"]), int(metadata["instance_id"])))
        all_keys = {key for key, _ in self.items}
        if not selected or not selected <= all_keys:
            raise ValueError("The requested plate is empty or references invalid instances")
        return selected

    def native_input(self, target: Path, selected: set):
        """Sanitize export names and select build items in a scratch copy only."""
        remap = {}
        groups = {}
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
            for name in names:
                name.set("value", f"mcp_object_{oid}")
        for plate in self.settings.findall("plate"):
            for instance in plate.findall("model_instance"):
                meta = _metadata(instance)
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
                key = int(item.get("object_id")), int(item.get("instance_id"))
                if key not in selected:
                    assemble.remove(item)
                else:
                    item.set("instance_id", str(remap[key]))
        # Retain all plate ids/origins and their settings. Empty other plates
        # contain no build instances and therefore cannot be oriented/arranged.
        changed = {self.root_name: _native_xml(self.model),
                   "Metadata/model_settings.config": _native_xml(self.settings)}
        with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in self.data.items():
                archive.writestr(name, changed.get(name, data))
            if "Metadata/model_settings.config" not in self.data:
                archive.writestr("Metadata/model_settings.config", changed["Metadata/model_settings.config"])
        return groups


def _read_stl(path: Path):
    if path.is_symlink():
        raise ValueError("Native STL output cannot be a symlink")
    data = path.read_bytes()
    if len(data) < 84:
        raise ValueError("Native output is not a binary STL")
    count = struct.unpack_from("<I", data, 80)[0]
    if not count or len(data) != 84 + count * 50:
        raise ValueError("Native STL facet count is invalid")
    points = [tuple(face[i:i + 3]) for face in struct.iter_unpack("<12fH", data[84:]) for i in (3, 6, 9)]
    if not all(all(math.isfinite(n) for n in point) for point in points):
        raise ValueError("Native STL has non-finite coordinates")
    return points


def _subtract(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def _tolerance(*meshes):
    # Binary STL is float32. Allow its rounding at the measured coordinate
    # magnitude while retaining a 0.2 micron absolute floor in print space.
    magnitude = max((abs(n) for mesh in meshes for p in mesh for n in p), default=1.)
    return max(0.0002, magnitude * 4 * 2 ** -23)


def _verify_baseline(points, triangles, instance):
    if len(points) != 3 * len(triangles):
        raise ValueError("Native baseline changed the source facet count")
    tolerance = _tolerance(points)
    for index, triangle in enumerate(triangles):
        expected = [_point(p, instance) for p in triangle]
        actual = points[index * 3:index * 3 + 3]
        # Native import may reverse a negative-volume or mirrored facet. Its
        # three positions must still be this exact source triangle in space.
        if not any(all(math.dist(a, b) <= tolerance for a, b in zip(actual, order))
                   for order in itertools.permutations(expected)):
            raise ValueError("Native baseline changed the source pose or facet sequence; no delta can be certified")


def _recover(before, after):
    if len(before) != len(after) or len(before) < 4:
        raise ValueError("Native operations changed facet correspondence")
    p0 = before[0]
    i1 = max(range(len(before)), key=lambda i: math.dist(p0, before[i]))
    v1 = _subtract(before[i1], p0)
    i2 = max(range(len(before)), key=lambda i: _dot(_cross(v1, _subtract(before[i], p0)),
                                                  _cross(v1, _subtract(before[i], p0))))
    v2 = _subtract(before[i2], p0)
    normal = _cross(v1, v2)
    i3 = max(range(len(before)), key=lambda i: abs(_dot(normal, _subtract(before[i], p0))))
    v3 = _subtract(before[i3], p0)
    determinant = _dot(v1, _cross(v2, v3))
    extent = max(math.dist(p0, p) for p in before)
    if extent == 0 or abs(determinant) <= extent ** 3 * 1e-10:
        raise ValueError("A non-coplanar source basis is required to certify a 3D delta")
    # Inverse of the matrix whose rows are the three source basis vectors.
    columns = [_cross(v2, v3), _cross(v3, v1), _cross(v1, v2)]
    inverse = [[columns[j][i] / determinant for j in range(3)] for i in range(3)]
    targets = [_subtract(after[i], after[0]) for i in (i1, i2, i3)]
    linear = [sum(inverse[i][k] * targets[k][j] for k in range(3)) for i in range(3) for j in range(3)]
    translation = [after[0][j] - sum(p0[k] * linear[3 * k + j] for k in range(3)) for j in range(3)]
    matrix = linear + translation
    rows = [linear[i:i + 3] for i in (0, 3, 6)]
    if (abs(_dot(rows[0], _cross(rows[1], rows[2])) - 1.) > 2e-5
            or any(abs(_dot(rows[i], rows[j]) - (1. if i == j else 0.)) > 2e-5
                   for i in range(3) for j in range(3))):
        raise ValueError("Native operation introduced scale, shear or reflection; a rigid delta is required")
    maximum_error = max(math.dist(_point(p, matrix), q) for p, q in zip(before, after))
    tolerance = _tolerance(before, after)
    if maximum_error > tolerance:
        raise ValueError("Native facet correspondence changed or cannot be verified for every vertex")
    return matrix, maximum_error, tolerance


def _write_audit(work: Path, value: dict):
    (work / "native_ops.json").write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _layout_state(geometry, log_path):
    """Read the actual native bed and post-arrange plate count."""
    plates = geometry.settings.findall("plate")
    identifiers = [int(_metadata(p).get("plater_id", i)) for i, p in enumerate(plates, 1)]
    if identifiers != list(range(1, len(plates) + 1)):
        raise ValueError("Native plate frame requires sequential, ordered plate ids")
    log = log_path.read_text(errors="replace")
    before = log.split("before arrange, need_arrange=", 1)[0]
    shapes = re.findall(r"m_plate_width (\d+), m_plate_depth (\d+), m_plate_height \d+, plate count (\d+)", before)
    counts = re.findall(r"rebuild_plates_after_arrangement:after rebuild, plates count (\d+)", log)
    if not shapes or not counts:
        raise ValueError("Native plate frame could not be observed from its layout diagnostics")
    width, depth, original_count = map(int, shapes[-1])
    final_count = int(counts[-1])
    if original_count != (len(plates) or 1) or not 1 <= final_count <= 36 or min(width, depth) <= 0:
        raise ValueError("Native layout changed the plate grid unexpectedly")
    return {"source_plate_count": original_count, "native_plate_count": final_count,
            "bed_dimensions_mm": [width, depth]}


def _plate_origin(index, count, dimensions):
    # Exact PartPlate.cpp compute_shape_position, using native measured bed
    # dimensions and observed counts rather than assuming a 324 mm stride.
    columns = math.ceil(math.sqrt(count))
    return [(index % columns) * dimensions[0] * 1.2, -(index // columns) * dimensions[1] * 1.2, 0.]


def _plate_frame(state, plate):
    """Restore a selected plate's frame after native empty-plate recycling."""
    if state["native_plate_count"] != plate:
        raise ValueError("Native layout spilled or changed the selected plate unexpectedly")
    dimensions = state["bed_dimensions_mm"]
    source = _plate_origin(plate - 1, state["source_plate_count"], dimensions)
    native = _plate_origin(plate - 1, state["native_plate_count"], dimensions)
    return {**state, "source_origin": source, "native_origin": native,
            "world_correction": [source[i] - native[i] for i in range(3)]}


def _fit_plate(points, origin, dimensions):
    tolerance = _tolerance(points)
    return all(origin[axis] - tolerance <= point[axis] <= origin[axis] + dimensions[axis] + tolerance
               for point in points for axis in (0, 1))


def _run(engine, project, output, arrange, orient, timeout, audit, work):
    output.mkdir()
    datadir = output / "native-data"
    datadir.mkdir()
    stls = output / "stls"
    command = [str(engine.executable), "--allow-newer-file", "--debug", "5", "--arrange", str(int(arrange)),
               "--orient", str(int(orient)), "--ensure-on-bed=0", "--datadir", str(datadir),
               "--outputdir", str(output), "--export-stls", str(stls)]
    command.append(str(project))
    env = {k: v for k, v in os.environ.items() if k not in (
        "DISPLAY", "WAYLAND_DISPLAY", "SNAPMAKER_ORCA_ALLOW_NEWER_FILE")}
    env["LC_ALL"] = "C"
    record = {"argv": command, "log_path": str(output / "engine.log"), "status": "running"}
    audit["runs"].append(record)
    _write_audit(work, audit)
    engine.verify()
    started = time.perf_counter()
    try:
        with (output / "engine.log").open("w") as log:
            process = subprocess.Popen(command, cwd=output, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=(os.name != "nt"))
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=log, stderr=subprocess.STDOUT, check=False)
                    if process.poll() is None:
                        process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()
                record["status"] = "timeout"
                raise TimeoutError(f"Native edit timed out; diagnostics: {work}") from error
        record.update(status="complete" if not code else "failed", exit_code=code)
    finally:
        record["elapsed_seconds"] = time.perf_counter() - started
        record["log_sha256"] = file_hash(output / "engine.log")
        engine.verify()
    if code:
        raise RuntimeError(f"Native edit failed ({code}); diagnostics: {work}")
    return stls


def native_deltas(engine: Engine, project: Path, work: Path, arrange: bool = False, orient: bool = False,
                  plate: int = 0, timeout: float = 1800) -> dict[tuple[int, int], list[float]]:
    """Measure official arrange/orient deltas without re-exporting the 3MF.

    ``work`` must have no previous native operation outputs. Plate numbers are
    1-based; zero selects unlocked instances. Instance ordinals are original per-root build
    ordinals, including when only one plate is selected. Other plate slots are
    locked in the scratch subset so native arrange retains its plate frame.
    Global arrange writes the observed final plate count and assignments for
    returned deltas to ``native_ops.json["layout"]``. Locked instances stay as
    native anchors and are verified separately; the caller reflows them to the
    final grid. Selected-plate overflow fails explicitly. No G-code or
    renderer/3MF export is requested. Every source baseline
    triangle and every output vertex is verified; unsupported correspondence,
    degenerate geometry and non-rigid results fail explicitly.
    """
    if type(arrange) is not bool or type(orient) is not bool or not (arrange or orient):
        raise ValueError("Request arrange and/or orient as booleans")
    if type(plate) is not int or plate < 0:
        raise ValueError("plate must be zero (all) or a positive plate number")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    project, work = Path(project).resolve(strict=True), Path(work).resolve()
    engine.verify()
    source_hash = file_hash(project)
    geometry = _Geometry(project)
    if file_hash(project) != source_hash:
        raise ValueError("Source changed while reading its geometry; retry a stable saved project")
    locked = geometry.locked_instances()
    selected = geometry.selected(plate, omit_locked=True)
    if not plate and arrange and orient and locked:
        raise ValueError("Combined global arrange/orient with locked plates requires separate per-plate operations")
    included = {key for key, _ in geometry.items} if arrange and not plate else selected
    work.mkdir(parents=True, exist_ok=True)
    if any((work / name).exists() for name in ("native_input.3mf", "native_ops.json", "baseline", "edited")):
        raise ValueError("Native operation scratch outputs already exist; choose a fresh work directory")
    audit = {"status": "running", "source_path": str(project), "source_sha256": source_hash,
             "engine": engine.identity(), "arrange": arrange, "orient": orient, "plate": plate,
             "compatibility_flags": {"allow_newer_file": True}, "runs": [], "artifacts": [], "instances": []}
    _write_audit(work, audit)
    try:
        native_input = work / "native_input.3mf"
        meshes = {oid: geometry.mesh(oid) for oid in geometry.instances if any(k[0] == oid for k in included)}
        groups = geometry.native_input(native_input, included)
        input_hash = file_hash(native_input)
        audit["native_input_sha256"] = input_hash
        baseline = _run(engine, native_input, work / "baseline", False, False, timeout, audit, work)
        edited = _run(engine, native_input, work / "edited", arrange, orient, timeout, audit, work)
        layout_state = _layout_state(geometry, work / "edited" / "engine.log") if arrange else None
        frame = _plate_frame(layout_state, plate) if arrange and plate else None
        if frame:
            audit["plate_frame"] = frame
        elif layout_state:
            audit["native_layout_state"] = layout_state
            audit["layout"] = {"plate_count": layout_state["native_plate_count"], "instances": []}
        if file_hash(project) != source_hash or file_hash(native_input) != input_hash:
            raise ValueError("Source or native scratch input changed during the operation")
        outputs = []
        for directory in (baseline, edited):
            indexed = {}
            ordinals = set()
            for path in directory.iterdir():
                match = re.fullmatch(r"obj_(\d+)_mcp_object_(\d+)\.stl", path.name)
                if not match or int(match[1]) in ordinals or int(match[2]) in indexed:
                    raise ValueError("Unexpected or ambiguous native STL output")
                ordinals.add(int(match[1]))
                indexed[int(match[2])] = path
                audit["artifacts"].append({"path": str(path), "sha256": file_hash(path)})
            if ordinals != set(range(1, len(groups) + 1)) or set(indexed) != set(groups):
                raise ValueError("Native STL object count does not match the selected source roots")
            outputs.append(indexed)
        result = {}
        for oid, keys in groups.items():
            # Native rebuild sorts ModelObjects by arrange_order. The sanitized
            # root name remains its identity even when obj_N changes.
            before, after = (_read_stl(output[oid]) for output in outputs)
            per_instance = 3 * len(meshes[oid])
            if len(before) != per_instance * len(keys) or len(after) != len(before):
                raise ValueError("Native operation changed the object or instance facet count")
            for index, key in enumerate(keys):
                first, last = index * per_instance, (index + 1) * per_instance
                source_points, target_points = before[first:last], after[first:last]
                _verify_baseline(source_points, meshes[oid], geometry.instances[oid][key[1]])
                if key not in selected:
                    old_count, count = layout_state["source_plate_count"], layout_state["native_plate_count"]
                    dimensions, locked_plate = layout_state["bed_dimensions_mm"], locked[key]
                    if locked_plate > count:
                        raise ValueError("Native layout removed an occupied locked plate")
                    old_origin = _plate_origin(locked_plate - 1, old_count, dimensions)
                    new_origin = _plate_origin(locked_plate - 1, count, dimensions)
                    offset = [new_origin[i] - old_origin[i] for i in range(3)]
                    error = max(math.dist(tuple(p[i] + offset[i] for i in range(3)), q)
                                for p, q in zip(source_points, target_points))
                    if error > _tolerance(source_points, target_points) or not _fit_plate(target_points, new_origin, dimensions):
                        raise ValueError("Native operation changed a locked instance's relative pose or plate fit")
                    audit.setdefault("locked_instances_verified", []).append(
                        {"object_id": oid, "instance_id": key[1], "plate": locked_plate,
                         "vertices_verified": per_instance, "relative_pose_error_mm": error})
                    continue
                if frame:
                    origin, dimensions = frame["native_origin"], frame["bed_dimensions_mm"]
                    if not _fit_plate(target_points, origin, dimensions):
                        raise ValueError("Native arranged geometry does not fit the selected plate")
                    target_points = [tuple(point[i] + frame["world_correction"][i] for i in range(3))
                                     for point in target_points]
                elif layout_state:
                    count, dimensions = layout_state["native_plate_count"], layout_state["bed_dimensions_mm"]
                    candidates = [index + 1 for index in range(count)
                                  if _fit_plate(target_points, _plate_origin(index, count, dimensions), dimensions)]
                    if len(candidates) != 1:
                        raise ValueError("Native arranged instance does not fit exactly one printable plate")
                    audit["layout"]["instances"].append({"object_id": oid, "instance_id": key[1],
                                                        "plate": candidates[0]})
                matrix, error, tolerance = _recover(source_points, target_points)
                result[key] = matrix
                audit["instances"].append({"object_id": oid, "instance_id": key[1], "delta": matrix,
                                           "vertices_verified": per_instance, "max_error_mm": error,
                                           "tolerance_mm": tolerance})
        engine.verify()
        audit["status"] = "verified"
        return result
    except Exception as error:
        audit.update(status="failed", error=str(error))
        raise
    finally:
        _write_audit(work, audit)


def _f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _number(config, key, default):
    value = config.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"Invalid numeric setting: {key}")
    try:
        result = float(str(value).rstrip("%"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid numeric setting: {key}") from error
    if not math.isfinite(result):
        raise ValueError(f"Non-finite setting: {key}")
    return result


def _numbers(config, key, default):
    values = config.get(key, default)
    if not isinstance(values, (list, tuple)):
        values = re.split(r"[;,]", values) if isinstance(values, str) else [values]
    if not values:
        raise ValueError(f"Empty per-nozzle setting: {key}")
    return [_number({key: value}, key, 0.) for value in values]


def _height_ranges(geometry, object_id, minimum, maximum):
    data = geometry.data.get("Metadata/layer_config_ranges.xml")
    if data is None:
        return []
    # Official 3MF layer-config ranges use a legacy 1-based model-object index,
    # even when the layer-height-profile file uses root object ids (version 2).
    ordinal = list(geometry.instances).index(object_id) + 1
    ranges = []
    for obj in ET.fromstring(data).findall("object"):
        if int(obj.get("id")) != ordinal:
            continue
        for node in obj.findall("range"):
            low, high = float(node.get("min_z")), float(node.get("max_z"))
            options = {o.get("opt_key"): o.text for o in node.findall("option")}
            if "layer_height" not in options:
                raise ValueError("Adaptive height ranges must contain layer_height")
            height = _number(options, "layer_height", 0.)
            if (not all(math.isfinite(n) for n in (low, high)) or low < 0 or low >= high
                    or not minimum <= height <= maximum):
                raise ValueError("Adaptive height range is outside the effective layer limits")
            ranges.append((low, high, height))
    return sorted(ranges)


def adaptive_layer_profile(project_path: Path, object_id: int, config: dict, quality: float) -> list[float]:
    """Port the official 9bf2f18 adaptive profile algorithm without editing a file.

    Sources: ``Slicing.cpp::layer_height_profile_adaptive``,
    ``SlicingAdaptive.cpp::next_layer_height`` and ``SlicingParameters``. Quality
    is the native 0..1 parameter (0 is finest). First-instance world geometry,
    normal parts, raft first-layer behavior, nozzle defaults, explicit height
    ranges and the 0.04 mm height-change limit follow the official algorithm.

    This port supports uniform per-nozzle limits and 100% Z compensation. Mixed
    nozzle limits or non-default shrinkage need a native parameter adapter and
    fail explicitly rather than silently approximating extruder selection.
    The caller persists only the returned profile in its original graph.
    """
    if type(quality) not in (int, float) or not math.isfinite(quality) or not 0 <= quality <= 1:
        raise ValueError("quality must be a finite native parameter in 0..1 (0 is finest)")
    if type(object_id) is not int or not isinstance(config, dict):
        raise ValueError("Specify a root object id and effective configuration")
    project_path = Path(project_path).resolve(strict=True)
    geometry = _Geometry(project_path)
    if object_id not in geometry.instances:
        raise ValueError("Unknown source root object id")
    settings = geometry.object_settings.get(object_id)
    effective = {**config, **(_metadata(settings) if settings is not None else {})}
    layer = _number(effective, "layer_height", 0.2)
    first = _number(effective, "initial_layer_print_height", 0.2)
    if first <= 0:
        first = layer
    nozzles = _numbers(effective, "nozzle_diameter", [0.4])
    minima = _numbers(effective, "min_layer_height", [0.])
    maxima = _numbers(effective, "max_layer_height", [0.])
    if any(len(set(round(n, 12) for n in values)) != 1 for values in (nozzles, minima, maxima)):
        raise ValueError("Adaptive profile currently requires uniform per-nozzle layer limits")
    if any(n != 100. for n in _numbers(effective, "filament_shrinkage_compensation_z", [100.])):
        raise ValueError("Adaptive profile with non-default Z shrinkage compensation is unsupported")
    nozzle, minimum, maximum = nozzles[0], minima[0], maxima[0]
    if nozzle <= 0 or minimum < 0 or maximum < 0 or layer <= 0 or first <= 0:
        raise ValueError("Layer and nozzle dimensions must be positive")
    minimum = 0.07 if minimum == 0 else max(0.01, minimum)
    maximum = max(minimum, 0.75 * nozzle if maximum == 0 else maximum)
    minimum, maximum = min(minimum, layer), max(maximum, layer)
    raft = _number(effective, "raft_layers", 0.)
    if raft < 0 or not raft.is_integer():
        raise ValueError("raft_layers must be a non-negative integer")
    if raft:
        first = layer
    instance = geometry.instances[object_id][0]
    triangles = [tuple(tuple(_f32(n) for n in _point(vertex, instance)) for vertex in triangle)
                 for triangle in geometry.mesh(object_id)]
    object_height = _f32(max(p[2] for triangle in triangles for p in triangle))
    if object_height <= 0:
        raise ValueError("Adaptive profiling requires object geometry above Z=0")
    faces = []
    for triangle in triangles:
        normal = _cross(_subtract(triangle[1], triangle[0]), _subtract(triangle[2], triangle[0]))
        length = math.sqrt(_dot(normal, normal))
        if length == 0:
            raise ValueError("Adaptive profiling requires non-degenerate facets")
        normal = tuple(_f32(n / length) for n in normal)
        z = [p[2] for p in triangle]
        faces.append((min(z), max(z), abs(normal[2]), _f32(math.hypot(normal[0], normal[1]))))
    faces.sort(key=lambda f: f[:2])
    ranges = _height_ranges(geometry, object_id, minimum, maximum)
    quality = _f32(quality)
    deviation = _f32(minimum + (layer - minimum) * 2 * quality if quality < .5
                     else maximum + (layer - maximum) * 2 * (1 - quality))

    def slope_height(face):
        cosine, sine = face[2:]
        slope = _f32(1.44 * deviation * math.sqrt(_f32(sine / cosine))) if cosine > 1e-5 else math.inf
        return min(_f32(deviation / _f32(.184)), slope)

    current_facet = 0

    def next_height(print_z):
        nonlocal current_facet
        height, first_hit, index = _f32(maximum), False, current_facet
        while index < len(faces):
            face = faces[index]
            if face[0] >= print_z:
                break
            if face[1] > print_z:
                if not first_hit:
                    first_hit, current_facet = True, index
                if face[1] >= print_z + 1e-4:
                    height = min(height, slope_height(face))
            index += 1
        height = max(height, _f32(minimum))
        if height > _f32(minimum):
            while index < len(faces):
                face = faces[index]
                if face[0] >= _f32(print_z + height):
                    break
                if face[1] >= print_z + 1e-4:
                    reduced = slope_height(face)
                    distance = _f32(face[0] - print_z)
                    if reduced < distance:
                        height = distance
                    elif reduced < height:
                        height = reduced
                index += 1
            height = max(height, _f32(minimum))
        return height

    profile = [0., first]
    if not raft:
        profile.extend([first, first])
    print_z = first
    while print_z + 1e-4 < object_height:
        height = min(_f32(maximum), next_height(_f32(print_z)))
        if abs(profile[-1] - height) > .04:
            height = _f32(profile[-1] + (.04 if profile[-1] < height else -.04))
        for low, high, override in ranges:
            if low <= print_z <= high:
                height = _f32(override)
                break
        profile.extend([print_z, height])
        print_z += height
    gap = object_height - profile[-2]
    if gap > 0:
        profile.extend([object_height, max(minimum, min(gap, maximum))])
    return profile
