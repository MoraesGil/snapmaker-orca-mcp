"""Synthetic MCP workflows: edits, drafts, geometry and layer operations."""
import uuid
from pathlib import Path
import zipfile
from .http_geometry import bounds, z_levels
from .http_transport import gcode, rejected

async def edits(session, call, load):
    loaded = await load('editing')
    pid = loaded['project_id']
    state = await call(session, 'get_config', project_id=pid, keys=['layer_height'])
    assert float(state['config']['layer_height']) == 0.2
    await call(session, 'edit_project', project_id=pid, operations=[{'operation': 'set_config', 'changes': {'layer_height': .16}}])
    assert float((await call(session, 'get_config', project_id=pid, keys=['layer_height']))['config']['layer_height']) == .16
    await call(session, 'set_object_config', project_id=pid, object_id=1, changes={'wall_loops': 4})
    objects = (await call(session, 'list_objects', project_id=pid))['objects']
    assert objects[0]['config']['wall_loops'] == '4'
    previous = objects[0]['instances'][0]['transform']
    revision = await call(session, 'transform_object', project_id=pid, object_id=1, translate=[5.0, 0.0, 0.0])
    current = (await call(session, 'list_objects', project_id=pid))['objects'][0]['instances'][0]['transform']
    assert abs(current[9] - previous[9] - 5) < 1e-6
    await call(session, 'duplicate_object', project_id=pid, object_id=1)
    objects = (await call(session, 'list_objects', project_id=pid))['objects']
    duplicate = next(obj['object_id'] for obj in objects if obj['object_id'] != 1)
    await call(session, 'add_plate', project_id=pid, name='Synthetic second plate')
    assert len((await call(session, 'list_plates', project_id=pid))['plates']) == 2
    await call(session, 'move_object_to_plate', project_id=pid, object_id=duplicate, plate=2)
    plates = (await call(session, 'list_plates', project_id=pid))['plates']
    assert any(obj['object_id'] == duplicate for obj in plates[1]['objects'])
    assert (await call(session, 'select_plate', project_id=pid, plate=2))['selected_plate'] == 2
    await call(session, 'select_plate', project_id=pid, plate=1)
    await call(session, 'delete_object', project_id=pid, object_id=duplicate)
    await call(session, 'delete_plate', project_id=pid, plate=2)
    assert len((await call(session, 'list_objects', project_id=pid))['objects']) == 1
    assert len((await call(session, 'list_plates', project_id=pid))['plates']) == 1
    job = await call(session, 'slice_project', project_id=pid, force=True)
    assert gcode(job)['moves']
    return {'scope': 'synthetic revisions, geometry/plate membership, final real slice'}

async def presets(session, call, load):
    loaded = await load('preset-draft')
    pid = loaded['project_id']
    named = '0.20mm Standard @Snapmaker U1 (0.4 nozzle)'
    catalog = await call(session, 'list_presets', type='print', include_system=True)
    assert any(rec['name'] == named and rec['read_only'] for rec in catalog['presets'])
    baseline = await call(session, 'get_preset_config', type='print', name=named)
    assert baseline['read_only']
    await call(session, 'select_preset', project_id=pid, type='print', name=named)
    name = 'Acceptance ' + uuid.uuid4().hex[:12]
    draft = await call(session, 'save_preset', project_id=pid, type='print', name=name)
    assert draft['source'] == 'draft' and not draft['read_only']
    assert Path(draft['path']).is_relative_to(Path(loaded['source_path']).parent / 'artifacts' / 'preset-drafts')
    edited = await call(session, 'edit_preset', type='print', name=name, changes={'layer_height': .16})
    assert float(edited['config']['layer_height']) == .16
    renamed = name + ' renamed'
    renamed_record = await call(session, 'rename_preset', type='print', name=name, new_name=renamed)
    assert not Path(draft['path']).exists() and Path(renamed_record['path']).is_file()
    assert (await call(session, 'get_preset_config', type='print', name=renamed))['source'] == 'draft'
    deleted = await call(session, 'delete_preset', type='print', name=renamed)
    assert deleted['deleted'] is True and not Path(renamed_record['path']).exists()
    assert not any(rec['name'] in (name, renamed) for rec in (await call(session, 'list_presets', type='print'))['presets'])
    await rejected(session, 'edit_preset', type='print', name=named, changes={'layer_height': .16})
    assert (await call(session, 'describe_setting', key='layer_height'))['key'] == 'layer_height'
    assert any(s['key'] == 'layer_height' for s in (await call(session, 'search_settings', query='layer_height'))['settings'])
    return {'scope': 'system read-only; temporary artifact draft create/edit/rename/delete'}

