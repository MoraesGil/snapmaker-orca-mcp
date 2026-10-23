"""Use cases coordinate injected ports without storage or schema globals."""
from snapmaker_slicer_mcp.domain.settings.normalization import validate_changes
from snapmaker_slicer_mcp.application.services.presets import PresetService


def test_normalization_uses_explicit_schema_without_global_defaults():
    schema = {'synthetic_count': {'type': 'coInt', 'min': 2, 'max': 7}}
    assert validate_changes({'synthetic_count': 3}, {}, schema) == {'synthetic_count': '3'}


def test_catalog_uses_injected_repository_and_engine():
    class Engine:
        def verify(self):
            pass
        def identity(self):
            return {'source_commit': 'fixture'}
    class Repository:
        def records(self):
            return [{'name': 'Synthetic', 'type': 'print', 'source': 'user', 'path': 'opaque',
                     'sha256': 'hash', 'raw': {'name': 'Synthetic', 'layer_height': '0.2'}, 'read_only': True}]
    service = PresetService(Engine(), Repository(), {'_meta': {'source_commit': 'fixture'},
                                                   'layer_height': {'type': 'coFloat'}})
    assert service.get_preset_config('process', 'Synthetic')['config']['layer_height'] == '0.2'


def test_project_service_coordinates_injected_revision_repository():
    from contextlib import nullcontext
    from snapmaker_slicer_mcp.application.services.projects import ProjectService

    class Document:
        config = {'synthetic_count': '2'}
        def execute(self, name, arguments):
            assert name == 'set_config'
            self.config.update(arguments['changes'])
            return None

    class Repository:
        published = 0
        def transaction(self):
            return nullcontext()
        def state(self, identifier):
            assert identifier == 'opaque-project'
            return {'project_id': identifier, 'sha256': 'current'}
        def open(self, state):
            return Document()
        def commit(self, state, document, operations, results, engine):
            self.published += 1
            assert document.config == {'synthetic_count': '4'}
            assert results == [{'operation': 'set_config', 'result': None}]
            return {**state, 'revision': 1}

    class Engine:
        def verify(self):
            pass
    class Slicer:
        engine = Engine()

    repository = Repository()
    service = ProjectService(Slicer(), repository, object(), object(),
                             {'synthetic_count': {'type': 'coInt', 'min': 2}})
    import pytest
    with pytest.raises(ValueError, match='Stale'):
        service.set_config('opaque-project', {'synthetic_count': 4}, 'old')
    assert repository.published == 0
    result = service.set_config('opaque-project', {'synthetic_count': 4}, 'current')
    assert result['revision'] == repository.published == 1
    with pytest.raises(ValueError, match='outside'):
        service.set_config('opaque-project', {'synthetic_count': 1})
    assert repository.published == 1


def test_services_have_no_infrastructure_imports():
    import ast
    from pathlib import Path
    import snapmaker_slicer_mcp.application.services.projects as projects
    import snapmaker_slicer_mcp.application.services.presets as presets
    forbidden = {'pathlib', 'subprocess', 'os', 'shutil', 'tempfile', 'xml', 'adapters',
                 'project', 'engine', 'service', 'native_ops'}
    for module in (projects, presets):
        tree = ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not (set(alias.name.split('.')) & forbidden) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (set((node.module or '').split('.')) & forbidden)


def test_filesystem_catalog_ignores_valid_json_that_is_not_a_preset(tmp_path):
    from snapmaker_slicer_mcp.adapters.storage.presets import FilesystemPresetRepository
    source = tmp_path / 'sources'
    source.mkdir()
    (source / 'descriptor.json').write_text('["descriptor", "data"]')
    repository = FilesystemPresetRepository(tmp_path / 'resources', [source], tmp_path / 'drafts')
    assert repository.records() == []


def test_profile_normalization_uses_explicit_flow_keys_without_mutation():
    from snapmaker_slicer_mcp.domain.settings.normalization import normalize_filament_profile
    schema = {'filament_volume_type': {'enum_values': ['slow', 'fast']},
              '_meta': {'flow_variant_keys': {'filament': ['synthetic_temperature']}}}
    profile = {'filament_flow_support': ['slow', 'fast'], 'synthetic_temperature': ['200']}
    assert normalize_filament_profile(profile, schema)['synthetic_temperature'] == ['200', '200']
    assert profile['synthetic_temperature'] == ['200']


def test_native_reset_mode_preserves_null_operation_result(tmp_path):
    from test_editor import project
    from test_service import boundary
    from snapmaker_slicer_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    editor = Editor(slicer)
    loaded = editor.load_model(str(project(tmp_path / 'reset-source.3mf')))
    changed = editor.edit_project(loaded['project_id'], [
        {'operation': 'set_layer_height', 'object_id': 2, 'mode': 'reset'}])
    assert changed['results'] == [{'operation': 'set_layer_height', 'result': None}]
    assert changed['revision'] == 1
