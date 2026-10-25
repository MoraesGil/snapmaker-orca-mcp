"""Revision provenance and actual native feedrate effects through MCP HTTP."""
import json
from pathlib import Path
import zipfile
from xml.etree import ElementTree as ET

from .harness import assert_effect, feature_moves
from .http_transport import digest, gcode, rejected


async def semantics(session, call, load):
    loaded = await load('semantic')
    pid = loaded['project_id']
    observations = []
    for speed in (20, 60):
        status = await call(session, 'get_status', project_id=pid)
        revision = await call(session, 'set_config', project_id=pid,
                              changes={'outer_wall_speed': [speed, speed]}, expected_sha256=status['sha256'])
        with zipfile.ZipFile(revision['path']) as archive:
            assert json.loads(archive.read('Metadata/project_settings.config'))['outer_wall_speed'] == [str(speed)] * 2
        assert digest(revision['path']) == revision['sha256']
        job = await call(session, 'slice_project', project_id=pid, force=True)
        assert job['source']['sha256'] == revision['sha256']
        observations.append(gcode(job))
    assert_effect('outer_wall_speed', *observations)
    manifest = await session.read_resource(job['manifest_uri'])
    assert json.loads(manifest.contents[0].text) == job
    props = await session.read_resource(job['props_uri'])
    props_path = Path(job['plates'][0]['gcode_path']).parent / 'props.json'
    assert json.loads(props.contents[0].text) == json.loads(props_path.read_text())
    lines = json.loads((await session.read_resource(job['plates'][0]['gcode_lines_uri'])).contents[0].text)
    assert lines['job_id'] == job['job_id']
    assert lines['lines'] == Path(job['plates'][0]['gcode_path']).read_text().splitlines()[:200]
    recovered = await call(session, 'recover_3mf', gcode_file=job['plates'][0]['gcode_path'])
    assert Path(recovered['path']).read_bytes() == Path(revision['path']).read_bytes()

    revision = await call(session, 'add_modifier', project_id=pid, object_id=1,
                          center=[6., 12., 2.5], dimensions=[12., 30., 10.],
                          changes={'outer_wall_speed': [20, 20]}, name='Synthetic speed region')
    with zipfile.ZipFile(revision['path']) as archive:
        xml = ET.fromstring(archive.read('Metadata/model_settings.config'))
        assert any(n.get('value') == 'modifier_part' or n.get('subtype') == 'modifier_part' for n in xml.iter())
    walls = feature_moves(gcode(await call(session, 'slice_project', project_id=pid, force=True)), 'Outer wall')
    xmin, xmax = min(m['x'] for m in walls), max(m['x'] for m in walls)
    assert any(abs(m['f'] - 1200) < 1 for m in walls if m['x'] < xmin + 1)
    assert any(abs(m['f'] - 3600) < 1 for m in walls if m['x'] > xmax - 1)

    state = await call(session, 'get_status', project_id=pid)
    saved = Path(state['path']).read_bytes()
    await rejected(session, 'set_config', project_id=pid, changes={'layer_height': .12}, expected_sha256=loaded['sha256'])
    assert (await call(session, 'get_status', project_id=pid))['sha256'] == state['sha256']
    await rejected(session, 'edit_project', project_id=pid, operations=[
        {'operation': 'set_config', 'changes': {'layer_height': .12}}, {'operation': 'unknown_operation'}])
    assert (await call(session, 'get_status', project_id=pid))['sha256'] == state['sha256']
    await rejected(session, 'set_config', project_id=pid, changes={'post_process': ['/tmp/not-allowed']})
    current = await call(session, 'get_status', project_id=pid)
    assert current['sha256'] == state['sha256'] and Path(current['path']).read_bytes() == saved
    applied = await call(session, 'apply_and_slice', project_id=pid, changes={'nozzle_temperature': [215]})
    assert applied['slice']['source']['sha256'] == applied['project']['sha256']
    assert any(op in ('M104', 'M109') and args.get('S') == 215 for op, args in gcode(applied['slice'])['commands'])
    direct = await load('direct')
    assert gcode(await call(session, 'slice_3mf', input_3mf=direct['source_path'], plate=1, force=True))['moves']

    return {'outer_wall_speed_mm_s': [20, 60], 'regional_speed_mm_s': [20, 60],
            'applied_nozzle_temperature_c': 215, 'exact_recovery': True}
