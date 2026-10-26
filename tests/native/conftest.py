import json
import os
import uuid
import subprocess
from pathlib import Path
import pytest
from . import harness

@pytest.fixture(scope='session')
def native_engine(tmp_path_factory):
    if os.environ.get('SNAPMAKER_NATIVE')!='1':
        pytest.fail('Selected native tests require the official engine; use scripts/run_native_regressions.py. '
                    'For the engine-independent profile select -m "not native" explicitly.', pytrace=False)
    from snapmaker_slicer_mcp.engine import Engine
    app=Path(os.environ.get('SNAPMAKER_NATIVE_APP','/Applications/Snapmaker Orca.app'))/'Contents'
    if not (app/'MacOS/Snapmaker_Orca').is_file():
        pytest.fail('Required native engine is absent', pytrace=False)
    harness.APP=app
    commit=os.environ.get('SNAPMAKER_NATIVE_COMMIT',harness.COMMIT)
    assert commit==harness.COMMIT, 'Explicit regression expectation update required for a changed source binding'
    engine=Engine(Engine.bind(app/'MacOS/Snapmaker_Orca',commit,app/'Resources'))
    version,build=harness.native_version()
    assert version=='2.4.1', 'Explicit compatibility update required for a changed native version'
    report=Path(os.environ.get('SNAPMAKER_NATIVE_REPORT',str(tmp_path_factory.getbasetemp()/'native-report.json'))).resolve()
    report.parent.mkdir(parents=True,exist_ok=True)
    identity={**engine.manifest,'native_version':version,'native_build':build}
    root=Path(__file__).resolve().parents[2]
    test_commit=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    test_dirty=bool(subprocess.run(['git','status','--porcelain'],cwd=root,capture_output=True,text=True,check=True).stdout)
    run_id=os.environ.get('SNAPMAKER_NATIVE_RUN_ID')
    if run_id:
        data=json.loads(report.read_text())
        assert data['run_id']==run_id, 'Native report belongs to another run'
        assert data['test_source_commit']==os.environ['SNAPMAKER_NATIVE_TEST_COMMIT']==test_commit
        assert data['test_source_dirty']==test_dirty
        assert data['requested_engine']=={'app':str(app.parent.resolve()),'source_commit':commit}
    else:
        run_id=uuid.uuid4().hex
        data={'run_id':run_id,'cases':{key:{'status':'not_run'} for key in [*harness.CASES,'modifier']}}
    data.update(engine=identity,engine_run_id=run_id,test_source_commit=test_commit,test_source_dirty=test_dirty)
    report.write_text(json.dumps(data,indent=2))
    engine.regression_run_id=run_id
    engine.regression_report=report
    return engine

def record(engine,key,status,detail=None):
    path=engine.regression_report
    report=json.loads(path.read_text())
    assert report['run_id']==engine.regression_run_id, 'Native report changed during this run'
    report['cases'][key]={'status':status,**(detail or {}),'run_id':engine.regression_run_id}
    path.write_text(json.dumps(report,indent=2)+'\n')
