#!/usr/bin/env python3
"""Repeatable wrapper benchmark against an explicitly selected official engine.

Example (reports and all generated artifacts remain local):
  python scripts/benchmark_wrapper.py --app '/Applications/Snapmaker Orca.app' \
    --source-commit <40-hex-upstream-commit> --output .temp/baseline.json
Repeat after a refactor with --compare .temp/baseline.json. A mismatch in engine
identity or deterministic input hashes rejects comparison. No GUI is launched.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import platform
import resource
import statistics
import subprocess
import sys
import time
import tracemalloc
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
SIZES = {'small': 1, 'medium': 8, 'large': 48}


def generate_fixture(path, config, subdivisions):
    """A closed, outward-facing 24x24x5 mm box with increasingly dense faces.

    Fixed ZIP metadata and canonical JSON make hashes independent of clock/path.
    Vertices at face boundaries are shared, preserving a manifold shell.
    """
    if type(subdivisions) is not int or subdivisions < 1:
        raise ValueError('subdivisions must be a positive integer')
    n = subdivisions
    root = ET.Element('model', {'xmlns': NS, 'unit': 'millimeter'})
    objects = ET.SubElement(root, 'resources')
    obj = ET.SubElement(objects, 'object', id='1', type='model')
    mesh = ET.SubElement(obj, 'mesh')
    vertices = ET.SubElement(mesh, 'vertices')
    triangles = ET.SubElement(mesh, 'triangles')
    indices = {}
    faces = [((0, 0, 0), (0, 1, 0), (1, 0, 0)),
             ((0, 0, n), (1, 0, 0), (0, 1, 0)),
             ((0, 0, 0), (0, 0, 1), (0, 1, 0)),
             ((n, 0, 0), (0, 1, 0), (0, 0, 1)),
             ((0, 0, 0), (1, 0, 0), (0, 0, 1)),
             ((0, n, 0), (0, 0, 1), (1, 0, 0))]
    for origin, u, v in faces:
        grid = {}
        for j in range(n + 1):
            for i in range(n + 1):
                point = tuple(origin[k] + i * u[k] + j * v[k] for k in range(3))
                if point not in indices:
                    indices[point] = len(indices)
                    ET.SubElement(vertices, 'vertex', **{
                        axis: format(point[k] * scale / n, '.12g')
                        for k, (axis, scale) in enumerate(zip('xyz', (24, 24, 5)))})
                grid[i, j] = indices[point]
        for j in range(n):
            for i in range(n):
                a, b, c, d = (grid[i, j], grid[i + 1, j], grid[i + 1, j + 1], grid[i, j + 1])
                for triangle in ((a, b, c), (a, c, d)):
                    ET.SubElement(triangles, 'triangle', **{f'v{k + 1}': str(value) for k, value in enumerate(triangle)})
    ET.SubElement(ET.SubElement(root, 'build'), 'item', objectid='1',
                  transform='1 0 0 0 1 0 0 0 1 100 100 0')
    settings = ('<config><object id="1"><metadata key="name" value="Synthetic benchmark box"/></object>'
                '<plate><metadata key="plater_id" value="1"/><model_instance>'
                '<metadata key="object_id" value="1"/><metadata key="instance_id" value="0"/>'
                '<metadata key="identify_id" value="1"/></model_instance></plate></config>')
    members = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>',
        '3D/3dmodel.model': ET.tostring(root),
        'Metadata/model_settings.config': settings,
        'Metadata/project_settings.config': json.dumps(config, sort_keys=True, separators=(',', ':')),
    }
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, content in members.items():
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr = 0o600 << 16
            archive.writestr(info, content)
    return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size_bytes': path.stat().st_size,
            'triangles': 12 * n * n, 'vertices': len(indices), 'subdivisions': n}


def measure(operation, repetitions=10, setup=lambda: None):
    """Time warm calls without tracemalloc; measure Python allocations separately.

    Setup is excluded from both measurements. Native execution is reported outside
    these operations; process RSS below is only an overall high-water mark.
    """
    if repetitions < 10:
        raise ValueError('At least 10 warm repetitions are required')
    operation(setup())
    samples = []
    for _ in range(repetitions):
        state = setup()
        start = time.perf_counter()
        operation(state)
        samples.append(time.perf_counter() - start)
    state = setup()
    gc.collect()
    tracemalloc.start()
    try:
        operation(state)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {'sample_count': repetitions, 'wall_seconds': samples,
            'median_seconds': statistics.median(samples), 'min_seconds': min(samples),
            'max_seconds': max(samples), 'python_peak_bytes': peak}


def compare_reports(current, baseline):
    for key in ('engine', 'fixtures'):
        if current[key] != baseline[key]:
            raise ValueError(f'Cannot compare different {key}')
    if current['methodology'] != baseline['methodology']:
        raise ValueError('Cannot compare different methodology')
    if current['operations'].keys() != baseline['operations'].keys():
        raise ValueError('Cannot compare different operations')
    result = {}
    for name, now in current['operations'].items():
        before = baseline['operations'][name]
        result[name] = {
            'latency_ratio': now['median_seconds'] / before['median_seconds'] if before['median_seconds'] else None,
            'python_peak_ratio': now['python_peak_bytes'] / before['python_peak_bytes'] if before['python_peak_bytes'] else None,
        }
    return result


def run(args):
    from snapmaker_slicer_mcp.engine import Engine
    from snapmaker_slicer_mcp.editor import Editor
    from snapmaker_slicer_mcp.service import Slicer

    repo = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    scratch = output.parent / ('benchmark-' + uuid.uuid4().hex)
    scratch.mkdir(parents=True, mode=0o700)
    # This repository's native harness only reads official profiles and normalizes
    # them into a synthetic config; it never edits installed/user presets.
    spec = importlib.util.spec_from_file_location('benchmark_native_harness', repo / 'tests/native/harness.py')
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    contents = args.app.resolve() / 'Contents'
    start = time.perf_counter()
    engine = Engine(Engine.bind(contents / 'MacOS/Snapmaker_Orca', args.source_commit, contents / 'Resources'))
    startup = time.perf_counter() - start
    config = harness.official_config(engine, scratch)
    slicer = Slicer(engine, scratch / 'artifacts')
    editor = Editor(slicer)
    report = {
        'format_version': 1,
        'wrapper': {
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
            'dirty_status': subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo, text=True).splitlines(),
            'product_diff': subprocess.check_output(['git', 'diff', '--name-only', 'HEAD', '--', 'src', 'pyproject.toml', 'uv.lock'], cwd=repo, text=True).splitlines(),
        },
        'runtime': {'python': platform.python_version(), 'platform': platform.platform()},
        'engine': engine.identity(), 'engine_bind_seconds': startup,
        'scratch': str(scratch),
        'methodology': {
            'fixture_subdivisions': SIZES,
            'warmups': 1, 'repetitions': args.repetitions,
            'latency': 'perf_counter wall seconds; setup and memory probe excluded',
            'memory': 'separate tracemalloc probe; Python allocations only; excludes native RSS',
            'native': 'one forced cold slice per size; separately reports engine and wrapper residual',
            'cache': 'warm verified cache hits; original job native_elapsed_seconds is historical, not request time',
            'operations': 'public synchronous Editor/Slicer APIs; no transport overhead',
        },
        'fixtures': {}, 'operations': {}, 'native_slices': {},
    }
    for size, subdivisions in SIZES.items():
        print(f'Benchmarking {size} ({12 * subdivisions * subdivisions} triangles)', file=sys.stderr, flush=True)
        source = scratch / f'{size}.3mf'
        report['fixtures'][size] = generate_fixture(source, config, subdivisions)
        load = lambda _: editor.load_model(str(source))
        setup = lambda: editor.load_model(str(source))
        operations = report['operations']
        operations[f'{size}/load'] = measure(load, args.repetitions)
        operations[f'{size}/set_config'] = measure(
            lambda state: editor.set_config(state['project_id'], {'layer_height': 0.16}, expected_sha256=state['sha256']),
            args.repetitions, setup)
        speeds = config.get('outer_wall_speed', 20)
        modifier_speed = [20] * len(speeds) if isinstance(speeds, list) else 20
        operations[f'{size}/add_modifier'] = measure(
            lambda state: editor.add_modifier(state['project_id'], 1, [12, 12, 2.5], [4, 4, 2],
                                              {'outer_wall_speed': modifier_speed}, expected_sha256=state['sha256']),
            args.repetitions, setup)
        start = time.perf_counter()
        job = slicer.slice_3mf(str(source), plate=1, force=True)
        wall = time.perf_counter() - start
        assert job['cached'] is False
        report['native_slices'][size] = {
            'sample_count': 1, 'cached': False, 'wall_seconds': wall,
            'native_seconds': job['native_elapsed_seconds'],
            'wrapper_residual_seconds': wall - job['native_elapsed_seconds'],
        }

        def cached_slice(_):
            cached = slicer.slice_3mf(str(source), plate=1)
            if cached['cached'] is not True or cached['job_id'] != job['job_id']:
                raise RuntimeError('Cache miss invalidates the cache-only measurement')
            return cached

        operations[f'{size}/cache_hit'] = {**measure(cached_slice, args.repetitions), 'cached': True, 'native_seconds': 0}
        operations[f'{size}/artifact_manifest'] = measure(lambda _: slicer.job(job['job_id']), args.repetitions)
        operations[f'{size}/lines'] = measure(lambda _: slicer.read_lines(job['job_id'], 1, 1, 200), args.repetitions)
        if hashlib.sha256(source.read_bytes()).hexdigest() != report['fixtures'][size]['sha256']:
            raise RuntimeError('Synthetic source changed during benchmark')
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report['wrapper_process_peak_rss_bytes'] = rss if sys.platform == 'darwin' else rss * 1024
    if args.compare:
        report['comparison'] = compare_reports(report, json.loads(args.compare.read_text()))
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=10)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    if args.repetitions < 10:
        parser.error('--repetitions must be at least 10')
    run(args)
    print(str(args.output.resolve()))


if __name__ == '__main__':
    main()
