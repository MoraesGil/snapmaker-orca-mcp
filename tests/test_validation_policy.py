"""Exercise the repository validation policy in isolated real pytest processes."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from xml.etree import ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]


def isolated_pytest(tmp_path, source, *, args=(), native=False, environment=None):
    project = tmp_path / 'isolated'
    project.mkdir()
    policy = ROOT / 'tests/conftest.py'
    if policy.is_file():
        shutil.copyfile(policy, project / 'conftest.py')
    (project / 'pytest.ini').write_text(
        '[pytest]\nmarkers = native: requires the selected official engine\nxfail_strict = true\n')
    target = project
    if native:
        target = project / 'native'
        target.mkdir()
        (target / '__init__.py').touch()
        for name in ('conftest.py', 'harness.py'):
            shutil.copyfile(ROOT / 'tests/native' / name, target / name)
    (target / 'test_case.py').write_text(source)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('SNAPMAKER_') and key != 'PYTEST_ADDOPTS'}
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONPATH=str(ROOT / 'src'))
    env.update(environment or {})
    junit = project / 'results.xml'
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '--strict-markers',
         '--junitxml', str(junit), *args],
        cwd=project, env=env, capture_output=True, text=True, timeout=30,
    )
    return result, list(ET.parse(junit).getroot().iter('testcase'))


def test_zero_skip_policy_accepts_a_clean_selected_suite(tmp_path):
    result, cases = isolated_pytest(tmp_path, 'def test_ok():\n    assert 2 + 2 == 4\n')
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(cases) == 1
    assert all(cases[0].find(tag) is None for tag in ('skipped', 'failure', 'error'))


@pytest.mark.parametrize('source', [
    'import pytest\ndef test_case():\n    pytest.skip("runtime")\n',
    'import pytest\n@pytest.mark.skip(reason="marker")\ndef test_case():\n    assert False\n',
    'import pytest\n@pytest.mark.skipif(True, reason="condition")\ndef test_case():\n    assert False\n',
    'import pytest\npytest.skip("collection", allow_module_level=True)\n',
    'import pytest\ndef test_case():\n    pytest.xfail("expected failure")\n',
    'import pytest\n@pytest.mark.xfail(strict=False)\ndef test_case():\n    assert True\n',
    'import pytest\n@pytest.fixture\ndef setup():\n    pytest.skip("setup")\ndef test_case(setup):\n    assert True\n',
    'import pytest\n@pytest.fixture\ndef setup():\n    yield\n    pytest.skip("teardown")\ndef test_case(setup):\n    assert True\n',
])
def test_zero_skip_policy_rejects_unexecuted_or_expected_failure_outcomes(tmp_path, source):
    result, _ = isolated_pytest(tmp_path, source)
    assert result.returncode != 0, 'A skipped/xfail outcome was accepted as a green suite'
    assert 'zero-skip' in result.stdout.lower(), result.stdout + result.stderr


PROFILE_SOURCE = '''import pytest
@pytest.fixture
def native_engine():
    raise AssertionError("An engine was not provisioned for this portable profile")
@pytest.fixture
def bound_project(native_engine):
    return native_engine
def test_native_output(bound_project):
    assert bound_project
def test_contract():
    assert 2 + 2 == 4
'''


def test_portable_profile_explicitly_selects_only_engine_independent_tests(tmp_path):
    result, cases = isolated_pytest(tmp_path, PROFILE_SOURCE, args=('-m', 'not native'))
    assert result.returncode == 0, result.stdout + result.stderr
    assert [case.get('name') for case in cases] == ['test_contract']
    assert '1 deselected' in result.stdout


def test_complete_suite_rejects_deselected_native_cases(tmp_path):
    result, _ = isolated_pytest(tmp_path, PROFILE_SOURCE, args=('-m', 'not native'),
                               environment={'SNAPMAKER_COMPLETE_SUITE': '1'})
    assert result.returncode != 0, 'A partial portable selection certified the complete suite'
    assert 'zero-skip' in result.stdout.lower(), result.stdout + result.stderr


@pytest.mark.parametrize('enabled', ['0', '1'])
def test_selected_native_cases_fail_instead_of_skipping_when_engine_is_unavailable(tmp_path, enabled):
    result, cases = isolated_pytest(tmp_path,
        'def test_native_required(native_engine):\n    assert native_engine\n', native=True,
        environment={'SNAPMAKER_NATIVE': enabled, 'SNAPMAKER_NATIVE_APP': str(tmp_path / 'absent.app')})
    assert result.returncode != 0, 'The selected native suite accepted an unavailable engine'
    assert len(cases) == 1 and cases[0].find('skipped') is None
    assert cases[0].find('error') is not None


@pytest.mark.parametrize('payload', [
    '<testsuites><testsuite tests="1" skipped="1"><testcase><skipped/></testcase></testsuite></testsuites>',
    '<testsuites><testsuite tests="1"><testcase><failure/></testcase></testsuite></testsuites>',
    '<testsuites><testsuite tests="1"><testcase><error/></testcase></testsuite></testsuites>',
    '<testsuites><testsuite tests="1" skipped="1"><testcase/></testsuite></testsuites>',
    '<testsuites/>',
    '<broken',
])
def test_required_runner_rejects_skipped_failed_empty_or_invalid_junit(tmp_path, payload):
    report = tmp_path / 'result.xml'
    report.write_text(payload)
    runner = runpy.run_path(str(ROOT / 'scripts/run_native_regressions.py'))
    totals = runner.get('junit_totals', lambda path: {'passed': True})(report)
    assert totals['passed'] is False, 'Incomplete JUnit was accepted as complete-suite evidence'


def test_required_runner_requires_an_existing_junit_with_executed_tests(tmp_path):
    runner = runpy.run_path(str(ROOT / 'scripts/run_native_regressions.py'))
    totals = runner.get('junit_totals', lambda path: {'passed': True})(tmp_path / 'absent.xml')
    assert totals['passed'] is False
    report = tmp_path / 'clean.xml'
    report.write_text('<testsuites><testsuite tests="2" skipped="0" failures="0" errors="0">'
                      '<testcase/><testcase/></testsuite></testsuites>')
    assert runner['junit_totals'](report) == {
        'tests': 2, 'skipped': 0, 'failures': 0, 'errors': 0, 'passed': True,
    }


@pytest.mark.parametrize('skip_case', [False, True])
def test_required_runner_checks_junit_even_when_native_case_report_claims_pass(tmp_path, skip_case):
    """A current-run case report cannot conceal a skipped unrelated test."""
    project = tmp_path / 'repository'
    (project / 'scripts').mkdir(parents=True)
    (project / 'tests').mkdir()
    shutil.copyfile(ROOT / 'scripts/run_native_regressions.py', project / 'scripts/run_native_regressions.py')
    app = project / 'synthetic.app'
    binary = app / 'Contents/MacOS/Snapmaker_Orca'
    binary.parent.mkdir(parents=True)
    binary.touch()  # Runner-boundary fixture only; no native slicing is claimed.
    keys = ('outer_wall_speed', 'inner_wall_speed', 'layer_height', 'wall_loops',
            'sparse_infill_density', 'enable_support', 'support_type',
            'nozzle_temperature', 'cool_plate_temp', 'additional_cooling_fan_speed',
            'modifier', 'sparse_infill_speed', 'top_surface_speed',
            'internal_solid_infill_speed', 'initial_layer_speed', 'filament_flow_ratio', 'mcp_http')
    fixture = '''import json, os
from pathlib import Path
import pytest
def test_fixture_report():
    path = Path(os.environ['SNAPMAKER_NATIVE_REPORT'])
    data = json.loads(path.read_text())
    app = Path(os.environ['SNAPMAKER_NATIVE_APP']).resolve() / 'Contents'
    data.update(engine_run_id=data['run_id'], engine={
        'source_commit': os.environ['SNAPMAKER_NATIVE_COMMIT'],
        'executable': str(app / 'MacOS/Snapmaker_Orca'),
        'resources': str(app / 'Resources'), 'native_version': '2.4.1',
        'executable_sha256': 'a' * 64, 'resources_sha256': 'b' * 64})
    data['cases'] = {key: {'status': 'pass', 'run_id': data['run_id']} for key in KEYS}
    path.write_text(json.dumps(data))
    if SKIP:
        pytest.skip('Selected unrelated case was not executed')
'''
    (project / 'tests/test_fixture.py').write_text(
        fixture.replace('KEYS', repr(keys)).replace('SKIP', repr(skip_case)))
    for command in (['git', 'init', '-q', str(project)], ['git', 'add', '.'],
                    ['git', '-c', 'user.name=Synthetic Fixture', '-c',
                     'user.email=fixture@example.invalid', 'commit', '-qm', 'test: seed fixture']):
        subprocess.run(command, cwd=project, capture_output=True, text=True, check=True)
    report = tmp_path / 'report/result.json'
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('SNAPMAKER_') and key != 'PYTEST_ADDOPTS'}
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    result = subprocess.run([sys.executable, str(project / 'scripts/run_native_regressions.py'),
                             '--all-tests', '--app', str(app), '--source-commit', 'c' * 40,
                             '--report', str(report)], env=env, capture_output=True, text=True, timeout=30)
    evidence = json.loads(report.read_text())
    if skip_case:
        assert result.returncode != 0, 'A skipped test certified the complete native run'
        assert evidence['status'] == 'fail'
        assert evidence['test_totals']['skipped'] == 1
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert evidence['status'] == 'pass'
        assert evidence['test_totals'] == {
            'tests': 1, 'skipped': 0, 'failures': 0, 'errors': 0, 'passed': True,
        }
