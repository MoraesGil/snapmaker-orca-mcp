"""Shared execution only; each named property supplies literal values and an oracle."""
import json
import zipfile
from pathlib import Path

from snapmaker_slicer_mcp.editor import Editor
from snapmaker_slicer_mcp.service import Slicer

from .conftest import record
from .harness import make_project, observe, official_config


def paired_property(engine, temporary, *, key, values, oracle, scope,
                    config_overrides=None):
    """Persist two explicit values, fresh-slice them, then check native semantics.

    ``values`` pairs input and independently written expected saved values.
    The oracle receives command/extrusion observations, never config comments.
    All inputs/artifacts belong to this pytest temporary directory.
    """
    editor = Editor(Slicer(engine, temporary / 'artifacts', timeout=120))
    config = official_config(engine, temporary)
    config.update(config_overrides or {})
    observations, artifacts = [], []
    try:
        assert len(values) == 2, 'A native property comparison requires two values'
        for index, (value, expected) in enumerate(values):
            source = make_project(temporary / f'{key}-{index}.3mf', config)
            original = source.read_bytes()
            loaded = editor.load_model(str(source))
            revision = editor.set_config(loaded['project_id'], {key: value}, loaded['sha256'])
            with zipfile.ZipFile(revision['path']) as archive:
                saved = json.loads(archive.read('Metadata/project_settings.config'))
            assert saved[key] == expected, 'The saved setting differs from the explicit expectation'
            job = editor.slice_project(loaded['project_id'], force=True)
            assert job['source']['sha256'] == revision['sha256']
            output = Path(job['plates'][0]['gcode_path'])
            observation = observe(output.read_text())
            observations.append(observation)
            artifacts.append({'source_3mf': revision['path'], 'gcode': str(output),
                              'persisted': saved[key], 'extrusion_moves': len(observation['moves'])})
            assert source.read_bytes() == original, 'Native regression modified its input'
        oracle(*observations)
    except Exception as error:
        record(engine, key, 'fail', {'error_type': type(error).__name__, 'artifacts': artifacts})
        raise
    record(engine, key, 'pass', {'scope': scope, 'artifacts': artifacts})
