import asyncio
import copy
import zipfile
from xml.etree import ElementTree as ET

import pytest
from test_editor import project
from test_service import boundary
from snapmaker_slicer_mcp.editor import Editor
from snapmaker_slicer_mcp.project import Project, CORE, SETTINGS, _tree_state


def loaded(tmp_path):
    slicer, _ = boundary(tmp_path)
    editor = Editor(slicer)
    source = project(tmp_path / 'input.3mf')
    return editor, editor.load_model(str(source)), source


def operation(**changes):
    return dict(operation='add_modifier', object_id=2, center=[3, 4, 5], dimensions=[2, 4, 6],
                changes={'outer_wall_speed': [20]}, **changes)


def test_direct_mesh_promotion_preserves_mesh_and_metadata(tmp_path):
    editor, state, source = loaded(tmp_path)
    before = Project(source)
    mesh = _tree_state(before._object(2).find('{*}mesh'))
    metadata = before._object_settings(2)
    ET.SubElement(metadata, 'part', {'id': '2', 'subtype': 'normal_part'}).append(
        ET.Element('metadata', {'key': 'name', 'value': 'painted original'}))
    source = tmp_path / "with-metadata.3mf"
    before.save(source)
    original = source.read_bytes()
    state = editor.load_model(str(source))
    result = editor.edit_project(state['project_id'], [operation()], state['sha256'])
    final = Project(result['path'])
    root = final._object(2)
    components = root.find('{*}components')
    assert root.find('{*}mesh') is None
    assert len(components) == 2
    old_id, new_id = [int(c.get('objectid')) for c in components]
    objects = {int(o.get('id')): o for o in final._resources()}
    assert _tree_state(objects[old_id].find('{*}mesh')) == mesh
    assert components[1].get('transform').split()[-3:] == ['3', '4', '5']
    verts = objects[new_id].findall('.//{*}vertex')
    assert len(verts) == 8
    assert {float(v.get('x')) for v in verts} == {-1, 1}
    assert {float(v.get('z')) for v in verts} == {-3, 3}
    faces = objects[new_id].findall('.//{*}triangle')
    edges = {}
    for f in faces:
        ids = [int(f.get(k)) for k in ('v1', 'v2', 'v3')]
        for a,b in zip(ids, ids[1:] + ids[:1]):
            edge = tuple(sorted((a,b)))
            edges[edge] = edges.get(edge, 0) + 1
    assert len(faces) == 12 and set(edges.values()) == {2}
    parts = final.list_objects()[0]['parts']
    assert parts[0]['part_id'] == old_id and parts[0]['name'] == 'painted original'
    assert parts[1]['subtype'] == 'modifier_part'
    assert parts[1]['config']['outer_wall_speed'] == '20'
    assert final._items(2)[0].attrib == before._items(2)[0].attrib
    with zipfile.ZipFile(result['path']) as z:
        assert z.read('Auxiliaries/keep.bin') == b'preserve this payload'
        assert 'Metadata/plate_1.gcode' not in z.namelist()
    assert source.read_bytes() == original


@pytest.mark.parametrize('field,value', [('center',[True,0,0]),('center',[float('nan'),0,0]),
    ('center',[0,0]),('dimensions',[1,0,1]),('dimensions',[1,-1,1]),('dimensions',[1,float('inf'),1]),
    ('changes',{'layer_height':0.1}),('changes',{'nozzle_temperature':[220]}),('changes',{}),('changes',{'outer_wall_speed':[]}),('changes',{'outer_wall_speed':[True]}),
    ('object_id',True),('object_id',999),('name',None),('units','inch')])
def test_invalid_modifier_is_atomic(tmp_path, field, value):
    editor,state,_ = loaded(tmp_path)
    op = operation(); op[field] = value
    with pytest.raises((ValueError,TypeError)):
        editor.edit_project(state['project_id'], [op])
    assert editor.get_status(state['project_id'])['sha256'] == state['sha256']


