from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile
from snapmaker_slicer_mcp.editor import Editor
from snapmaker_slicer_mcp.service import Slicer
from .harness import official_config,make_project,observe,feature_moves
from .conftest import record

def test_real_native_modifier(native_engine,tmp_path):
    editor=Editor(Slicer(native_engine,tmp_path/'slicer',timeout=120))
    try:
        config=official_config(native_engine,tmp_path)
        path=make_project(tmp_path/'modifier.3mf',config)
        loaded=editor.load_model(str(path));pid=loaded['project_id']
        editor.set_config(pid,{'outer_wall_speed':[60,60]})
        baseline=editor.slice_project(pid)
        before=observe(Path(baseline['plates'][0]['gcode_path']).read_text())
        revision=editor.add_modifier(pid,object_id=1,center=[6,12,2.5],dimensions=[12,30,10],changes={'outer_wall_speed':[20,20]},name='Synthetic speed region')
        with zipfile.ZipFile(revision['path']) as z:
            xml=ET.fromstring(z.read('Metadata/model_settings.config'))
            assert any(n.get('value')=='modifier_part' or n.get('subtype')=='modifier_part' for n in xml.iter())
        result=editor.slice_project(pid)
        output=Path(result['plates'][0]['gcode_path'])
        after=observe(output.read_text());walls=feature_moves(after,'Outer wall')
        original=feature_moves(before,'Outer wall');assert original and walls
        xmin=min(m['x'] for m in walls);xmax=max(m['x'] for m in walls)
        inside=[m for m in walls if m['x']<xmin+1]
        outside=[m for m in walls if m['x']>xmax-1]
        assert inside and outside
        assert any(abs(m['f']-1200)<1 for m in inside)
        assert any(abs(m['f']-3600)<1 for m in outside)
        assert any(abs(m['f']-3600)<1 for m in original)
        record(native_engine,'modifier','pass',{'source_3mf':revision['path'],'gcode':str(output),'scope':'object-local box; interior 20mm/s, exterior baseline 60mm/s'})
    except Exception as exc:
        record(native_engine,'modifier','fail',{'error_type':type(exc).__name__})
        raise
