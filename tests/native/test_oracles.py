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
