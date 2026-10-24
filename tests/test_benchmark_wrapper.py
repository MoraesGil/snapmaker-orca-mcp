"""Reproducibility and comparison checks for the standalone wrapper benchmark."""
import importlib.util
from pathlib import Path
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location('benchmark_wrapper', Path(__file__).parents[1] / 'scripts/benchmark_wrapper.py')


def benchmark():
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def test_synthetic_fixtures_are_deterministic_closed_and_scale(tmp_path):
    module = benchmark()
    small = module.generate_fixture(tmp_path / 'small.3mf', {'layer_height': '0.2'}, 1)
    again = module.generate_fixture(tmp_path / 'again.3mf', {'layer_height': '0.2'}, 1)
    large = module.generate_fixture(tmp_path / 'large.3mf', {'layer_height': '0.2'}, 4)
    assert small['sha256'] == again['sha256']
    assert small['triangles'] == 12 and large['triangles'] == 192
    assert large['size_bytes'] > small['size_bytes']
    from xml.etree import ElementTree as ET
    with zipfile.ZipFile(tmp_path / 'large.3mf') as archive:
        root = ET.fromstring(archive.read('3D/3dmodel.model'))
    edges = {}
    for triangle in root.findall('.//{*}triangle'):
        vertices = [int(triangle.get('v' + str(i))) for i in (1, 2, 3)]
        for a, b in zip(vertices, vertices[1:] + vertices[:1]):
            key = tuple(sorted((a, b)))
            edges[key] = edges.get(key, 0) + 1
    assert set(edges.values()) == {2}


def test_measure_records_ten_warm_samples_and_separate_memory_probe():
    module = benchmark()
    calls = []
    measured = module.measure(lambda _: calls.append(bytearray(1024)), repetitions=10)
    assert measured['sample_count'] == 10
    assert len(measured['wall_seconds']) == 10
    assert len(calls) == 12  # warmup, ten timed calls, separate memory probe
    assert measured['python_peak_bytes'] > 0
    with pytest.raises(ValueError, match='10'):
        module.measure(lambda _: None, repetitions=9)


def report(median=1.0, fixture_hash='hash'):
    return {'engine': {'source_commit': 'a' * 40}, 'methodology': {'fixture_subdivisions': {'small': 1}},
            'fixtures': {'small': {'sha256': fixture_hash}}, 'operations': {
                'small/load': {'median_seconds': median, 'python_peak_bytes': 100}}}


def test_comparison_reports_ratios_and_rejects_incompatible_fixtures():
    module = benchmark()
    compared = module.compare_reports(report(0.5), report())
    assert compared['small/load']['latency_ratio'] == 0.5
    with pytest.raises(ValueError, match='fixtures'):
        module.compare_reports(report(fixture_hash='changed'), report())
    changed = report()
    changed['engine']['source_commit'] = 'b' * 40
    with pytest.raises(ValueError, match='engine'):
        module.compare_reports(changed, report())
