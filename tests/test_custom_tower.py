"""Public synthetic examples for the optional tower plugin."""
import importlib.util
import json
from pathlib import Path

import pytest


def test_custom_tower_plugin_is_available():
    assert importlib.util.find_spec('snapmaker_slicer_mcp.plugins') is not None


def options(**changes):
    from snapmaker_slicer_mcp.plugins.custom_tower.contracts import TowerOptions
    return TowerOptions.model_validate(dict(model_tools=[0], interface_tool=1,
        primary_object_id=2, minimum_width_mm=16, maximum_width_mm=32,
        purge_volume_mm3={0: 1, 1: 1}, **changes))


def sample(tower=False, late=False):
    name = '__SYNTH_TOWER_V1__' + 'a' * 32
    head = 'G90\nM83\nT0\n'
    def block(label, z, x=10):
        return (f'; printing object {label} id:0 copy 0\n;TYPE:Outer wall\n'
                f'G0 X{x} Y10 Z{z}\nG1 X{x+2} Y10 E1 F1200\n'
                ';TYPE:Sparse infill\nG1 X10 Y12 E1\n'
                f'; stop printing object {label} id:0 copy 0\n')
    text = head + ';LAYER_CHANGE\n;Z:0.2\n' + block('Model', .2)
    text += ';LAYER_CHANGE\n;Z:0.4\nT1\n'
    if tower and not late:
        text += block(name, .4, 50)
    text += block('Model', .4)
    if tower and late:
        text += block(name, .4, 50)
    text += ';LAYER_CHANGE\n;Z:0.6\n' + block('Model', .6)
    return text


def test_trace_separates_tool_changes_from_continuing_head():
    from snapmaker_slicer_mcp.plugins.custom_tower.gcode import trace
    result = trace(sample(), diameters=[1.75, 1.75])
    assert [p.changed for p in result.phases] == [False, True, False]
    assert result.layers[1].tools == {1}
    assert result.layers[2].tools == {1}
    assert result.phases[1].volume_mm3 > 4


@pytest.mark.parametrize('extrusion', ['G1 X2 Y1 E.4', 'G2 X1 Y1 I.5 J0 E.4'])
def test_trace_handles_absolute_e_full_circle_and_retraction(extrusion):
    from snapmaker_slicer_mcp.plugins.custom_tower.gcode import trace
    text = ('G90\nM82\nT0\n;LAYER_CHANGE\n;Z:0.2\n'
            '; printing object Model id:0 copy 0\n;TYPE:Outer wall\nG0 X1 Y1 Z.2\n'
            'G92 E0\nG1 E-.2\nG1 E0\nM221 S50\n' + extrusion + '\n')
    result = trace(text, diameters=[1.75])
    assert result.phases[0].volume_mm3 == pytest.approx(.4 * 3.141592653589793 * (1.75/2)**2 * .5)


def test_unknown_unlabelled_preparation_fails_closed():
    from snapmaker_slicer_mcp.plugins.custom_tower.gcode import trace
    with pytest.raises(ValueError, match='unlabelled|unclassified'):
        trace(sample() + 'G1 X99 Y10 E1\n', diameters=[1.75, 1.75])
    with pytest.raises(ValueError,match='unlabelled|Unlabelled|unclassified'):
        trace(sample().replace(';Z:0.2',';Z:0.2\nT1\nG1 X2 Y2 E1\nT0',1),diameters=[1.75]*2)


def test_native_rounded_wipe_repayment_is_not_a_new_priming_pass():
    from snapmaker_slicer_mcp.plugins.custom_tower.gcode import trace
    text=sample().replace('G1 X12 Y10 E1 F1200',
        'G1 X12 Y10 E1 F1200\nG1 E-1.49999\nG1 E1.5',1)
    result=trace(text,diameters=[1.75]*2)
    assert len(result.phases)==3
    with pytest.raises(ValueError,match='E-only'):
        trace(text.replace('G1 E1.5\n','G1 E1.6\n'),diameters=[1.75]*2)


