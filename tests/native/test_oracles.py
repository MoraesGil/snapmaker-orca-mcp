from .harness import box_mesh, observe

def test_closed_box_has_each_edge_twice():
    vertices, faces=box_mesh((0,0,0),(2,3,4))
    from collections import Counter
    edges=Counter(tuple(sorted((f[i],f[(i+1)%3]))) for f in faces for i in range(3))
    assert len(vertices)==8 and len(faces)==12 and set(edges.values())=={2}

def test_observer_requires_extrusion_and_tracks_modal_feed():
    result=observe('; FEATURE: Outer wall\nM83\nG1 Z1\nG1 X2 F1200\nG1 X4 E0.2\nG1 X5 E-0.1\n; outer_wall_speed = 999\n')
    assert len(result['moves'])==1
    assert result['moves'][0]==dict(feature='Outer wall',x=4.,y=0.,z=1.,f=1200.,length=2.,e=0.2)

def test_observer_reads_native_type_and_leading_decimal_extrusion():
    result=observe(';TYPE:Outer wall\nM83\nG1 Z.85 F1200\nG1 X.5 E.03\n')
    assert result['moves'][0]['feature']=='Outer wall'
    assert result['moves'][0]['e']==.03
    assert result['moves'][0]['z']==.85

def test_speed_oracle_rejects_comments_without_extrusion():
    import pytest
    from .harness import assert_effect
    with pytest.raises(AssertionError):
        assert_effect('outer_wall_speed',observe('; outer_wall_speed = 20'),observe('; outer_wall_speed = 60'))

def test_off_fan_oracle_rejects_missing_and_nonzero_commands():
    import pytest
    from .harness import assert_effect
    off=observe('M106 P2 S0')
    assert_effect('additional_cooling_fan_speed',off,off)
    for bad in (observe('; M106 P2 S0'),observe('M106 P2 S1')):
        with pytest.raises(AssertionError): assert_effect('additional_cooling_fan_speed',off,bad)

def test_required_runner_errors_and_writes_failure_for_missing_engine(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    runner=Path(__file__).resolve().parents[2]/'scripts/run_native_regressions.py'
    report=tmp_path/'absent.json'
    result=subprocess.run([sys.executable,str(runner),'--app',str(tmp_path/'absent.app'),'--source-commit','9bf2f18eaff7cc0655cbdeef6a215660085ecb0f','--report',str(report)],capture_output=True,text=True)
    assert result.returncode!=0
    assert json.loads(report.read_text())['status']=='fail'

def test_required_runner_rejects_old_pass_when_selection_omits_native_fixture(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    from .harness import CASES,COMMIT
    runner=Path(__file__).resolve().parents[2]/'scripts/run_native_regressions.py'
    app=tmp_path/'Public synthetic placeholder.app'
    binary=app/'Contents/MacOS/Snapmaker_Orca'
    binary.parent.mkdir(parents=True)
    binary.write_text('not executed: this test selects only a pure geometry oracle')
    report=tmp_path/'stale-pass.json'
    report.write_text(json.dumps({'run_id':'old-run','status':'pass','engine':{'source_commit':COMMIT},'cases':{key:{'status':'pass'} for key in [*CASES,'modifier']}}))
    env=dict(os.environ,PYTEST_ADDOPTS='-k test_closed_box')
    result=subprocess.run([sys.executable,str(runner),'--app',str(app),'--source-commit',COMMIT,'--report',str(report)],env=env,capture_output=True,text=True)
    fresh=json.loads(report.read_text())
    assert result.returncode!=0, 'Selecting no native cases must not reuse an older passing report'
    assert fresh['status']=='fail'
    assert fresh['run_id']!='old-run'
    assert not fresh.get('engine')
    assert all(case['status']=='not_run' for case in fresh['cases'].values())

def test_observer_rejects_relative_xyz_and_extruding_arcs():
    import pytest
    for text in ('G91\nG1 X1 E1', 'M83\nG2 X1 I.5 E.2', 'M83\nG3 I1 E.2'):
        with pytest.raises(ValueError,match='Unsupported native regression motion'):
            observe(text)

def test_observer_allows_nonextruding_arc_endpoint_updates():
    result=observe('M83\nG3 X2 Z1 I1 F3000\nG1 X4 E.2 F1200')
    assert result['moves'][0]['length']==2
    assert result['moves'][0]['z']==1

def test_required_completion_requires_current_identity_and_each_case_run_id():
    import copy
    import runpy
    from pathlib import Path
    runner=runpy.run_path(str(Path(__file__).resolve().parents[2]/'scripts/run_native_regressions.py'))
    initial={'run_id':'current','test_source_commit':'a'*40,'test_source_dirty':False,'requested_engine':{'app':'/public/synthetic.app','source_commit':'b'*40}}
    data={**initial,'engine_run_id':'current','engine':{
        'source_commit':'b'*40,'native_version':'2.4.1','executable_sha256':'c'*64,'resources_sha256':'d'*64,
        'executable':'/public/synthetic.app/Contents/MacOS/Snapmaker_Orca','resources':'/public/synthetic.app/Contents/Resources'},
        'cases':{key:{'status':'pass','run_id':'current'} for key in runner['EXPECTED']}}
    assert runner['complete'](data,initial)
    for field in ('run_id','engine_run_id','test_source_commit','test_source_dirty'):
        bad=copy.deepcopy(data);bad[field]='old'
        assert not runner['complete'](bad,initial)
    for field in ('source_commit','executable','resources','executable_sha256','resources_sha256','native_version'):
        bad=copy.deepcopy(data);bad['engine'][field]='old'
        assert not runner['complete'](bad,initial)
    for key in runner['EXPECTED']:
        bad=copy.deepcopy(data);bad['cases'][key]['run_id']='old'
        assert not runner['complete'](bad,initial)
