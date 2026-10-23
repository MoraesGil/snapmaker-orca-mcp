"""Native event order/volumes; this does not prove physical priming or adhesion."""
from pathlib import Path
import json
import sys

import pytest

from snapmaker_slicer_mcp.editor import Editor
from snapmaker_slicer_mcp.service import Slicer
from snapmaker_slicer_mcp.project import Project
from snapmaker_slicer_mcp.plugins.custom_tower.workflow import CustomTowerPlugin
from snapmaker_slicer_mcp.plugins.custom_tower.archive import signed_towers
from .tower_fixture import tower_fixture
from .conftest import record


@pytest.mark.parametrize('models,interface,inverse',[([0],1,False),([0,2],1,False),([1,2,3],0,False),([2],0,True)])
def test_real_native_custom_tower(native_engine,tmp_path,monkeypatch,models,interface,inverse):
    source=tower_fixture(tmp_path/'fixture.3mf',native_engine,tmp_path,models,interface,inverse)
    original=source.read_bytes()
    editor=Editor(Slicer(native_engine,tmp_path/'slicer',timeout=180))
    loaded=editor.load_model(str(source)); plugin=CustomTowerPlugin(editor,editor.slicer)
    config=dict(model_tools=models,interface_tool=interface,primary_object_id=1,
                minimum_width_mm=24,maximum_width_mm=48,
                purge_volume_mm3={t:1 for t in models+[interface]})
    result=plugin.add(loaded['project_id'],1,config,loaded['sha256'])
    assert result['status']=='digitally_validated',result
    assert result['audit']['changes_checked']>50
    assert not result['audit']['failures']
    assert source.read_bytes()==original
    saved=Project(Path(result['project']['path']))
    assert saved.config['enable_prime_tower']=='0'
    assert saved.config['purge_in_prime_tower']=='0'
    assert signed_towers(saved)==[result['tower_object_id']]
    assert not plugin.status(loaded['project_id'])['requires_recalculation']
    # An actual model edit invalidates the receipt and the old auxiliary is
    # removed before the new prepass; exactly one replacement is published.
    if models==[0]:
        edited=editor.edit_project(loaded['project_id'],[{'operation':'transform_object',
            'object_id':1,'translate':[2,0,0]}],result['project']['sha256'])
        assert plugin.status(loaded['project_id'])['requires_recalculation']
        native_slice=editor.slicer.slice_3mf
        def mutate_staging_after_snapshot(path,*args,**kwargs):
            job=native_slice(path,*args,**kwargs)
            if Path(path).name.startswith('candidate-'):
                with Path(path).open('ab') as stream: stream.write(b'changed-after-native-snapshot')
            return job
        monkeypatch.setattr(editor.slicer,'slice_3mf',mutate_staging_after_snapshot)
        again=plugin.add(loaded['project_id'],1,config,edited['sha256'])
        assert again['status']=='digitally_validated',again
        assert again['replaced_tower_ids']==[result['tower_object_id']]
        assert len(signed_towers(Project(Path(again['project']['path']))))==1
        assert again['bindings']['prepass_gcode_sha256']!=result['bindings']['prepass_gcode_sha256']
        assert again['project']['sha256']==again['slice']['source']['sha256']
    label='inverse' if inverse else {1:'mono',2:'dual',3:'tri'}[len(models)]
    record(native_engine,'custom_tower_'+label,'pass',{'scope':'native synthetic Y tower; complete changes, measured positive extrusion, role mapping and fixed bed/nozzle targets',
        'changes_checked':result['audit']['changes_checked'],'model_tools':models,'interface_tool':interface,
        'gcode_sha256':result['slice']['plates'][0]['sha256']})


async def test_real_native_custom_tower_through_mcp(native_engine,tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    source=tower_fixture(tmp_path/'protocol.3mf',native_engine,tmp_path)
    engine_file=tmp_path/'engine.json'; engine_file.write_text(json.dumps(native_engine.manifest))
    argv=['-m','snapmaker_slicer_mcp.cli','--engine',str(engine_file),
          '--artifacts',str(tmp_path/'artifacts'),'serve','--no-update-check','--plugin','custom-tower']
    params=StdioServerParameters(command=sys.executable,args=argv)
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            assert 'add_custom_tower' in {t.name for t in (await session.list_tools()).tools}
            loaded=await session.call_tool('load_model',{'path':str(source)})
            assert not loaded.isError
            state=loaded.structuredContent or json.loads(loaded.content[0].text)
            call=await session.call_tool('add_custom_tower',{'project_id':state['project_id'],
                'plate':1,'expected_sha256':state['sha256'],
                'options':{'model_tools':[0],'interface_tool':1,'primary_object_id':1,
                           'purge_volume_mm3':{'0':12,'1':12}}})
            assert not call.isError,call.content
            result=call.structuredContent or json.loads(call.content[0].text)
            assert result['status']=='digitally_validated',result
            assert result['audit']['changes_checked']>50
            assert not result['audit']['failures']
            assert result['bindings']['prepass_3mf_sha256']!=result['slice']['source']['sha256']
            status=await session.call_tool('custom_tower_status',{'project_id':state['project_id']})
            assert not status.isError
            assert not (status.structuredContent or json.loads(status.content[0].text))['requires_recalculation']
            record(native_engine,'custom_tower_mcp','pass',{'scope':'real stdio tool call; native prepass/final slice; 12 mm3 requested per toolchange',
                'changes_checked':result['audit']['changes_checked'],'gcode_sha256':result['slice']['plates'][0]['sha256']})
