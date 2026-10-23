"""One native slicing operation, immutable artifacts and source recovery."""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree

from .engine import Engine, file_hash


def write_json(path: Path, value: dict) -> None:
    staging = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    staging.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    staging.replace(path)


def validate_project(path: Path) -> None:
    if path.suffix.lower() != ".3mf":
        raise ValueError("Input must be a 3MF project")
    with zipfile.ZipFile(path) as archive:
        if not any(n.startswith("3D/") and n.endswith(".model") for n in archive.namelist()):
            raise ValueError("The archive has no 3MF model; G-code alone is not a source project")
        for entry in archive.infolist():
            if not entry.filename.endswith((".config", ".json")):
                continue
            data = archive.read(entry)
            try:
                config = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                try:
                    root = ElementTree.fromstring(data)
                except ElementTree.ParseError:
                    continue
                if any(n.get("key") == "post_process" and n.get("value", "").strip() for n in root.iter()):
                    raise ValueError("External post-processing is unsupported by the native slice contract")
                continue

            def check(value):
                if isinstance(value, dict):
                    if value.get("post_process") not in (None, "", [], [""]):
                        raise ValueError("External post-processing is unsupported by the native slice contract")
                    for child in value.values():
                        check(child)
                elif isinstance(value, list):
                    for child in value:
                        check(child)
            check(config)


def gcode_properties(path: Path) -> dict:
    config, active = {}, False
    with path.open(errors="replace") as stream:
        for line in stream:
            if "CONFIG_BLOCK_START" in line:
                active = True
            elif "CONFIG_BLOCK_END" in line:
                active = False
            elif active and line.startswith(";") and " = " in line:
                key, value = line[1:].split(" = ", 1)
                config[key.strip()] = value.strip()
    return config