def test_native_xy_coordinate_rounding_has_a_bounded_extrusion_tolerance():
    from snapmaker_slicer_mcp.plugins.custom_tower.gcode import trace
    text=sample().replace('G1 X12 Y10 E1 F1200','G1 X12 Y10 E1 F1200\nG1 X12 Y10 E.00002',1)
    assert len(trace(text,diameters=[1.75]*2).phases)==3
    with pytest.raises(ValueError,match='E-only'):
        trace(text.replace('E.00002','E.001'),diameters=[1.75]*2)


def test_audit_rejects_model_before_tower_and_accepts_complete_pass():
    from snapmaker_slicer_mcp.plugins.custom_tower.gcode import trace, audit_tower
    good = audit_tower(trace(sample(tower=True), diameters=[1.75]*2), targets={1: 1})
    bad = audit_tower(trace(sample(tower=True, late=True), diameters=[1.75]*2), targets={1: 1})
    assert good['passed'] and good['changes_checked'] == 1
    assert not bad['passed'] and 'model_before_tower' in bad['failures']


def test_roles_are_bounded_and_never_hardcode_petg_or_tool_zero():
    from snapmaker_slicer_mcp.plugins.custom_tower.contracts import TowerOptions
    for tools, interface in [([3], 0), ([2, 0], 3), ([1, 2, 3], 0)]:
        value = TowerOptions(model_tools=tools, interface_tool=interface, primary_object_id=2)
        assert value.interface_tool not in value.model_tools
    with pytest.raises(ValueError):
        TowerOptions(model_tools=[0, 1], interface_tool=1, primary_object_id=2)
    with pytest.raises(ValueError):
        options(max_iterations=1000)


def test_mcp_json_material_keys_are_accepted_without_weakening_tool_validation():
    from snapmaker_slicer_mcp.plugins.custom_tower.contracts import TowerOptions
    value=TowerOptions.model_validate({'model_tools':[0],'interface_tool':1,'primary_object_id':2,
        'purge_volume_mm3':{'0':12,'1':6}})
    assert value.purge_volume_mm3=={0:12,1:6}
    with pytest.raises(ValueError):
        TowerOptions.model_validate({'model_tools':[0],'interface_tool':1,'primary_object_id':2,
            'purge_volume_mm3':{'4':12}})


def test_only_explicit_signed_auxiliary_is_removed(tmp_path):
    from test_editor import project
    from snapmaker_slicer_mcp.project import Project, _set_metadata
    from snapmaker_slicer_mcp.plugins.custom_tower.archive import signed_towers, remove_signed, SIGNATURE, geometry_fingerprint
    p = Project(project(tmp_path/'model.3mf'))
    _set_metadata(p._object_settings(2), 'name', 'Real TORRE miniature')
    assert signed_towers(p) == []
    assert remove_signed(p, 1) == []
    name = SIGNATURE + 'a'*32
    _set_metadata(p._object_settings(2), 'name', name)
    assert signed_towers(p) == [], 'a name alone must not authorize removing a model'
    _set_metadata(p._object_settings(2), 'slicer_mcp_auxiliary', json.dumps(
        {'schema': 1, 'producer': 'custom-tower', 'role': 'synthetic_priming_tower', 'token': 'a'*32,
         'geometry_sha256':geometry_fingerprint(p,2)}))
    assert signed_towers(p) == [2]
    assert remove_signed(p, 1) == [2]
    assert p.list_objects() == []


def test_structural_pass_has_no_purge_budget():
    from snapmaker_slicer_mcp.plugins.custom_tower.geometry import pass_density
    assert pass_density(area=100, height=.2, perimeter=20, line_width=.5,
                        target=10, changed=False, structural_density=8) == 8
    assert pass_density(area=100, height=.2, perimeter=20, line_width=.5,
                        target=10, changed=True, structural_density=8) > 8


