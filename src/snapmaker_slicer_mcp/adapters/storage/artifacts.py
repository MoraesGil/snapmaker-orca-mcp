"""Filesystem snapshots, verified cache records and native output artifacts."""
from __future__ import annotations

import json
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import TypeGuard
from xml.etree import ElementTree

from ...application.contracts.artifacts import GcodeLines
from ...application.contracts.slicing import SliceRequest, SliceWorkspace, SourceSnapshot
from ...application.services.lines import bounded_lines
from ...domain.entities.slicing import JobManifest, PlateRecord
from ...domain.types.json import JsonObject, JsonValue, json_object, decode_json, decode_object
from ..orca.engine import file_hash


def write_json(path: Path, value: JsonObject) -> None:
    staging = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    staging.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    staging.replace(path)


def _reject_post_process(value: JsonValue) -> None:
    if isinstance(value, dict):
        if value.get('post_process') not in (None, '', [], ['']):
            raise ValueError('External post-processing is unsupported by the native slice contract')
        for child in value.values():
            _reject_post_process(child)
    elif isinstance(value, list):
        for child in value:
            _reject_post_process(child)


def validate_project(path: Path) -> None:
    if path.suffix.lower() != '.3mf':
        raise ValueError('Input must be a 3MF project')
    with zipfile.ZipFile(path) as archive:
        if not any(n.startswith('3D/') and n.endswith('.model') for n in archive.namelist()):
            raise ValueError('The archive has no 3MF model; G-code alone is not a source project')
        for entry in archive.infolist():
            if not entry.filename.endswith(('.config', '.json')):
                continue
            data = archive.read(entry)
            try:
                config = decode_json(data)
            except (ValueError, UnicodeDecodeError):
                try:
                    root = ElementTree.fromstring(data)
                except ElementTree.ParseError:
                    continue
                if any(n.get('key') == 'post_process' and n.get('value', '').strip() for n in root.iter()):
                    raise ValueError('External post-processing is unsupported by the native slice contract')
                continue
            _reject_post_process(config)


def gcode_properties(path: Path) -> JsonObject:
    config: JsonObject = {}
    active = False
    with path.open(errors='replace') as stream:
        for line in stream:
            if 'CONFIG_BLOCK_START' in line:
                active = True
            elif 'CONFIG_BLOCK_END' in line:
                active = False
            elif active and line.startswith(';') and ' = ' in line:
                key, value = line[1:].split(' = ', 1)
                config[key.strip()] = value.strip()
    return config


def _manifest(value: JsonObject) -> TypeGuard[JobManifest]:
    text = ('status', 'job_id', 'cache_key', 'props_path', 'props_sha256', 'manifest_uri', 'props_uri', 'log_path')
    if not all(isinstance(value.get(key), str) for key in text):
        return False
    if type(value.get('cached')) is not bool or type(value.get('requested_plate')) is not int:
        return False
    if not all(isinstance(value.get(key), (int, float)) for key in ('native_elapsed_seconds', 'request_elapsed_seconds')):
        return False
    source, plates, flags = value.get('source'), value.get('plates'), value.get('compatibility_flags')
    if not isinstance(source, dict) or not all(isinstance(source.get(key), str) for key in ('path', 'sha256')):
        return False
    if not isinstance(flags, dict) or type(flags.get('allow_newer_file')) is not bool:
        return False
    if not isinstance(value.get('engine'), dict) or not isinstance(value.get('engine_status'), dict):
        return False
    if not isinstance(plates, list) or not plates:
        return False
    for plate in plates:
        if not isinstance(plate, dict):
            return False
        number = plate.get('plate')
        if type(number) is not int or number <= 0 or type(plate.get('size_bytes')) is not int:
            return False
        if not all(isinstance(plate.get(key), str) for key in ('gcode_path', 'sha256', 'gcode_lines_uri')):
            return False
    return True