class Slicer:
    def __init__(self, engine: Engine, root: Path, timeout: float = 1800):
        self.engine, self.root, self.timeout = engine, root.resolve(), timeout
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        (self.root / "cache").mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self._admission = asyncio.Lock()

    @contextmanager
    def native_session(self) -> Iterator[None]:
        """Serialize a native layout operation with native slicing."""
        with self._lock:
            yield

    async def run_slice(self, function, *args, **kwargs):
        """Queue adapter calls without occupying threads needed by read operations."""
        await self._admission.acquire()
        pending = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        # A disconnected caller must not release admission while its native
        # process is still running. The thread's completion releases the gate.
        def finished(completed):
            self._admission.release()
            if not completed.cancelled():
                completed.exception()  # Consume late failures after disconnection.
        pending.add_done_callback(finished)
        return await asyncio.shield(pending)

    def job(self, job_id: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("Invalid generated job ID")
        path = self.root / "runs" / job_id / "result.json"
        if not path.is_file():
            raise ValueError("Generated job is unavailable")
        result = json.loads(path.read_text())
        if not isinstance(result, dict) or result.get("job_id") != job_id:
            raise ValueError("Generated job record is invalid")
        plates, source = result.get("plates"), result.get("source")
        if (not isinstance(plates, list) or not plates or not isinstance(source, dict)
                or not all(isinstance(p, dict) and type(p.get("plate")) is int and p["plate"] > 0
                           and isinstance(p.get("sha256"), str) for p in plates)
                or not isinstance(source.get("sha256"), str) or not isinstance(result.get("props_sha256"), str)):
            raise ValueError("Generated job artifact records are invalid")
        # Always return the canonical files actually checked, including after an
        # artifact directory is moved. Stored absolute paths are not authority.
        job = path.parent
        result["source"] = {**source, "path": str(job / "source.3mf")}
        result["plates"] = [{**p, "gcode_path": str(job / f"plate_{p['plate']}.gcode")} for p in plates]
        result["props_path"], result["log_path"] = str(job / "props.json"), str(job / "engine.log")
        return result

    def _intact(self, result: dict) -> bool:
        if not re.fullmatch(r"[0-9a-f]{32}", result["job_id"]) or not result["plates"]:
            return False
        job = self.root / "runs" / result["job_id"]
        records = [(job / "source.3mf", result["source"]["sha256"]),
                   (job / "props.json", result["props_sha256"])]
        records.extend((job / f"plate_{p['plate']}.gcode", p["sha256"]) for p in result["plates"])
        return all(path.is_file() and file_hash(path) == expected for path, expected in records)

    def slice_3mf(self, input_3mf: str, plate: int = 0, force: bool = False, allow_newer_file: bool = True) -> dict:
        if type(plate) is not int or plate < 0:
            raise ValueError("plate must be 0 (all) or a positive plate number")
        if type(force) is not bool:
            raise ValueError("force must be a boolean")
        if type(allow_newer_file) is not bool:
            raise ValueError("allow_newer_file must be a boolean")
        started = time.perf_counter()
        source = Path(input_3mf).expanduser().resolve(strict=True)
        validate_project(source)
        source_hash = file_hash(source)
        key = hashlib.sha256(json.dumps({"contract": 1, "input": source_hash,
                            "plate": plate, "allow_newer_file": allow_newer_file,
                            "engine": self.engine.identity()}, sort_keys=True).encode()).hexdigest()
        cache = self.root / "cache" / f"{key}.json"
        with self._lock:
            self.engine.verify()
            if not force and cache.is_file():
                try:
                    pointer = json.loads(cache.read_text())
                    result = self.job(pointer["job_id"])
                    record = self.root / "runs" / result["job_id"] / "result.json"
                    matches = (pointer.get("result_sha256") == file_hash(record)
                               and result.get("cache_key") == key and result.get("requested_plate") == plate
                               and result["source"]["sha256"] == source_hash
                               and result["engine"] == self.engine.identity()
                               and result.get("compatibility_flags") == {"allow_newer_file": allow_newer_file})
                    if matches and self._intact(result):
                        return {**result, "cached": True, "request_elapsed_seconds": time.perf_counter() - started,
                                "engine_status": self.engine.status()}
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            job_id = uuid.uuid4().hex
            job = self.root / "runs" / job_id
            job.mkdir()
            snapshot = job / "source.3mf"
            shutil.copyfile(source, snapshot)
            if file_hash(snapshot) != source_hash:
                raise ValueError("Source changed during snapshot; retry with a stable saved file")
            validate_project(snapshot)
            datadir = job / "native-data"
            datadir.mkdir()
            native_tmp = job / "native-tmp"
            native_tmp.mkdir()
            argv = [str(self.engine.executable), "--slice", str(plate), "--outputdir", str(job),
                    "--datadir", str(datadir), "--export-settings", str(job / "native-settings.json"), str(snapshot)]
            if allow_newer_file:
                argv.insert(1, "--allow-newer-file")
            env = {k: v for k, v in os.environ.items() if k not in (
                "DISPLAY", "WAYLAND_DISPLAY", "SNAPMAKER_ORCA_ALLOW_NEWER_FILE"
            )}
            env["LC_ALL"] = "C"
            env["TMPDIR"] = str(native_tmp) + os.sep
            run = {"job_id": job_id, "engine": self.engine.identity(), "argv": argv,
                   "source_sha256": source_hash, "requested_plate": plate,
                   "compatibility_flags": {"allow_newer_file": allow_newer_file}}
            write_json(job / "request.json", run)
            self.engine.verify()
            native_start = time.perf_counter()
            try:
                with (job / "engine.log").open("w") as log:
                    process = subprocess.Popen(argv, cwd=job, env=env, stdout=log, stderr=subprocess.STDOUT,
                                               start_new_session=(os.name != "nt"))
                    try:
                        code = process.wait(timeout=self.timeout)
                    except subprocess.TimeoutExpired as error:
                        if os.name == "nt":
                            process.kill()
                        else:
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                        write_json(job / "failure.json", {**run, "reason": "timeout", "log_path": str(job / "engine.log")})
                        raise TimeoutError(f"Native slice timed out; diagnostics: {job}") from error
            finally:
                shutil.rmtree(native_tmp)
            native_seconds = time.perf_counter() - native_start
            if code:
                write_json(job / "failure.json", {**run, "exit_code": code, "log_path": str(job / "engine.log")})
                raise RuntimeError(f"Native Snapmaker slice failed ({code}); diagnostics: {job}")
            self.engine.verify()
            files = sorted((p for p in job.glob("plate_*.gcode") if re.fullmatch(r"plate_\d+\.gcode", p.name)),
                           key=lambda p: int(p.stem.split("_")[1]))
            if not files or any(p.stat().st_size == 0 for p in files):
                raise RuntimeError(f"Native slice returned no valid G-code; diagnostics: {job}")
            numbers = [int(p.stem.split("_")[1]) for p in files]
            if plate and numbers != [plate]:
                raise RuntimeError(f"Native output plate mismatch; diagnostics: {job}")
            native_config = json.loads((job / "native-settings.json").read_text())
            props = {"scope": "native_global_export_and_per_plate_config_block; final per-region values are not exhaustively exported",
                     "native_global": native_config,
                     "plate_config": {str(n): gcode_properties(p) for n, p in zip(numbers, files)},
                     "fallback_profile": None,
                     "substitution_coverage": "Native defaults and compatibility substitutions may apply; consult settings and engine.log"}
            write_json(job / "props.json", props)
            result = {
                "status": "ready", "job_id": job_id, "cached": False,
                "cache_key": key, "requested_plate": plate,
                "compatibility_flags": run["compatibility_flags"],
                "source": {"path": str(snapshot), "sha256": source_hash},
                "engine": self.engine.identity(), "engine_status": self.engine.status(),
                "plates": [{"plate": n, "gcode_path": str(p), "sha256": file_hash(p), "size_bytes": p.stat().st_size,
                            "gcode_lines_uri": f"slicer://jobs/{job_id}/plates/{n}/lines/1/200"} for n, p in zip(numbers, files)],
                "props_path": str(job / "props.json"), "props_sha256": file_hash(job / "props.json"),
                "manifest_uri": f"slicer://jobs/{job_id}/manifest", "props_uri": f"slicer://jobs/{job_id}/props",
                "log_path": str(job / "engine.log"), "native_elapsed_seconds": native_seconds,
                "request_elapsed_seconds": time.perf_counter() - started,
            }
            write_json(job / "result.json", result)
            write_json(cache, {"job_id": job_id, "result_sha256": file_hash(job / "result.json")})
            return result

    def recover_3mf(self, gcode_file: str) -> dict:
        expected = file_hash(Path(gcode_file).expanduser().resolve(strict=True))
        candidates = {}
        for path in (self.root / "runs").glob("*/result.json"):
            try:
                result = self.job(path.parent.name)
                if any(p["sha256"] == expected for p in result["plates"]) and self._intact(result):
                    candidates[result["source"]["sha256"]] = result["source"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if len(candidates) != 1:
            raise ValueError("Original 3MF is unavailable or ambiguous; G-code cannot reconstruct its source mesh")
        return {"kind": "source_recovery", "reconstructed": False, "gcode_sha256": expected,
                **next(iter(candidates.values()))}

    def read_lines(self, job_id: str, plate: int, start: int = 1, count: int = 200) -> dict:
        if start < 1 or count < 1 or count > 1000:
            raise ValueError("Line range must start at 1 or later and contain 1..1000 lines")
        result = self.job(job_id)
        if plate not in [p["plate"] for p in result["plates"]]:
            raise ValueError("Plate is not part of this generated job")
        path = self.root / "runs" / job_id / f"plate_{plate}.gcode"
        with path.open(errors="replace") as stream:
            lines = [line.rstrip("\r\n") for line in itertools.islice(stream, start - 1, start - 1 + count)]
        return {"job_id": job_id, "plate": plate, "start_line": start, "lines": lines}