def test_stale_and_later_failure_do_not_publish(tmp_path):
    editor,state,_ = loaded(tmp_path)
    with pytest.raises(ValueError, match='Stale'):
        editor.edit_project(state['project_id'], [operation()], 'old')
    with pytest.raises(ValueError):
        editor.edit_project(state['project_id'], [operation(), {'operation':'delete_object','object_id':999}])
    assert editor.get_status(state['project_id'])['sha256'] == state['sha256']


def test_unsupported_units_before_mutation(tmp_path):
    _,_,source = loaded(tmp_path)
    p = Project(source); p.model.set('unit','inch')
    before = _tree_state(p.model)
    with pytest.raises(ValueError, match='millimeter'):
        p.add_modifier(2,[0,0,0],[1,1,1],{'outer_wall_speed':[20]})
    assert _tree_state(p.model) == before


def test_mcp_discovery_and_call(tmp_path):
    from snapmaker_slicer_mcp.server import build_server
    slicer,_ = boundary(tmp_path)
    server = build_server(slicer)
    async def check():
        assert 'add_modifier' in {t.name for t in await server.list_tools()}
        result = await server.call_tool('load_model', {'path':str(project(tmp_path/'input.3mf'))})
        import json
        state = json.loads(result[0].text)
        result = await server.call_tool('add_modifier', dict(project_id=state['project_id'], object_id=2,
            center=[0,0,0], dimensions=[1,1,1], changes={'outer_wall_speed':[20]}))
        assert 'modifier_part' in str(result)
        with pytest.raises(Exception, match='validation|number|integer'):
            await server.call_tool('add_modifier', dict(project_id=state['project_id'], object_id=True,
                center=[True,0,0], dimensions=[1,1,1], changes={'outer_wall_speed':[20]}))
    asyncio.run(check())


def test_external_assembly_and_instance_transforms_preserved(tmp_path):
    editor,state,source = loaded(tmp_path)
    with zipfile.ZipFile(source) as z:
        data = {n:z.read(n) for n in z.namelist()}
    p = Project(source)
    root = p._object(2)
    mesh = root.find('{*}mesh'); root.remove(mesh)
    production = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
    components = ET.SubElement(root, f"{{{CORE}}}components")
    ET.SubElement(components, f"{{{CORE}}}component", {'objectid':'99', f'{{{production}}}path':'/3D/Objects/painted.model',
        'transform':'1 0 0 0 1 0 0 0 1 7 8 9'})
    p.data['3D/Objects/painted.model'] = b'<model>opaque painting payload</model>'
    ET.SubElement(p._object_settings(2), 'part', {'id':'99','subtype':'normal_part'})
    item = copy.deepcopy(p._items(2)[0]); item.set('transform','0 2 0 -2 0 0 0 0 2 100 200 3')
    p.model.find('{*}build').append(item)
    source = tmp_path/'assembly.3mf'; p.save(source)
    state = editor.load_model(str(source)); original = source.read_bytes()
    before = Project(source)
    edited = editor.edit_project(state['project_id'], [operation()])
    after = Project(edited['path'])
    assert after.data['3D/Objects/painted.model'] == before.data['3D/Objects/painted.model']
    assert [_tree_state(e) for e in after._items(2)] == [_tree_state(e) for e in before._items(2)]
    assert _tree_state(after._object(2).find('{*}components')[0]) == _tree_state(components[0])
    assert after.list_plates() == before.list_plates()
    assert len(after._object(2).find('{*}components')) == 2
    assert source.read_bytes() == original


@pytest.mark.parametrize('key', ['outer_wall_speed','inner_wall_speed','sparse_infill_speed',
                                 'top_surface_speed','internal_solid_infill_speed'])
def test_allowed_native_speed_vectors(tmp_path, key):
    _,_,source = loaded(tmp_path)
    p = Project(source)
    p.add_modifier(2,[0,0,0],[1,1,1],{key:[20,30]})
    assert p.list_objects()[0]['parts'][-1]['config'][key] == '20,30'


def test_editor_direct_method(tmp_path):
    editor,state,_ = loaded(tmp_path)
    edited = editor.add_modifier(state['project_id'], 2, [0,0,0], [1,1,1],
                                {'outer_wall_speed':[20]}, expected_sha256=state['sha256'])
    assert edited['revision'] == state['revision'] + 1
