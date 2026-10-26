"""Selected tests must execute; the complete profile also forbids deselection."""
import os

import pytest


class _ZeroSkipPolicy:
    def __init__(self):
        self.rejected = 0
        self.deselected = 0
        self.complete_suite = os.environ.get('SNAPMAKER_COMPLETE_SUITE') == '1'

    def pytest_runtest_logreport(self, report):
        if report.skipped or hasattr(report, 'wasxfail'):
            self.rejected += 1

    def pytest_collectreport(self, report):
        if report.skipped:
            self.rejected += 1

    def pytest_deselected(self, items):
        if self.complete_suite:
            self.deselected += len(items)

    def pytest_sessionfinish(self, session, exitstatus):
        if (self.rejected or self.deselected) and exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter):
        if self.rejected or self.deselected:
            terminalreporter.write_sep('=',
                f'Zero-skip policy rejected {self.rejected} skipped/xfail outcomes; '
                f'{self.deselected} deselected tests in the complete suite', red=True)


def pytest_configure(config):
    config.pluginmanager.register(_ZeroSkipPolicy(), 'snapmaker-zero-skip')


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    for item in items:
        if 'native_engine' in getattr(item, 'fixturenames', ()):
            item.add_marker(pytest.mark.native)
