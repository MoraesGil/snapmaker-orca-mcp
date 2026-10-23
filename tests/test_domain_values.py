"""Domain contracts and format dependency direction."""
import ast
import importlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


def test_domain_values_enforce_existing_geometry_invariants():
    values = importlib.import_module('snapmaker_slicer_mcp.domain.types.values')
    assert values.XYZ(-5, 0, 2).as_list() == [-5.0, 0.0, 2.0]
    assert values.BoxDimensions(1, 2, 3).as_list() == [1.0, 2.0, 3.0]
    for xyz in [(float('nan'), 0, 0), (0, float('inf'), 0), (True, 1, 2)]:
        with pytest.raises(ValueError):
            values.XYZ(*xyz)
    for box in [(0, 1, 1), (-1, 1, 1)]:
        with pytest.raises(ValueError):
            values.BoxDimensions(*box)
    for identifier in [0, -1, True]:
        with pytest.raises(ValueError):
            values.ObjectId(identifier)
    with pytest.raises(FrozenInstanceError):
        values.ObjectId(1).value = 2
    assert str(values.Sha256('a' * 64)) == 'a' * 64
    for digest in ['', 'A' * 64, 'z' * 64]:
        with pytest.raises(ValueError):
            values.Sha256(digest)
    assert values.LineRange(1, 3).last == 3
    for endpoints in [(0, 3), (4, 3), (True, 3)]:
        with pytest.raises(ValueError):
            values.LineRange(*endpoints)


def test_format_facades_are_real_module_aliases():
    project = importlib.import_module('snapmaker_slicer_mcp.project')
    document = importlib.import_module('snapmaker_slicer_mcp.adapters.three_mf.document')
    native = importlib.import_module('snapmaker_slicer_mcp.native_ops')
    layout = importlib.import_module('snapmaker_slicer_mcp.adapters.orca.layout')
    assert project is document
    assert native is layout
    assert project.Project.__module__ == document.__name__


def test_domain_has_no_infrastructure_dependencies():
    package = importlib.import_module('snapmaker_slicer_mcp.domain')
    forbidden = {'pathlib', 'os', 'zipfile', 'xml', 'fastapi', 'starlette', 'mcp'}
    for path in Path(package.__file__).parent.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
            else:
                continue
            assert all(name.split('.')[0] not in forbidden and 'adapters' not in name for name in names), path


def test_document_exposes_typed_reader_editor_capabilities(tmp_path):
    from snapmaker_slicer_mcp.adapters.three_mf.capabilities import DocumentEditor
    from snapmaker_slicer_mcp.project import Project
    import zipfile

    path = tmp_path / 'capability.3mf'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model><resources/><build/></model>")
    document: DocumentEditor = Project(path)
    assert isinstance(document, DocumentEditor)
    assert document.describe()['objects'] == document.list_objects()
    assert document.get_config() == Project(path).get_config()
