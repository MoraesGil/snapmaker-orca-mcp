#!/usr/bin/env python3
"""Required native integration: missing engine and skips are failures."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app',default='/Applications/Snapmaker Orca.app')
    p.add_argument('--source-commit',required=True,help='operator-declared 40-character source commit')
    p.add_argument('--report',default='.temp/native-regressions.json')
    a=p.parse_args();root=Path(__file__).resolve().parents[1];report=Path(a.report).resolve()
    report.parent.mkdir(parents=True,exist_ok=True)
    if not (Path(a.app)/'Contents/MacOS/Snapmaker_Orca').is_file():
        report.write_text(json.dumps({'status':'fail','reason':'required native engine absent','cases':{}},indent=2)+'\n')
        p.error('Required native engine is absent')
    env=dict(os.environ,SNAPMAKER_NATIVE='1',SNAPMAKER_NATIVE_REQUIRED='1',SNAPMAKER_NATIVE_APP=str(Path(a.app).resolve()),SNAPMAKER_NATIVE_COMMIT=a.source_commit,SNAPMAKER_NATIVE_REPORT=str(report),PYTHONPATH=os.pathsep.join((str(root/'src'),str(root/'tests'))))
    result=subprocess.run([sys.executable,'-m','pytest','tests/native','-q','--basetemp',str(report.parent/'native-pytest')],cwd=root,env=env)
    data=json.loads(report.read_text()) if report.is_file() else {'cases':{}}
    data['status']='fail' if result.returncode else 'pending'
    data['test_source_commit']=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    report.write_text(json.dumps(data,indent=2)+'\n')
    if result.returncode: return result.returncode
    expected={'outer_wall_speed','inner_wall_speed','layer_height','wall_loops','sparse_infill_density','enable_support','support_type','nozzle_temperature','cool_plate_temp','additional_cooling_fan_speed','modifier'}
    if set(data['cases'])!=expected or any(v['status']!='pass' for v in data['cases'].values()):
        data['status']='fail'
        report.write_text(json.dumps(data,indent=2)+'\n')
        print('Required native coverage incomplete; inspect JSON report',file=sys.stderr);return 1
    data['status']='pass'
    report.write_text(json.dumps(data,indent=2)+'\n')
    print(f'Native evidence: {report}');return 0

if __name__=='__main__': raise SystemExit(main())
