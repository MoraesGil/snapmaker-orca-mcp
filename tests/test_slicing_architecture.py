"""Boundary and orchestration checks for the headless slicing application."""
import ast
from pathlib import Path

import pytest


def test_domain_and_application_cannot_import_infrastructure():
    package = Path(__file__).parents[1] / 'src/snapmaker_slicer_mcp'
    banned_external = {'pathlib', 'subprocess', 'shutil', 'zipfile', 'os', 'socket',
                       'httpx', 'fastmcp', 'starlette', 'mcp'}
    banned_internal = {'adapters', 'engine', 'service', 'editor', 'presets', 'project',
                       'native_ops', 'server', 'http_server', 'rest', 'cli'}
    failures = []
    for directory in ('domain', 'application'):
        for path in (package / directory).rglob('*.py'):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    parent = ['snapmaker_slicer_mcp', *path.relative_to(package).parent.parts]
                    base = parent[:len(parent) - node.level + 1] if node.level else []
                    module = '.'.join([*base, node.module or '']).rstrip('.')
                    modules = [module, *(module + '.' + alias.name for alias in node.names)]
                else:
                    continue
                for module in modules:
                    parts = module.split('.')
                    forbidden = (parts[0] in banned_external or
                        (len(parts) > 1 and parts[0] == 'snapmaker_slicer_mcp' and parts[1] in banned_internal))
                    if forbidden:
                        failures.append((str(path.relative_to(package)), node.lineno, module))
    assert not failures


def test_bounded_lines_stops_iterator_at_limit():
    from snapmaker_slicer_mcp.application.services.lines import bounded_lines
    consumed = []
    def source():
        for n in range(10000):
            consumed.append(n)
            yield f'G1 X{n}\r\n'
    assert bounded_lines(source(), 'job', 2, 3, 2) == {
        'job_id': 'job', 'plate': 2, 'start_line': 3, 'lines': ['G1 X2', 'G1 X3']}
    assert consumed == [0, 1, 2, 3]
    with pytest.raises(ValueError, match='1..1000'):
        bounded_lines([], 'job', 1, 1, 1001)


def test_slicing_use_case_accepts_ports_and_orders_verification():
    from snapmaker_slicer_mcp.application.contracts.slicing import SliceWorkspace, SourceSnapshot
    from snapmaker_slicer_mcp.application.services.admission import NativeAdmission
    from snapmaker_slicer_mcp.application.services.slicing import Slicing
    events = []
    class Engine:
        def verify(self): events.append('verify')
        def identity(self): return {'source_commit': '1' * 40}
        def status(self): return {'update_available': False}
    class Repository:
        def prepare_source(self, path):
            events.append('prepare')
            return SourceSnapshot(path, 'a' * 64)
        def cached(self, *args):
            events.append('cache')
            return None
        def create(self, source):
            events.append('snapshot')
            return SliceWorkspace('b' * 32, '/private/job', source)
        def publish(self, workspace, *args):
            events.append('publish')
            return {'job_id': workspace.job_id, 'cached': False}
    class Runner:
        def run(self, *args):
            events.append('native')
            return 0.25
    service = Slicing(Engine(), Repository(), Runner(), NativeAdmission())
    assert service.slice_3mf('fixture.3mf')['job_id'] == 'b' * 32
    assert events == ['prepare', 'verify', 'cache', 'snapshot', 'verify', 'native', 'verify', 'publish']


def test_slicing_use_case_validates_before_using_ports():
    from snapmaker_slicer_mcp.application.services.slicing import Slicing
    service = object.__new__(Slicing)
    with pytest.raises(ValueError, match='plate must'):
        service.slice_3mf('fixture.3mf', plate=True)
