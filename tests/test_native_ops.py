"""Verified affine recovery; the subprocess double tests the native boundary only."""
import hashlib
import json
import os
import signal
import sys
import time
import zipfile
from pathlib import Path

import pytest


CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
ARRANGED = [0, 1, 0, -1, 0, 0, 0, 0, 1, 100, -312, 0]
ORIENTED = [1, 0, 0, 0, 0, 1, 0, -1, 0, 0, 0, 5]


def make_project(path):
    # Build order differs from both resource order and sorted object ids.
    mesh = '''<mesh><vertices><vertex x="0" y="0" z="0"/>
      <vertex x="4" y="0" z="0"/><vertex x="0" y="3" z="0"/>
      <vertex x="0" y="0" z="2"/></vertices><triangles>
      <triangle v1="0" v2="2" v3="1" paint_color="3"/>
      <triangle v1="0" v2="1" v3="3"/>
      <triangle v1="0" v2="3" v3="2"/>
      <triangle v1="1" v2="2" v3="3"/></triangles></mesh>'''
    xml = f'''<model xmlns="{CORE}" unit="millimeter"><resources>
      <object id="17" type="model" name="../bad/name">{mesh}</object>
      <object id="42" type="model" name="other">{mesh}</object>
      </resources><build>
      <item objectid="42" transform="1 0 0 0 1 0 0 0 1 30 40 -1"/>
      <item objectid="17" transform="1 0 0 0 1 0 0 0 1 60 70 3"/>
      <item objectid="42" transform="0 2 0 -2 0 0 0 0 2 320 10 2"/>
      </build></model>'''
    metadata = '''<config><object id="17"><metadata key="name" value="../../bad/name"/></object>
      <object id="42"><metadata key="name" value="other"/></object>
      <plate><metadata key="plater_id" value="1"/>
        <model_instance><metadata key="object_id" value="42"/>
          <metadata key="instance_id" value="0"/></model_instance>
        <model_instance><metadata key="object_id" value="17"/>
          <metadata key="instance_id" value="0"/></model_instance></plate>
      <plate><metadata key="plater_id" value="2"/>
        <model_instance><metadata key="object_id" value="42"/>
          <metadata key="instance_id" value="1"/></model_instance></plate></config>'''
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", xml)
        z.writestr("Metadata/model_settings.config", metadata)
        z.writestr("Metadata/project_settings.config", json.dumps({"layer_height": "0.2"}))
    return path