async def test_plugin_is_opt_in_and_shares_the_editor(tmp_path):
    from test_service import boundary
    from snapmaker_slicer_mcp.server import build_server
    slicer, _ = boundary(tmp_path)
    plain = build_server(slicer)
    names = {t.name for t in await plain.list_tools()}
    assert 'add_custom_tower' not in names
    extended = build_server(slicer, plugins=['custom-tower'])
    assert {'add_custom_tower', 'custom_tower_status'} <= {t.name for t in await extended.list_tools()}


def test_regions_cover_section_with_main_shell_and_one_alternate_center():
    from snapmaker_slicer_mcp.plugins.custom_tower.gcode import trace
    from snapmaker_slicer_mcp.plugins.custom_tower.geometry import make_plan, slab_mesh
    text='G90\nM83\nT3\n'
    for i in range(1,61):
        z=i*.2; span=40-i*.3
        text+=f';LAYER_CHANGE\n;Z:{z:.3f}\n'
        for t in ([3] if i==1 else [3,0]):
            text+=f'T{t}\n; printing object Model id:0 copy 0\n;TYPE:Outer wall\n'
            text+=f'G0 X10 Y10 Z{z:.3f}\nG1 X{10+span} Y10 E1 F1200\n; stop printing object Model id:0 copy 0\n'
    cfg=options().model_copy(update={'model_tools':[3],'interface_tool':0,'purge_volume_mm3':{0:1,3:1}})
    result=trace(text,diameters=[1.75]*4)
    plan=make_plan(result,cfg,'Model')
    assert plan['rows'][0]['width_mm']==32
    assert plan['rows'][-1]['width_mm']==16
    for row in plan['rows']:
        assert row['regions'][0]['tool']==3
        assert row['regions'][0]['outer']==1
        assert row['regions'][-1]['inner']==0
        assert sum(r['outer']**2-r['inner']**2 for r in row['regions'])==pytest.approx(1)
    vertices, faces=slab_mesh(20,.3,1,0,.2)
    edges={}
    for face in faces:
        for a,b in zip(face,face[1:]+face[:1]):
            edge=tuple(sorted((a,b))); edges[edge]=edges.get(edge,0)+1
    assert set(edges.values())=={2}, 'every closed prism edge has two incident faces'


def test_hybrid_advice_is_finite_and_never_receives_project_data():
    from snapmaker_slicer_mcp.plugins.custom_tower.contracts import HybridOptions
    from snapmaker_slicer_mcp.plugins.custom_tower.hybrid import candidate_order
    calls=[]
    def endpoint(url,payload,timeout):
        calls.append(payload)
        if len(calls)==1:
            return {'results':[{'index':i,'relevance_score':i} for i in range(3)]}
        return {'choices':[{'message':{'content':'{"candidate_id":"disable-safety"}'}}]}
    candidates,info=candidate_order(HybridOptions(ranker_url='http://127.0.0.1/rerank',
        llm_url='http://127.0.0.1/chat/completions',llm_model='explicit'),post_json=endpoint)
    assert candidates[0]['candidate_id']=='margin-20'
    assert info['calls']==2 and info['failures']==['llm_unavailable_or_invalid']
    assert not any(v in json.dumps(calls) for v in ('3mf','gcode','/Volumes','project_id'))


def test_stale_tower_blocks_native_slice_until_recalculated(tmp_path):
    from test_service import boundary
    from test_editor import project
    from snapmaker_slicer_mcp.editor import Editor
    from snapmaker_slicer_mcp.service import write_json
    slicer,_=boundary(tmp_path); editor=Editor(slicer)
    loaded=editor.load_model(str(project(tmp_path/'input.3mf')))
    loaded['custom_tower']={'validated_sha256':loaded['sha256'],'plate':1,'run_id':'a'*32}
    write_json(editor.root/loaded['project_id']/'state.json',loaded)
    edited=editor.set_config(loaded['project_id'],{'wall_loops':3},loaded['sha256'])
    with pytest.raises(ValueError,match='tower.*stale|tower.*recalculat'):
        editor.slice_project(loaded['project_id'])


