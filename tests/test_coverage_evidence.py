"""Explicit native evidence complements, but never replaces, contract tests."""
import json
from pathlib import Path
import runpy

import pytest


PIN = '9bf2f18eaff7cc0655cbdeef6a215660085ecb0f'


def reporter():
    return runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/property_coverage.py'))['report']


def evidence(tmp_path, *, commit=PIN, status='pass'):
    path = tmp_path / 'native.json'
    path.write_text(json.dumps({
        'status': status,
        'test_source_commit': '1' * 40,
        'engine': {'source_commit': commit, 'native_version': '2.4.1',
                   'executable_sha256': 'a' * 64, 'resources_sha256': 'b' * 64},
        'cases': {
            'layer_height': {'status': 'pass', 'scope': 'extrusion layer Z spacing'},
            'outer_wall_speed': {'status': 'fail', 'scope': 'feature feedrates'},
            'inner_wall_speed': {'status': 'not_run', 'scope': 'feature feedrates'},
            'modifier': {'status': 'pass', 'scope': 'inside 20 mm/s, outside 60 mm/s'},
        },
    }))
    return path


def test_native_report_attributes_only_explicit_passes_and_keeps_scope(tmp_path):
    result = reporter()(native_report=evidence(tmp_path))
    assert result['native_fixture_passed_properties'] == ['layer_height']
    assert len(result['missing_native_semantic_coverage']) == 698
    assert 'outer_wall_speed' in result['missing_native_semantic_coverage']
    assert result['native_function_cases']['add_modifier']['scope'] == 'inside 20 mm/s, outside 60 mm/s'
    assert result['native_evidence']['test_source_commit'] == '1' * 40


@pytest.mark.parametrize('commit,status', [('2' * 40, 'pass'), (PIN, 'fail')])
def test_failed_or_different_engine_report_cannot_claim_schema_native_coverage(tmp_path, commit, status):
    result = reporter()(native_report=evidence(tmp_path, commit=commit, status=status))
    assert result['native_fixture_passed_properties'] == []
    assert len(result['missing_native_semantic_coverage']) == 699
    assert result['native_evidence']['accepted'] is False


def test_engine_hashes_and_actual_case_scope_are_required(tmp_path):
    path = evidence(tmp_path)
    data = json.loads(path.read_text())
    del data['engine']['executable_sha256']
    path.write_text(json.dumps(data))
    assert reporter()(native_report=path)['native_fixture_passed_properties'] == []
    data['engine']['executable_sha256'] = 'a' * 64
    del data['cases']['layer_height']['scope']
    path.write_text(json.dumps(data))
    assert reporter()(native_report=path)['native_fixture_passed_properties'] == []