def boundary(tmp_path):
    # Use a real child process and actual binary STL files. This deliberately
    # substitutes the external slicer, not affine recovery or archive parsing.
    from snapmaker_slicer_mcp.engine import Engine

    binary = tmp_path / "engine" / ("Contents/MacOS/native-boundary" if sys.platform == "darwin"
                                   else "bin/native-boundary")
    binary.parent.mkdir(parents=True)
    binary.write_text(f'''#!{sys.executable}
import argparse, json, math, os, pathlib, struct, subprocess, sys, time, zipfile
from xml.parsers import expat
from xml.etree import ElementTree as E
p=argparse.ArgumentParser()
p.add_argument('--arrange', type=int, required=True)
p.add_argument('--orient', type=int, required=True)
p.add_argument('--ensure-on-bed', required=True)
p.add_argument('--export-stls', required=True)
p.add_argument('--outputdir', required=True)
p.add_argument('--datadir', required=True)
p.add_argument('--allow-newer-file', nargs='?', const='1')
p.add_argument('--slice', type=int, default=0)
p.add_argument('--debug', type=int, default=0)
p.add_argument('project')
a=p.parse_args()
assert a.ensure_on_bed == '0'
if a.arrange and a.slice == 2: sys.exit(139)  # Native select_plate touches wxGetApp without a GUI.
assert 'DISPLAY' not in os.environ and 'WAYLAND_DISPLAY' not in os.environ
assert 'SNAPMAKER_ORCA_ALLOW_NEWER_FILE' not in os.environ
mode=pathlib.Path(__file__).with_suffix('.mode')
mode=mode.read_text() if mode.exists() else ''
if mode == 'sleep': time.sleep(10)
if mode == 'fail': sys.exit(7)
if mode == 'drift': pathlib.Path(__file__).write_text('# changed engine')
if mode == 'drift_sleep':
    pathlib.Path(__file__).write_text('# changed engine')
    time.sleep(10)
if mode == 'descendant':
    child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])
    pathlib.Path(__file__).with_suffix('.child').write_text(str(child.pid))
    time.sleep(10)
ns={{'c': {CORE!r}}}
with zipfile.ZipFile(a.project) as z:
    data=z.read('3D/3dmodel.model')
    # The official importer uses Expat without namespace expansion and matches
    # core tag names literally. An ns0: prefix is valid XML but incompatible.
    tags=[]
    parser=expat.ParserCreate()
    parser.StartElementHandler=lambda name, attrs: tags.append(name)
    parser.Parse(data,True)
    assert 'model' in tags and 'build' in tags and 'item' in tags
    root=E.fromstring(data)
    objects={{int(o.get('id')): o for o in root.findall('c:resources/c:object',ns)}}
    names={{int(o.get('id')):o.get('name','') for o in objects.values()}}
    occupied=[]
    locked=[]
    locked_keys=set()
    plate_count=1
    if 'Metadata/model_settings.config' in z.namelist():
        settings=E.fromstring(z.read('Metadata/model_settings.config'))
        plate_count=len(settings.findall('plate'))
        for plate in settings.findall('plate'):
            metadata={{m.get('key'):m.get('value') for m in plate.findall('metadata')}}
            if metadata.get('locked')=='true':
                locked.append(int(metadata['plater_id']))
                for instance in plate.findall('model_instance'):
                    ids={{m.get('key'):int(m.get('value')) for m in instance.findall('metadata') if m.get('key') in ('object_id','instance_id')}}
                    locked_keys.add((ids['object_id'],ids['instance_id']))
            if plate.findall('model_instance'):
                occupied += [int(m.get('value')) for m in plate.findall('metadata') if m.get('key')=='plater_id']
        for o in settings.findall('object'):
            for m in o.findall('metadata'):
                if m.get('key')=='name': names[int(o.get('id'))]=m.get('value')
    if a.arrange and mode!='frame_missing':
        print(f'm_plate_width 270, m_plate_depth 270, m_plate_height 270, plate count {{plate_count}}')
        print('before arrange, need_arrange=1')
        final_count=max(occupied) if len(occupied)==1 else 3
        print(f'rebuild_plates_after_arrangement:after rebuild, plates count {{final_count+(1 if mode=="frame_spill" else 0)}}')
    grouped={{}}
    for item in root.findall('c:build/c:item',ns):
        grouped.setdefault(int(item.get('objectid')),[]).append(item)
    out=pathlib.Path(a.export_stls)
    out.mkdir(parents=True,exist_ok=True)
    groups=list(grouped.items())
    if a.arrange and mode=='object_order': groups.reverse()
    for ordinal,(oid,items) in enumerate(groups,1):
        obj=objects[oid]
        verts=[tuple(float(v.get(k)) for k in ('x','y','z')) for v in obj.findall('c:mesh/c:vertices/c:vertex',ns)]
        facets=[tuple(int(t.get(k)) for k in ('v1','v2','v3')) for t in obj.findall('c:mesh/c:triangles/c:triangle',ns)]
        points=[]
        for instance_ordinal,item in enumerate(items):
            is_locked=(oid,instance_ordinal) in locked_keys
            m=[float(n) for n in item.get('transform','1 0 0 0 1 0 0 0 1 0 0 0').split()]
            for face in facets:
                for i in face:
                    v=verts[i]
                    x,y,z=(sum(v[j]*m[3*j+k] for j in range(3))+m[9+k] for k in range(3))
                    if a.orient: x,y,z=x,-z,y+5
                    if a.arrange and not is_locked and len(occupied)==1:
                        selected=occupied[0]-1
                        old_cols=math.ceil(math.sqrt(plate_count))
                        new_cols=math.ceil(math.sqrt(selected+1))
                        dx=(selected%new_cols-selected%old_cols)*324
                        dy=-(selected//new_cols-selected//old_cols)*324
                        x,y=x+90.5+dx,y+81+dy
                    elif a.arrange and not is_locked: x,y,z=-y+100,x-312,z
                    if a.arrange and occupied==[2] and 1 not in locked: x-=324
                    if a.arrange and mode=='plate_outside':x+=270
                    if mode == 'scale' and a.arrange: x*=2
                    points.append((x,y,z))
        if a.arrange and mode == 'reorder': points[3],points[4]=points[4],points[3]
        if mode == 'baseline_shift': points=[(x+2,y,z) for x,y,z in points]
        target=out/f'obj_{{ordinal}}_{{names[oid]}}.stl'
        with target.open('wb') as f:
            f.write(b'native boundary'.ljust(80,b' ')+struct.pack('<I',len(points)//3))
            for i in range(0,len(points),3): f.write(struct.pack('<12fH',0,0,0,*points[i],*points[i+1],*points[i+2],0))
''')
    binary.chmod(0o755)
    resources = tmp_path / "engine" / ("Contents/Resources" if sys.platform == "darwin" else "resources")
    resources.mkdir()
    (resources / "profile.json").write_text("{}")
    return Engine(Engine.bind(binary, "1" * 40, resources)), binary