async def arrangement(session, call, load):
    loaded = await load('arrangement')
    pid = loaded['project_id']
    await call(session, 'duplicate_object', project_id=pid, object_id=1)
    old = bounds((await call(session, 'get_status', project_id=pid))['path'])
    assert len(old) == 2, 'duplicate count'
    original_boxes = list(old.values())
    assert original_boxes[0] == original_boxes[1], 'fixture must start with overlapping boxes'
    revision = await call(session, 'arrange_plate', project_id=pid, plate=1)
    boxes = list(bounds(revision['path']).values())
    assert len(boxes) == 2
    assert any(a[1] <= b[0] or b[1] <= a[0] for a, b in zip(boxes[0][:2], boxes[1][:2])), 'arranged meshes overlap'
    assert all(0 <= low <= high <= 270 for box in boxes for low, high in box[:2])
    job = await call(session, 'slice_project', project_id=pid, force=True)
    assert gcode(job)['moves']
    return {'scope': 'two overlapping boxes separated on one 270mm plate; actual native transform verification and slice'}

async def orient(session, call, load):
    loaded = await load('orientation')
    pid = loaded['project_id']
    revision = await call(session, 'transform_object', project_id=pid, object_id=1, rotate=[0.0, 90.0, 0.0])
    # Rotation preserves the pivot; explicitly place the 24mm-tall box on the bed.
    lower_z = bounds(revision['path'])[1][2][0]
    revision = await call(session, 'transform_object', project_id=pid, object_id=1, translate=[0.0, 0.0, -lower_z])
    before = bounds(revision['path'])[1]
    assert abs(before[2][1] - before[2][0] - 24) < 1e-5
    revision = await call(session, 'auto_orient', project_id=pid, plate=1)
    after = bounds(revision['path'])[1]
    assert abs(after[2][1] - after[2][0] - 5) < .01
    assert abs(after[2][0]) < .01, 'oriented mesh must rest on bed'
    assert sorted(round(b-a, 3) for a, b in before) == sorted(round(b-a, 3) for a, b in after)
    job = await call(session, 'slice_project', project_id=pid, force=True)
    assert gcode(job)['moves']
    return {'before_height_mm': 24, 'after_height_mm': 5, 'scope': 'single rotated public box'}

async def adaptive(session, call, load):
    loaded = await load('adaptive')
    pid = loaded['project_id']
    before = z_levels(await call(session, 'slice_project', project_id=pid, force=True))
    revision = await call(session, 'set_layer_height', project_id=pid, object_id=1, mode='adaptive', quality=.5)
    with zipfile.ZipFile(revision['path']) as archive:
        assert any('layer_height' in name for name in archive.namelist())
    after = z_levels(await call(session, 'slice_project', project_id=pid, force=True))
    assert len(before) == 25 and len(after) == 16, 'native adaptive layers did not change layer count'
    assert any(b-a > .23 for a, b in zip(after, after[1:]))
    await call(session, 'set_layer_height', project_id=pid, object_id=1, mode='reset')
    reset = z_levels(await call(session, 'slice_project', project_id=pid, force=True))
    assert reset == before
    return {'uniform_layers': len(before), 'adaptive_layers': len(after), 'reset_matches_baseline': True}

async def ranges(session, call, load):
    loaded = await load('height-range')
    pid = loaded['project_id']
    before = z_levels(await call(session, 'slice_project', project_id=pid, force=True))
    revision = await call(session, 'set_height_range', project_id=pid, object_id=1, min_z=.8, max_z=2.0, layer_height=.12)
    with zipfile.ZipFile(revision['path']) as archive:
        assert 'Metadata/layer_config_ranges.xml' in archive.namelist()
    levels = z_levels(await call(session, 'slice_project', project_id=pid, force=True))
    assert any(.8 <= a < b <= 2.0 and abs(b-a-.12) < .005 for a, b in zip(levels, levels[1:]))
    await call(session, 'set_height_range', project_id=pid, object_id=1, clear=True)
    assert z_levels(await call(session, 'slice_project', project_id=pid, force=True)) == before
    return {'scope': '0.12mm native Z steps inside object-local 0.8 to 2.0mm band'}
