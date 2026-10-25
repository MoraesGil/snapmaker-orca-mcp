"""Own and clean a test-server subtree, including detached native sessions.

Process metadata stays local. A unique scratch directory is the fallback identity
for native commands reparented before the monitor observes their PPID. Nothing
is selected by binary name, installed port, or broad user-process matching.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import threading
import time


def _snapshot():
    result = subprocess.run(
        ['ps', '-axo', 'pid=,ppid=,pgid=,stat=,lstart=,command='],
        capture_output=True, text=True, check=True, timeout=5,
    )
    processes = {}
    for line in result.stdout.splitlines():
        fields = line.split(None, 9)
        if len(fields) == 10:
            pid, parent, group = map(int, fields[:3])
            processes[pid] = (parent, group, ' '.join(fields[4:9]), fields[9], fields[3])
    return processes


class ProcessOwner:
    def __init__(self, process, scratch):
        self.process = process
        self.marker = str(Path(scratch).resolve()) + '/'
        self.known = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._monitor, daemon=True)

    def capture(self):
        current = _snapshot()
        owned = set()
        # The marker is unique to this test. Native --datadir/outputdir/input
        # arguments keep this identity even after an early server exit.
        owned.update(pid for pid, row in current.items() if self.marker in row[3])
        with self.lock:
            owned.update(pid for pid, row in self.known.items()
                         if pid in current and current[pid][2:4] == row[2:4])
        while True:
            children = {pid for pid, row in current.items() if row[0] in owned}
            if children <= owned:
                break
            owned.update(children)
        with self.lock:
            for pid in owned:
                if pid in current and pid != os.getpid():
                    self.known[pid] = current[pid]
        return current

    def _monitor(self):
        while not self.stop.is_set():
            try:
                self.capture()
            except Exception as error:
                self.error = error
                return
            self.stop.wait(.1)

    def signal(self, pid, row, sig):
        # Verify both start time and command before every signal: a reused PID
        # must never receive a signal intended for this test's earlier process.
        current = self.capture().get(pid)
        if current is None or current[2:4] != row[2:4] or current[4].startswith('Z'):
            return
        try:
            if row[1] == pid and row[1] != os.getpgrp():
                os.killpg(row[1], sig)
            else:
                os.kill(pid, sig)
        except ProcessLookupError:
            pass

    def cleanup(self):
        try:
            self.capture()
            with self.lock:
                rows = list(self.known.items())
            # Kill detached workers first while the server can still reap them.
            for pid, row in rows:
                if pid != self.process.pid:
                    self.signal(pid, row, signal.SIGTERM)
            root = self.known.get(self.process.pid)
            if root:
                self.signal(self.process.pid, root, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if root:
                    self.signal(self.process.pid, root, signal.SIGKILL)
                self.process.wait(timeout=10)
            deadline = time.monotonic() + 10
            while True:
                current = self.capture()
                with self.lock:
                    live = [(pid, row) for pid, row in self.known.items()
                            if pid in current and current[pid][2:4] == row[2:4]
                            and not current[pid][4].startswith('Z')]
                if not live:
                    break
                for pid, row in live:
                    self.signal(pid, row, signal.SIGKILL)
                assert time.monotonic() < deadline, 'Owned native processes did not exit'
                time.sleep(.05)
            assert self.error is None, 'Dedicated process monitor failed'
        finally:
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=10)
            self.stop.set()
            self.thread.join(timeout=6)
            assert not self.thread.is_alive(), 'Process monitor did not stop'


@contextmanager
def owned_process(argv, scratch, log, cwd=None):
    process = subprocess.Popen(argv, cwd=cwd, stdout=log, stderr=log, start_new_session=True)
    owner = ProcessOwner(process, scratch)
    owner.thread.start()
    try:
        yield process
    finally:
        owner.cleanup()
