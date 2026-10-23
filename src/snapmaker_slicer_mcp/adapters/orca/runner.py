"""Native subprocess lifecycle and job-local diagnostics."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from ...application.contracts.slicing import SliceRequest, SliceWorkspace
from ...domain.types.json import JsonObject
from ..storage.artifacts import write_json


class OrcaProcess:
    def __init__(self, executable: Path, operating_system: str = os.name) -> None:
        self.executable, self.operating_system = executable, operating_system

    def run(self, workspace: SliceWorkspace, request: SliceRequest,
            engine: JsonObject, timeout: float) -> float:
        job = Path(workspace.directory)
        datadir, native_tmp = job / 'native-data', job / 'native-tmp'
        datadir.mkdir()
        native_tmp.mkdir()
        try:
            argv = [str(self.executable), '--slice', str(request.plate), '--outputdir', str(job),
                    '--datadir', str(datadir), '--export-settings', str(job / 'native-settings.json'), workspace.source.path]
            if request.allow_newer_file:
                argv.insert(1, '--allow-newer-file')
            env = {key: value for key, value in os.environ.items() if key not in (
                'DISPLAY', 'WAYLAND_DISPLAY', 'SNAPMAKER_ORCA_ALLOW_NEWER_FILE')}
            env['LC_ALL'], env['TMPDIR'] = 'C', str(native_tmp) + os.sep
            run: JsonObject = {'job_id': workspace.job_id, 'engine': engine, 'argv': [arg for arg in argv],
                'source_sha256': workspace.source.sha256, 'requested_plate': request.plate,
                'compatibility_flags': {'allow_newer_file': request.allow_newer_file}}
            write_json(job / 'request.json', run)
            native_start = time.perf_counter()
            with (job / 'engine.log').open('w') as log:
                process = subprocess.Popen(argv, cwd=job, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=(self.operating_system != 'nt'))
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    if self.operating_system == 'nt':
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    write_json(job / 'failure.json', {**run, 'reason': 'timeout', 'log_path': str(job / 'engine.log')})
                    raise TimeoutError(f'Native slice timed out; diagnostics: {job}') from error
            native_seconds = time.perf_counter() - native_start
            if code:
                write_json(job / 'failure.json', {**run, 'exit_code': code, 'log_path': str(job / 'engine.log')})
                raise RuntimeError(f'Native Snapmaker slice failed ({code}); diagnostics: {job}')
            return native_seconds
        finally:
            shutil.rmtree(native_tmp)
