#!/usr/bin/env python3
"""Required native integration: missing, stale, omitted and skipped cases fail."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

EXPECTED = {'outer_wall_speed','inner_wall_speed','layer_height','wall_loops',
            'sparse_infill_density','enable_support','support_type',
            'nozzle_temperature','cool_plate_temp','additional_cooling_fan_speed','modifier'}

def complete(data, initial):
    """Only this subprocess's fixture and all case records can certify its run."""
    run_id=initial['run_id']
    if any(data.get(k)!=initial[k] for k in ('run_id','test_source_commit','test_source_dirty','requested_engine')):
        return False
    if data.get('engine_run_id')!=run_id:
        return False
    engine=data.get('engine',{})
    if not isinstance(engine,dict): return False
    app=Path(initial['requested_engine']['app'])/'Contents'
    if (engine.get('source_commit')!=initial['requested_engine']['source_commit']
            or engine.get('executable')!=str(app/'MacOS/Snapmaker_Orca')
            or engine.get('resources')!=str(app/'Resources')
            or engine.get('native_version')!='2.4.1'):
        return False
    if any(not re.fullmatch('[0-9a-f]{64}',engine.get(key,''))
           for key in ('executable_sha256','resources_sha256')):
        return False
    cases=data.get('cases',{})
    return isinstance(cases,dict) and set(cases)==EXPECTED and all(
        isinstance(case,dict) and
        case.get('status')=='pass' and case.get('run_id')==run_id
        for case in cases.values())

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app',default='/Applications/Snapmaker Orca.app')
    p.add_argument('--source-commit',required=True,help='operator-declared 40-character source commit')
    p.add_argument('--report',default='.temp/native-regressions.json')
    a=p.parse_args();root=Path(__file__).resolve().parents[1];report=Path(a.report).resolve()
    report.parent.mkdir(parents=True,exist_ok=True)
    source_commit=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    source_dirty=bool(subprocess.run(['git','status','--porcelain'],cwd=root,capture_output=True,text=True,check=True).stdout)
    initial={'run_id':uuid.uuid4().hex,'status':'pending','test_source_commit':source_commit,'test_source_dirty':source_dirty,
             'requested_engine':{'app':str(Path(a.app).resolve()),'source_commit':a.source_commit},
             'cases':{key:{'status':'not_run'} for key in sorted(EXPECTED)}}
    # Truncate any earlier evidence BEFORE dispatch, even when pytest selects no fixture.
    report.write_text(json.dumps(initial,indent=2)+'\n')
    if not (Path(a.app)/'Contents/MacOS/Snapmaker_Orca').is_file():
        initial.update(status='fail',reason='required native engine absent')
        report.write_text(json.dumps(initial,indent=2)+'\n')
        p.error('Required native engine is absent')
    env=dict(os.environ,SNAPMAKER_NATIVE='1',SNAPMAKER_NATIVE_REQUIRED='1',
             SNAPMAKER_NATIVE_APP=initial['requested_engine']['app'],
             SNAPMAKER_NATIVE_COMMIT=a.source_commit,SNAPMAKER_NATIVE_REPORT=str(report),
             SNAPMAKER_NATIVE_RUN_ID=initial['run_id'],SNAPMAKER_NATIVE_TEST_COMMIT=source_commit,
             PYTHONPATH=os.pathsep.join((str(root/'src'),str(root/'tests'))))
    result=subprocess.run([sys.executable,'-m','pytest','tests/native','-q','--basetemp',str(report.parent/'native-pytest')],cwd=root,env=env)
    try:
        data=json.loads(report.read_text())
        if not isinstance(data,dict): raise ValueError('Invalid report')
    except (OSError,ValueError):
        data={**initial,'reason':'missing or invalid current run report'}
    success=result.returncode==0 and complete(data,initial)
    data['status']='pass' if success else 'fail'
    if not success: data['reason']='Required native coverage incomplete or not from the current run'
    report.write_text(json.dumps(data,indent=2)+'\n')
    if not success:
        print('Required native coverage incomplete; inspect JSON report',file=sys.stderr)
        return result.returncode or 1
    print(f'Native evidence: {report}');return 0

if __name__=='__main__': raise SystemExit(main())
