import json
import zipfile
from pathlib import Path
import pytest
from snapmaker_slicer_mcp.editor import Editor
from snapmaker_slicer_mcp.service import Slicer
from .harness import CASES, official_config, make_project, observe, assert_effect
from .conftest import record

@pytest.mark.parametrize('key',list(CASES))
def test_real_native_property(native_engine,tmp_path,key):
    editor=Editor(Slicer(native_engine,tmp_path/'slicer',timeout=120))
    config=official_config(native_engine,tmp_path)
    if key=='support_type': config['enable_support']='1'
    observations=[]; artifacts=[]
    try:
        for i,value in enumerate(CASES[key]):
            path=make_project(tmp_path/f'{key}-{i}.3mf',config,overhang=key in ('enable_support','support_type'))
            source=path.read_bytes()
            loaded=editor.load_model(str(path));pid=loaded['project_id']
            revision=editor.set_config(pid,{key:value})
            assert path.read_bytes()==source
            with zipfile.ZipFile(revision['path']) as z:
                saved=json.loads(z.read('Metadata/project_settings.config'))
                expected = [str(v) for v in value] if isinstance(value,list) else ('1' if value is True else '0' if value is False else str(value))
                assert saved[key] == expected
            result=editor.slice_project(pid)
            output=Path(result['plates'][0]['gcode_path'])
            observations.append(observe(output.read_text()))
            artifacts.append({'source_3mf':revision['path'],'gcode':str(output),'persisted':saved[key]})
        assert_effect(key,*observations)
    except Exception as exc:
        record(native_engine,key,'fail',{'error_type':type(exc).__name__,'artifacts':artifacts})
        raise
    record(native_engine,key,'pass',{'artifacts':artifacts,'observations':[{'extrusion_moves':len(o['moves']),'feature_labels':sorted({m['feature'] for m in o['moves']})} for o in observations],'scope':'OFF-only policy; no nonzero fan experiment' if key=='additional_cooling_fan_speed' else 'paired native command/extrusion semantics'})
