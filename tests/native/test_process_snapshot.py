"""Process identity regressions independent of the native engine."""
import subprocess
import sys

from .http_processes import _snapshot


def test_snapshot_preserves_long_command_with_narrow_columns(tmp_path, monkeypatch):
    monkeypatch.setenv('COLUMNS', '80')
    marker = str(tmp_path / ('long-command-' * 20) / 'owned-child')
    argv = [sys.executable, '-c', 'import time; time.sleep(60)', marker]
    process = subprocess.Popen(argv, start_new_session=True)
    try:
        row = _snapshot()[process.pid]
        assert marker in row[3], 'Scratch identity was truncated by ps output width'
        assert row[3].endswith(marker)
        assert row[2], 'Start time remains part of the process identity'
    finally:
        process.kill()
        process.wait(timeout=10)


def test_signal_rejects_changed_start_time_or_command(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from .http_processes import ProcessOwner

    owner = ProcessOwner(SimpleNamespace(pid=123), tmp_path)
    original = (1, 123, 'Sat Oct 24 12:00:00 2026', '/synthetic/owned-child', 'S')
    signals = []
    monkeypatch.setattr('os.kill', lambda *args: signals.append(args))
    monkeypatch.setattr('os.killpg', lambda *args: signals.append(args))
    for index in (2, 3):
        changed = list(original)
        changed[index] += '-changed'
        monkeypatch.setattr(owner, 'capture', lambda: {123: tuple(changed)})
        owner.signal(123, original, 15)
    assert signals == [], 'Reused PID identities must never receive a signal'