def test_demo_is_on_first_plate_and_never_disabled(tmp_path):
    from test_editor import project
    from snapmaker_slicer_mcp.project import Project
    from snapmaker_slicer_mcp.plugins.custom_tower.archive import normalize_demo
    p=Project(project(tmp_path/'demo.3mf')); p._items(2)[0].set('printable','0')
    normalize_demo(p,[2])
    assert p.list_objects()[0]['instances'][0]['printable']
    assert p.list_plates()[0]['name'].startswith('DEMO')
    with pytest.raises(ValueError,match='plate 1'):
        normalize_demo(p,[999])


def test_tower_insertion_preserves_ordinal_ranges_cut_brim_and_profiles(tmp_path):
    from test_editor import project
    from snapmaker_slicer_mcp.project import Project, RANGES, BRIM, CUT, PROFILE
    from snapmaker_slicer_mcp.plugins.custom_tower.archive import add_to_project
    from xml.etree import ElementTree as ET
    p=Project(project(tmp_path/'source.3mf'))
    p.data[RANGES]=b'<objects><object id="1"><range min_z="1" max_z="2"><option opt_key="wall_loops">4</option></range></object></objects>'
    p.data[CUT]=b'<objects><object id="1"><cut_id id="7"/></object></objects>'
    p.data[BRIM]=b'brim_points_format_version=1\nobject_id=1|1;2;3\n'
    p.data[PROFILE]=b'object_id=1|0;0.2;2;0.2;10;0.2\n'
    plan={'rows':[{'bottom_z':0,'z':.2,'width_mm':32,'regions':[
        {'tool':0,'density':10,'inner':0,'outer':1}]}]}
    add_to_project(p,1,plan,options(),[150,150],{})
    assert ET.fromstring(p.data[RANGES]).find('object').get('id')=='2'
    assert ET.fromstring(p.data[CUT]).find('object').get('id')=='2'
    assert b'object_id=2|' in p.data[BRIM]
    assert p.get_layer_profile(2)==[0,.2,2,.2,10,.2]


def test_imported_signed_tower_without_receipt_requires_recalculation(tmp_path):
    from test_service import boundary
    from test_editor import project
    from snapmaker_slicer_mcp.editor import Editor
    from snapmaker_slicer_mcp.project import Project
    from snapmaker_slicer_mcp.plugins.custom_tower.archive import add_to_project
    p=Project(project(tmp_path/'input.3mf'))
    plan={'rows':[{'bottom_z':0,'z':.2,'width_mm':32,'regions':[{'tool':0,'density':10,'inner':0,'outer':1}]}]}
    add_to_project(p,1,plan,options(),[150,150],{})
    out=tmp_path/'with-tower.3mf'; p.save(out)
    slicer,_=boundary(tmp_path); editor=Editor(slicer); loaded=editor.load_model(str(out))
    with pytest.raises(ValueError,match='tower.*recalculat'):
        editor.slice_project(loaded['project_id'])
    with pytest.raises(ValueError,match='tower.*recalculat'):
        editor.apply_and_slice(loaded['project_id'],{'wall_loops':3},1,loaded['sha256'])


def test_external_fingerprint_ignores_only_signed_auxiliary_but_tracks_model_edits(tmp_path):
    from test_editor import project
    from snapmaker_slicer_mcp.project import Project
    from snapmaker_slicer_mcp.plugins.custom_tower.archive import add_to_project
    from snapmaker_slicer_mcp.plugins.custom_tower.receipts import external_fingerprint, tower_fingerprint
    p=Project(project(tmp_path/'input.3mf')); p.config.update(print_order='as_obj_list',print_sequence='by layer')
    original=external_fingerprint(p)
    plan={'rows':[{'bottom_z':0,'z':.2,'width_mm':32,'regions':[{'tool':0,'density':10,'inner':0,'outer':1}]}]}
    oid,_=add_to_project(p,1,plan,options(),[150,150],{})
    assert external_fingerprint(p)==original
    own=tower_fingerprint(p,oid,1)
    p._items(oid)[0].set('transform','1 0 0 0 1 0 0 0 1 151 150 0')
    assert tower_fingerprint(p,oid,1)!=own
    assert external_fingerprint(p)==original
    p.transform_object(2,translate=[1,0,0])
    assert external_fingerprint(p)!=original