class FilesystemArtifacts:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        (self.root / 'runs').mkdir(parents=True, exist_ok=True)
        (self.root / 'cache').mkdir(exist_ok=True)

    def prepare_source(self, input_3mf: str) -> SourceSnapshot:
        source = Path(input_3mf).expanduser().resolve(strict=True)
        validate_project(source)
        return SourceSnapshot(str(source), file_hash(source))

    def job(self, job_id: str) -> JobManifest:
        if not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise ValueError('Invalid generated job ID')
        path = self.root / 'runs' / job_id / 'result.json'
        if not path.is_file():
            raise ValueError('Generated job is unavailable')
        result = decode_object(path.read_text(), 'Generated job record')
        if result.get('job_id') != job_id:
            raise ValueError('Generated job record is invalid')
        if not _manifest(result):
            raise ValueError('Generated job artifact records are invalid')
        # Recorded absolute paths cannot redirect reads, including after a move.
        job = path.parent
        result['source'] = {**result['source'], 'path': str(job / 'source.3mf')}
        result['plates'] = [{**p, 'gcode_path': str(job / f"plate_{p['plate']}.gcode")} for p in result['plates']]
        result['props_path'], result['log_path'] = str(job / 'props.json'), str(job / 'engine.log')
        return result

    def intact(self, result: JobManifest) -> bool:
        if not re.fullmatch(r'[0-9a-f]{32}', result['job_id']) or not result['plates']:
            return False
        job = self.root / 'runs' / result['job_id']
        records = [(job / 'source.3mf', result['source']['sha256']), (job / 'props.json', result['props_sha256'])]
        records.extend((job / f"plate_{p['plate']}.gcode", p['sha256']) for p in result['plates'])
        return all(path.is_file() and file_hash(path) == expected for path, expected in records)

    def cached(self, key: str, request: SliceRequest, source: SourceSnapshot,
               engine: JsonObject) -> JsonObject | None:
        cache = self.root / 'cache' / f'{key}.json'
        if not cache.is_file():
            return None
        try:
            pointer = decode_object(cache.read_text())
            job_id = pointer['job_id']
            if not isinstance(job_id, str):
                return None
            result = self.job(job_id)
            record = self.root / 'runs' / result['job_id'] / 'result.json'
            matches = (pointer.get('result_sha256') == file_hash(record)
                and result['cache_key'] == key and result['requested_plate'] == request.plate
                and result['source']['sha256'] == source.sha256 and result['engine'] == engine
                and result['compatibility_flags'] == {'allow_newer_file': request.allow_newer_file})
            return json_object(result) if matches and self.intact(result) else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def create(self, source: SourceSnapshot) -> SliceWorkspace:
        job_id = uuid.uuid4().hex
        job = self.root / 'runs' / job_id
        job.mkdir()
        snapshot = job / 'source.3mf'
        shutil.copyfile(source.path, snapshot)
        if file_hash(snapshot) != source.sha256:
            raise ValueError('Source changed during snapshot; retry with a stable saved file')
        validate_project(snapshot)
        return SliceWorkspace(job_id, str(job), SourceSnapshot(str(snapshot), source.sha256))

    def publish(self, workspace: SliceWorkspace, key: str, request: SliceRequest,
                engine: JsonObject, engine_status: JsonObject, native_seconds: float,
                request_seconds: float) -> JsonObject:
        job, job_id = Path(workspace.directory), workspace.job_id
        files = sorted((p for p in job.glob('plate_*.gcode') if re.fullmatch(r'plate_\d+\.gcode', p.name)),
                       key=lambda p: int(p.stem.split('_')[1]))
        if not files or any(p.stat().st_size == 0 for p in files):
            raise RuntimeError(f'Native slice returned no valid G-code; diagnostics: {job}')
        numbers = [int(p.stem.split('_')[1]) for p in files]
        if request.plate and numbers != [request.plate]:
            raise RuntimeError(f'Native output plate mismatch; diagnostics: {job}')
        native_config = decode_json((job / 'native-settings.json').read_text())
        props: JsonObject = {
            'scope': 'native_global_export_and_per_plate_config_block; final per-region values are not exhaustively exported',
            'native_global': native_config,
            'plate_config': {str(n): gcode_properties(p) for n, p in zip(numbers, files)},
            'fallback_profile': None,
            'substitution_coverage': 'Native defaults and compatibility substitutions may apply; consult settings and engine.log'}
        write_json(job / 'props.json', props)
        plates: list[PlateRecord] = [{'plate': n, 'gcode_path': str(p), 'sha256': file_hash(p),
            'size_bytes': p.stat().st_size, 'gcode_lines_uri': f'slicer://jobs/{job_id}/plates/{n}/lines/1/200'}
            for n, p in zip(numbers, files)]
        result: JobManifest = {
            'status': 'ready', 'job_id': job_id, 'cached': False, 'cache_key': key, 'requested_plate': request.plate,
            'compatibility_flags': {'allow_newer_file': request.allow_newer_file},
            'source': {'path': workspace.source.path, 'sha256': workspace.source.sha256},
            'engine': engine, 'engine_status': engine_status, 'plates': plates,
            'props_path': str(job / 'props.json'), 'props_sha256': file_hash(job / 'props.json'),
            'manifest_uri': f'slicer://jobs/{job_id}/manifest', 'props_uri': f'slicer://jobs/{job_id}/props',
            'log_path': str(job / 'engine.log'), 'native_elapsed_seconds': native_seconds,
            'request_elapsed_seconds': request_seconds}
        payload = json_object(result)
        write_json(job / 'result.json', payload)
        write_json(self.root / 'cache' / f'{key}.json', {'job_id': job_id, 'result_sha256': file_hash(job / 'result.json')})
        return payload

    def recover_3mf(self, gcode_file: str) -> JsonObject:
        expected = file_hash(Path(gcode_file).expanduser().resolve(strict=True))
        candidates: dict[str, JsonObject] = {}
        for path in (self.root / 'runs').glob('*/result.json'):
            try:
                result = self.job(path.parent.name)
                if any(p['sha256'] == expected for p in result['plates']) and self.intact(result):
                    candidates[result['source']['sha256']] = json_object(result['source'])
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if len(candidates) != 1:
            raise ValueError('Original 3MF is unavailable or ambiguous; G-code cannot reconstruct its source mesh')
        return {'kind': 'source_recovery', 'reconstructed': False, 'gcode_sha256': expected,
                **next(iter(candidates.values()))}

    def read_lines(self, job_id: str, plate: int, start: int = 1, count: int = 200) -> GcodeLines:
        # Validate the range even when the job/plate is unavailable.
        bounded_lines((), job_id, plate, start, count)
        result = self.job(job_id)
        if plate not in [p['plate'] for p in result['plates']]:
            raise ValueError('Plate is not part of this generated job')
        with (self.root / 'runs' / job_id / f'plate_{plate}.gcode').open(errors='replace') as stream:
            return bounded_lines(stream, job_id, plate, start, count)
