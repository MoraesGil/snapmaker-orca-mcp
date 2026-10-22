import json
import os
from pathlib import Path
import pytest
from . import harness

@pytest.fixture(scope='session')
def native_engine(tmp_path_factory):
    if os.environ.get('SNAPMAKER_NATIVE')!='1':
        pytest.skip('native semantics not run; set SNAPMAKER_NATIVE=1 or use required runner')
    from snapmaker_slicer_mcp.engine import Engine
    app=Path(os.environ.get('SNAPMAKER_NATIVE_APP','/Applications/Snapmaker Orca.app'))/'Contents'
    if not (app/'MacOS/Snapmaker_Orca').is_file():
        if os.environ.get('SNAPMAKER_NATIVE_REQUIRED')=='1': pytest.fail('Required native engine is absent')
        pytest.skip('native engine unavailable; no semantic evidence')
    harness.APP=app
    commit=os.environ.get('SNAPMAKER_NATIVE_COMMIT',harness.COMMIT)
    assert commit==harness.COMMIT, 'Explicit regression expectation update required for a changed source binding'
    engine=Engine(Engine.bind(app/'MacOS/Snapmaker_Orca',commit,app/'Resources'))
    version,build=harness.native_version()
    assert version=='2.4.1', 'Explicit compatibility update required for a changed native version'
    report=Path(os.environ.get('SNAPMAKER_NATIVE_REPORT',str(tmp_path_factory.getbasetemp()/'native-report.json'))).resolve()
    report.parent.mkdir(parents=True,exist_ok=True)
    identity={**engine.manifest,'native_version':version,'native_build':build}
    report.write_text(json.dumps({'engine':identity,'cases':{key:{'status':'not_run'} for key in [*harness.CASES,'modifier']}},indent=2))
    engine.regression_report=report
    return engine

def record(engine,key,status,detail=None):
    path=engine.regression_report
    report=json.loads(path.read_text());report['cases'][key]={'status':status,**(detail or {})}
    path.write_text(json.dumps(report,indent=2)+'\n')
