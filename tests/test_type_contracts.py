"""Public types reject invalid consumers before code reaches runtime adapters."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


_IMPORTS = '''from snapmaker_slicer_mcp.domain.types.values import ObjectId, XYZ
from snapmaker_slicer_mcp.domain.types.json import JsonObject
from snapmaker_slicer_mcp.adapters.three_mf.capabilities import DocumentEditor
from snapmaker_slicer_mcp.application.contracts.presets import EngineIdentity
from snapmaker_slicer_mcp.application.contracts.projects import ProjectSlicer

class Engine:
    def verify(self) -> None:
        pass

    def identity(self) -> JsonObject:
        return {"source_commit": "synthetic"}

class Slicer:
    engine: EngineIdentity = Engine()

    def slice_3mf(self, path: str, plate: int, force: bool = False,
                  allow_newer_file: bool = True) -> JsonObject:
        return {"path": path, "plate": plate}


def use_slicer(slicer: ProjectSlicer) -> None:
    pass

'''

_POSITIVE = '''def valid_consumer(editor: DocumentEditor) -> None:
    ObjectId(2)
    XYZ(1.0, 2.0, 3.0)
    editor.set_config({"layer_height": "0.2"})
    use_slicer(Slicer())
'''

_NEGATIVE = '''class InvalidSlicer:
    engine: EngineIdentity = Engine()

    def slice_3mf(self, path: int, plate: int, force: bool = False,
                  allow_newer_file: bool = True) -> list[str]:
        return [str(path)]


def invalid_consumer(editor: DocumentEditor) -> None:
    ObjectId("x")
    XYZ("x", 2.0, 3.0)
    editor.set_config(["layer_height", "0.2"])
    use_slicer(InvalidSlicer())
'''


@pytest.mark.parametrize('valid', [True, False], ids=['accept-valid', 'reject-invalid'])
def test_public_domain_and_port_types_are_checked(tmp_path: Path, valid: bool) -> None:
    repository = Path(__file__).resolve().parents[1]
    source = _IMPORTS + (_POSITIVE if valid else _NEGATIVE)
    consumer = tmp_path / 'consumer.py'
    consumer.write_text(source, encoding='utf-8')
    environment = dict(os.environ)
    environment['MYPYPATH'] = str(repository / 'src')
    checked = subprocess.run(
        [sys.executable, '-m', 'mypy', '--config-file', str(repository / 'pyproject.toml'),
         '--strict', '--show-error-codes', '--no-error-summary', '--no-pretty',
         '--cache-dir', str(tmp_path / 'mypy-cache'), str(consumer)],
        cwd=repository, env=environment, capture_output=True, text=True, check=False,
    )
    diagnostic = checked.stdout + checked.stderr
    if valid:
        assert checked.returncode == 0, diagnostic
        assert diagnostic == ''
        return

    assert checked.returncode == 1, diagnostic
    errors = re.findall(r'^.*consumer\.py:(\d+): error: (.+) \[([^]]+)\]$',
                        diagnostic, re.MULTILINE)
    expected = [
        ('    ObjectId("x")', 'ObjectId'),
        ('    XYZ("x", 2.0, 3.0)', 'XYZ'),
        ('    editor.set_config(["layer_height", "0.2"])', 'set_config'),
        ('    use_slicer(InvalidSlicer())', 'use_slicer'),
    ]
    assert len(errors) == len(expected), diagnostic
    assert len(re.findall(r'^.*: error:', diagnostic, re.MULTILINE)) == len(expected), diagnostic
    for (line, message, code), (statement, name) in zip(errors, expected, strict=True):
        assert int(line) == source.splitlines().index(statement) + 1, diagnostic
        assert code == 'arg-type', diagnostic
        assert f'to "{name}"' in message, diagnostic
    assert 'ProjectSlicer' in diagnostic
    assert 'InvalidSlicer' in diagnostic
    assert 'Following member(s)' in diagnostic