def test_native_deltas_recovers_world_rotation_per_original_instance_and_preserves_source(tmp_path, monkeypatch):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine, _ = boundary(tmp_path)
    project = make_project(tmp_path / "input.3mf")
    before = project.read_bytes()
    monkeypatch.setenv("DISPLAY", ":42")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    monkeypatch.setenv("SNAPMAKER_ORCA_ALLOW_NEWER_FILE", "1")
    deltas = native_deltas(engine, project, tmp_path / "run", arrange=True)
    assert list(deltas) == [(42, 0), (42, 1), (17, 0)]
    for matrix in deltas.values():
        assert matrix == pytest.approx(ARRANGED, abs=1e-6)
    assert project.read_bytes() == before
    audit = json.loads((tmp_path / "run" / "native_ops.json").read_text())
    assert audit["status"] == "verified"
    assert audit["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert audit["engine"]["source_commit"] == "1" * 40
    assert len(audit["runs"]) == 2
    assert all(Path(r["log_path"]).is_file() for r in audit["runs"])
    assert all(r['log_sha256'] == hashlib.sha256(Path(r['log_path']).read_bytes()).hexdigest()
               for r in audit['runs'])
    assert audit["artifacts"]
    assert audit['layout']=={'plate_count':3,'instances':[
        {'object_id':42,'instance_id':0,'plate':3},
        {'object_id':42,'instance_id':1,'plate':1},
        {'object_id':17,'instance_id':0,'plate':3}]}


def test_selected_plate_orients_only_selected_instances_and_keeps_original_ordinals(tmp_path):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine, _ = boundary(tmp_path)
    project = make_project(tmp_path / "input.3mf")
    deltas = native_deltas(engine, project, tmp_path / "run", orient=True, plate=2)
    assert list(deltas) == [(42, 1)]
    assert deltas[(42, 1)] == pytest.approx(ORIENTED, abs=1e-6)


def test_native_object_export_order_uses_certified_root_identity(tmp_path):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine,binary=boundary(tmp_path)
    binary.with_suffix('.mode').write_text('object_order')
    deltas=native_deltas(engine,make_project(tmp_path/'input.3mf'),tmp_path/'run',arrange=True)
    for matrix in deltas.values():
        assert matrix==pytest.approx(ARRANGED,abs=1e-6)


def test_selected_plate_arrangement_keeps_its_native_plate_frame(tmp_path):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine, _ = boundary(tmp_path)
    deltas = native_deltas(engine, make_project(tmp_path / 'input.3mf'), tmp_path / 'run', arrange=True, plate=2)
    assert list(deltas) == [(42, 1)]
    assert deltas[(42, 1)] == pytest.approx(IDENTITY[:9]+[90.5,81,0], abs=1e-6)


def test_selected_plate_arrange_compensates_native_empty_plate_grid_reflow(tmp_path):
    from xml.etree import ElementTree as ET
    from snapmaker_slicer_mcp.native_ops import native_deltas

    original = make_project(tmp_path / 'original.3mf')
    project = tmp_path / 'five-plates.3mf'
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(project, 'w') as destination:
        for name in source.namelist():
            data = source.read(name)
            if name == '3D/3dmodel.model':
                model=ET.fromstring(data)
                model.find(f'{{{CORE}}}build')[2].set('transform','0 2 0 -2 0 0 0 0 2 10 -314 2')
                # Fixture serialization is independent of native preparation.
                ET.register_namespace('',CORE)
                data=ET.tostring(model)
            if name == 'Metadata/model_settings.config':
                settings=ET.fromstring(data)
                for field in settings.findall('plate')[1].findall('metadata'):
                    if field.get('key')=='plater_id':field.set('value','4')
                for number in (2,3,5):
                    p=ET.SubElement(settings,'plate')
                    ET.SubElement(p,'metadata',{'key':'plater_id','value':str(number)})
                settings[:]=sorted(settings,key=lambda p:int(next((m.get('value') for m in p.findall('metadata') if m.get('key')=='plater_id'),'0')))
                data=ET.tostring(settings)
            destination.writestr(name,data)
    engine,_=boundary(tmp_path)
    deltas=native_deltas(engine,project,tmp_path/'run',arrange=True,plate=4)
    assert deltas[(42,1)]==pytest.approx(IDENTITY[:9]+[90.5,81,0],abs=1e-6)


@pytest.mark.parametrize('mode',['frame_missing','frame_spill','plate_outside'])
def test_selected_arrange_fails_if_native_frame_or_fit_cannot_be_certified(tmp_path,mode):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine,binary=boundary(tmp_path)
    binary.with_suffix('.mode').write_text(mode)
    with pytest.raises(ValueError,match='plate|layout'):
        native_deltas(engine,make_project(tmp_path/'input.3mf'),tmp_path/'run',arrange=True,plate=2)


def test_native_input_namespaces_are_independent_of_other_xml_serialization(tmp_path):
    from xml.etree import ElementTree as ET
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine, _ = boundary(tmp_path)
    project = make_project(tmp_path / 'input.3mf')
    native_deltas(engine, project, tmp_path / 'first', arrange=True)
    ET.register_namespace('', 'http://schemas.openxmlformats.org/package/2006/relationships')
    namespaces = dict(ET._namespace_map)
    deltas = native_deltas(engine, project, tmp_path / 'second', orient=True)
    assert deltas[(42, 1)] == pytest.approx(ORIENTED, abs=1e-6)
    assert ET._namespace_map == namespaces


def locked_project(tmp_path, all_plates=False):
    from xml.etree import ElementTree as ET
    original = make_project(tmp_path / 'original.3mf')
    project = tmp_path / 'locked.3mf'
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(project, 'w') as destination:
        for name in source.namelist():
            data = source.read(name)
            if name == '3D/3dmodel.model':
                model=ET.fromstring(data)
                item=model.find(f'{{{CORE}}}build')[2]
                value=item.get('transform').split();value[-3]='340';item.set('transform',' '.join(value))
                ET.register_namespace('',CORE)
                data=ET.tostring(model)
            if name == 'Metadata/model_settings.config':
                settings = ET.fromstring(data)
                plates=settings.findall('plate')
                for plate in plates if all_plates else plates[1:]:
                    ET.SubElement(plate, 'metadata', {'key':'locked','value':'true'})
                data = ET.tostring(settings)
            destination.writestr(name, data)
    return project


def test_native_selected_plate_preserves_user_lock(tmp_path):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine,_=boundary(tmp_path)
    project=locked_project(tmp_path)
    before = project.read_bytes()
    with pytest.raises(ValueError, match='locked'):
        native_deltas(engine, project, tmp_path / 'run', arrange=True, plate=2)
    assert project.read_bytes() == before


def test_global_orient_preserves_locked_instances(tmp_path):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine,_=boundary(tmp_path)
    project=locked_project(tmp_path)
    before=project.read_bytes()
    deltas=native_deltas(engine,project,tmp_path/'run',orient=True)
    assert set(deltas)=={(42,0),(17,0)}
    assert all(matrix==pytest.approx(ORIENTED,abs=1e-6) for matrix in deltas.values())
    assert project.read_bytes()==before


def test_global_orient_rejects_all_locked_plates(tmp_path):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine,_=boundary(tmp_path)
    with pytest.raises(ValueError,match='locked'):
        native_deltas(engine,locked_project(tmp_path,all_plates=True),tmp_path/'run',orient=True)


def test_global_arrange_verifies_locked_anchors_but_returns_only_unlocked_layout(tmp_path):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine,_=boundary(tmp_path)
    deltas=native_deltas(engine,locked_project(tmp_path),tmp_path/'run',arrange=True)
    assert set(deltas)=={(42,0),(17,0)}
    audit=json.loads((tmp_path/'run/native_ops.json').read_text())
    assert {(i['object_id'],i['instance_id']) for i in audit['layout']['instances']}==set(deltas)
    assert audit['layout']['plate_count']==3
    assert audit['locked_instances_verified'][0]['object_id']==42
    assert audit['locked_instances_verified'][0]['instance_id']==1


@pytest.mark.parametrize("mode", ["reorder", "scale", "baseline_shift"])
def test_rejects_changed_correspondence_scale_and_changed_baseline_pose(tmp_path, mode):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine, binary = boundary(tmp_path)
    binary.with_suffix(".mode").write_text(mode)
    with pytest.raises(ValueError):
        native_deltas(engine, make_project(tmp_path / "input.3mf"), tmp_path / "run", arrange=True)
    assert json.loads((tmp_path / "run" / "native_ops.json").read_text())["status"] == "failed"


def test_failures_and_engine_drift_leave_diagnostics(tmp_path):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine, binary = boundary(tmp_path)
    project = make_project(tmp_path / "input.3mf")
    binary.with_suffix(".mode").write_text("fail")
    with pytest.raises(RuntimeError):
        native_deltas(engine, project, tmp_path / "fail", arrange=True)
    assert (tmp_path / "fail" / "baseline" / "engine.log").is_file()
    binary.with_suffix(".mode").write_text("drift")
    with pytest.raises(ValueError, match="Engine.*changed"):
        native_deltas(engine, project, tmp_path / "drift", arrange=True)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_timeout_terminates_native_process_group(tmp_path):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine, binary = boundary(tmp_path)
    binary.with_suffix(".mode").write_text("descendant")
    with pytest.raises(TimeoutError):
        native_deltas(engine, make_project(tmp_path / "input.3mf"), tmp_path / "run", arrange=True, timeout=1)
    pid = int(binary.with_suffix(".child").read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        proc_stat = Path(f'/proc/{pid}/stat')
        if proc_stat.is_file() and proc_stat.read_text().split(')')[-1].split()[0] == 'Z':
            break  # A killed orphan may await reaping by the container's PID 1.
        time.sleep(0.02)
    else:
        os.kill(pid, signal.SIGKILL)
        pytest.fail("Native subprocess descendant survived the timeout")


def test_engine_is_reverified_after_timeout(tmp_path):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine, binary = boundary(tmp_path)
    binary.with_suffix('.mode').write_text('drift_sleep')
    with pytest.raises(ValueError, match='Engine.*changed'):
        native_deltas(engine, make_project(tmp_path / 'input.3mf'), tmp_path / 'run', arrange=True, timeout=1)


def test_concurrently_changed_source_is_rejected_before_native_snapshot(tmp_path, monkeypatch):
    import snapmaker_slicer_mcp.native_ops as module

    engine, _ = boundary(tmp_path)
    project = make_project(tmp_path / 'input.3mf')
    read_geometry = module._Geometry

    def concurrent_writer(path):
        parsed = read_geometry(path)
        with path.open('ab') as stream:
            stream.write(b'concurrently changed ZIP comment')
        return parsed

    monkeypatch.setattr(module, '_Geometry', concurrent_writer)
    with pytest.raises(ValueError, match='Source.*changed'):
        module.native_deltas(engine, project, tmp_path / 'run', arrange=True)


@pytest.mark.parametrize("options", [{}, {"arrange": 1}, {"arrange": True, "plate": -1},
                                      {"arrange": True, "plate": True}, {"orient": True, "timeout": 0}])
def test_rejects_invalid_operation_options_before_starting_native(tmp_path, options):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine, _ = boundary(tmp_path)
    with pytest.raises(ValueError):
        native_deltas(engine, make_project(tmp_path / "input.3mf"), tmp_path / "run", **options)


def test_missing_or_empty_plate_is_rejected(tmp_path):
    from snapmaker_slicer_mcp.native_ops import native_deltas

    engine, _ = boundary(tmp_path)
    with pytest.raises(ValueError, match="plate"):
        native_deltas(engine, make_project(tmp_path / "input.3mf"), tmp_path / "run", arrange=True, plate=3)


def adaptive_project(path, *, sloped=False, ranges=False, modifier=False):
    vertices = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
             (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
             (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    if sloped:
        vertices = [(0, 0, 0), (4, 0, 0), (0, 3, 0), (0, 0, 2)]
        faces = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
    mesh = '<mesh><vertices>' + ''.join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices)
    mesh += '</vertices><triangles>' + ''.join(f'<triangle v1="{a}" v2="{b}" v3="{c}" paint_color="3"/>'
                                               for a, b, c in faces) + '</triangles></mesh>'
    if modifier:
        resources = f'''<object id="11"><components><component objectid="1"/>
          <component objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 99"/></components></object>
          <object id="1">{mesh}</object><object id="2">{mesh}</object>'''
        settings = '''<config><object id="11"><part id="1" subtype="normal_part"/>
          <part id="2" subtype="parameter_modifier"/></object></config>'''
    else:
        resources, settings = f'<object id="11">{mesh}</object>', '<config/>'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('3D/3dmodel.model', f'<model xmlns="{CORE}"><resources>{resources}</resources>'
                         '<build><item objectid="11"/></build></model>')
        archive.writestr('Metadata/model_settings.config', settings)
        if ranges:
            archive.writestr('Metadata/layer_config_ranges.xml', '''<objects><object id="1">
              <range min_z="0.2" max_z="0.4"><option opt_key="layer_height">0.1</option></range>
              </object></objects>''')
    return path


ADAPTIVE_CONFIG = {'layer_height': '0.2', 'initial_layer_print_height': '0.2',
                   'nozzle_diameter': ['0.4'], 'min_layer_height': ['0.08'], 'max_layer_height': ['0.3']}


def test_adaptive_profile_matches_official_vertical_wall_steps_and_preserves_painting(tmp_path):
    from snapmaker_slicer_mcp.native_ops import adaptive_layer_profile

    project = adaptive_project(tmp_path / 'cube.3mf')
    before = project.read_bytes()
    profile = adaptive_layer_profile(project, 11, ADAPTIVE_CONFIG, 0.5)
    # Native first-layer duplication, 0.04 mm slope limit and horizontal top
    # look-ahead. Expectations are independently hand-calculated.
    assert profile == pytest.approx([0, .2, .2, .2, .2, .24, .44, .28, .72, .28, 1, .28], abs=1e-7)
    assert project.read_bytes() == before


def test_adaptive_quality_uses_sloped_geometry_and_preserves_explicit_height_ranges(tmp_path):
    from snapmaker_slicer_mcp.native_ops import adaptive_layer_profile

    project = adaptive_project(tmp_path / 'slope.3mf', sloped=True)
    fine = adaptive_layer_profile(project, 11, ADAPTIVE_CONFIG, 0)
    coarse = adaptive_layer_profile(project, 11, ADAPTIVE_CONFIG, 1)
    assert len(fine) > len(coarse)
    assert min(fine[1::2]) >= .08 - 1e-7
    assert max(coarse[1::2]) <= .3 + 1e-7
    assert fine[-2] == coarse[-2] == 2
    ranged = adaptive_layer_profile(adaptive_project(tmp_path / 'ranged.3mf', ranges=True), 11,
                                    ADAPTIVE_CONFIG, 0.5)
    assert ranged[4:8] == pytest.approx([.2, .1, .3, .1], abs=1e-7)


def test_adaptive_profile_excludes_modifier_geometry_and_handles_raft_first_layer(tmp_path):
    from snapmaker_slicer_mcp.native_ops import adaptive_layer_profile

    project = adaptive_project(tmp_path / 'modifier.3mf', modifier=True)
    profile = adaptive_layer_profile(project, 11, ADAPTIVE_CONFIG, .5)
    assert profile[-2] == 1
    raft = adaptive_layer_profile(project, 11, {**ADAPTIVE_CONFIG, 'raft_layers': '2'}, .5)
    assert raft[:4] == pytest.approx([0, .2, .2, .24], abs=1e-7)


@pytest.mark.parametrize('quality', [-.1, 1.1, float('nan'), True])
def test_adaptive_profile_rejects_invalid_quality(tmp_path, quality):
    from snapmaker_slicer_mcp.native_ops import adaptive_layer_profile

    with pytest.raises(ValueError, match='quality'):
        adaptive_layer_profile(adaptive_project(tmp_path / 'cube.3mf'), 11, ADAPTIVE_CONFIG, quality)


def test_adaptive_profile_rejects_unknown_object_and_unsupported_nonuniform_limits(tmp_path):
    from snapmaker_slicer_mcp.native_ops import adaptive_layer_profile

    project = adaptive_project(tmp_path / 'cube.3mf')
    with pytest.raises(ValueError, match='object'):
        adaptive_layer_profile(project, 99, ADAPTIVE_CONFIG, .5)
    with pytest.raises(ValueError, match='uniform'):
        adaptive_layer_profile(project, 11, {**ADAPTIVE_CONFIG, 'nozzle_diameter': ['.4', '.8']}, .5)
    with pytest.raises(ValueError, match='shrinkage'):
        adaptive_layer_profile(project, 11, {**ADAPTIVE_CONFIG, 'filament_shrinkage_compensation_z': ['99%']}, .5)