@pytest.mark.parametrize('models,interface,expected',[([0],1,50),([1],0,65)])
def test_only_project_local_interface_bed_follows_the_main_material(tmp_path,models,interface,expected):
    from test_editor import project
    from snapmaker_slicer_mcp.project import Project
    from snapmaker_slicer_mcp.plugins.custom_tower.materials import main_material_bed
    p=Project(project(tmp_path/'materials.3mf')); source=p.path.read_bytes()
    cfg=options().model_copy(update={'model_tools':models,'interface_tool':interface})
    changed=main_material_bed(p,cfg)
    assert changed
    assert [float(v) for v in p.config['cool_plate_temp']]==[expected]*2
    assert p.path.read_bytes()==source


def test_demo_uses_identical_source_parts_at_the_largest_declared_size(tmp_path):
    from test_editor import project
    from snapmaker_slicer_mcp.project import Project
    from snapmaker_slicer_mcp.plugins.custom_tower.demos import validate_demo
    p=Project(project(tmp_path/'assembly.3mf')); p.add_plate('Production')
    clone=p.duplicate_object(2); p.move_object_to_plate(clone,2)
    p.add_modifier(clone,[5.,5.,5.],[2.,2.,2.],{'outer_wall_speed':[20.]})
    checked=validate_demo(p,[2],2)
    assert checked['identical_meshes_and_scale']
    p.transform_object(clone,scale=[.7,.7,.7])
    with pytest.raises(ValueError,match='identical|largest'):
        validate_demo(p,[2],2)


def test_editor_clients_share_the_revision_transaction_lock(tmp_path):
    from test_service import boundary
    from snapmaker_slicer_mcp.editor import Editor
    slicer,_=boundary(tmp_path)
    first,second=Editor(slicer),Editor(slicer)
    assert first._lock is second._lock, 'REST/MCP writers need one shared CAS critical section'


def test_native_unlabelled_brim_is_spatially_assigned_and_not_silently_exempt():
    from snapmaker_slicer_mcp.plugins.custom_tower.gcode import trace, audit_tower
    name='__SYNTH_TOWER_V1__'+'a'*32
    text=('G90\nM83\nT0\n;LAYER_CHANGE\n;Z:0.2\nT1\n;TYPE:Brim\n'
          'G0 X50 Y10 Z.2\nG1 X52 Y10 E.2 F1200\n'
          f'; printing object {name} id:1 copy 0\n;TYPE:Outer wall\nG1 X53 E1\n'
          f';TYPE:Sparse infill\nG1 Y12 E1\n; stop printing object {name} id:1 copy 0\n'
          '; printing object Model id:0 copy 0\nG0 X10 Y10\nG1 X12 E1\n'
          '; stop printing object Model id:0 copy 0\n')
    assigned=trace(text,diameters=[1.75]*2,brim_boxes={name:[49,9,55,15]})
    assert audit_tower(assigned,targets={1:1})['passed']
    unresolved=trace(text,diameters=[1.75]*2)
    assert not audit_tower(unresolved,targets={1:1})['passed']


def test_interface_bed_change_cannot_alter_another_production_plate(tmp_path):
    from test_editor import project
    from snapmaker_slicer_mcp.project import Project
    from snapmaker_slicer_mcp.plugins.custom_tower.materials import main_material_bed
    p=Project(project(tmp_path/'multi.3mf')); p.add_plate('PETG production')
    before=p.get_config()
    with pytest.raises(ValueError,match='project-wide|other production'):
        main_material_bed(p,options(),plate=1)
    assert p.get_config()==before
