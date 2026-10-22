"""Synthetic geometry and command-level observations, independent of config comments."""
from __future__ import annotations
import json
import math
import plistlib
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

COMMIT = '9bf2f18eaff7cc0655cbdeef6a215660085ecb0f'
APP = Path('/Applications/Snapmaker Orca.app/Contents')
NS = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'

def box_mesh(origin, dimensions):
    x,y,z = origin; a,b,c = dimensions
    vertices = [(x+i*a,y+j*b,z+k*c) for k in (0,1) for j in (0,1) for i in (0,1)]
    faces = [(0,2,3),(0,3,1),(4,5,7),(4,7,6),(0,1,5),(0,5,4),
             (2,6,7),(2,7,3),(0,4,6),(0,6,2),(1,3,7),(1,7,5)]
    return vertices, faces

def make_project(path, config, overhang=False):
    root=ET.Element('model', {'xmlns':NS,'unit':'millimeter'})
    resources=ET.SubElement(root,'resources'); obj=ET.SubElement(resources,'object',id='1',type='model')
    mesh=ET.SubElement(obj,'mesh'); verts=ET.SubElement(mesh,'vertices'); tris=ET.SubElement(mesh,'triangles')
    # Two closed intersecting solids form a supported post with a broad roof.
    boxes = [((0,0,0),(8,8,8)),((-6,-6,7.8),(20,20,2.2))] if overhang else [((0,0,0),(24,24,5))]
    offset=0
    for origin,dimensions in boxes:
        vv,ff=box_mesh(origin,dimensions)
        for x,y,z in vv: ET.SubElement(verts,'vertex',x=str(x),y=str(y),z=str(z))
        for face in ff: ET.SubElement(tris,'triangle',**{f'v{i+1}':str(v+offset) for i,v in enumerate(face)})
        offset+=len(vv)
    ET.SubElement(ET.SubElement(root,'build'),'item',objectid='1',transform='1 0 0 0 1 0 0 0 1 100 100 0')
    settings='<config><object id="1"><metadata key="name" value="Synthetic public fixture"/></object><plate><metadata key="plater_id" value="1"/><model_instance><metadata key="object_id" value="1"/><metadata key="instance_id" value="0"/><metadata key="identify_id" value="1"/></model_instance></plate></config>'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr('3D/3dmodel.model',ET.tostring(root))
        z.writestr('Metadata/model_settings.config',settings)
        z.writestr('Metadata/project_settings.config',json.dumps(config))
    return path

def observe(text):
    """Observe absolute XYZ linear extrusion only; reject G91/extruding arcs.

    Nonextruding arc endpoints update modal state but never count as path length.
    This deliberately bounded oracle is not a general CNC interpreter.
    """
    feature=''; pos={'X':0.,'Y':0.,'Z':0.,'E':0.}; feed=0.; relative=False; moves=[]; commands=[]
    for line in text.splitlines():
        if line.startswith(('; FEATURE:', ';TYPE:')): feature=line.split(':',1)[1].strip()
        code=line.split(';',1)[0].strip()
        if not code: continue
        parts=code.split(); op=parts[0]; vals={m.group(1):float(m.group(2)) for p in parts[1:] if (m:=re.fullmatch(r'([A-Z])(-?(?:\d+(?:\.\d*)?|\.\d+))',p))}
        commands.append((op,vals))
        if op=='G91': raise ValueError('Unsupported native regression motion: relative XYZ (G91)')
        if op=='M83': relative=True
        if op=='M82': relative=False
        if op=='G92': pos.update({k:v for k,v in vals.items() if k in pos})
        if op not in ('G0','G1','G2','G3'): continue
        feed=vals.get('F',feed); new={k:vals.get(k,v) for k,v in pos.items()}
        extrusion=vals.get('E',0) if relative else new['E']-pos['E']
        if op in ('G2','G3') and extrusion>0:
            raise ValueError('Unsupported native regression motion: extruding arc (G2/G3)')
        distance=math.hypot(new['X']-pos['X'],new['Y']-pos['Y'])
        if extrusion>0 and distance>0:
            moves.append({'feature':feature,'x':new['X'],'y':new['Y'],'z':new['Z'],'f':feed,'length':distance,'e':extrusion})
        pos=new
    return {'moves':moves,'commands':commands}

def feature_moves(observation, name):
    return [m for m in observation['moves'] if m['feature']==name and m['z']>0.8]

def official_config(engine, scratch):
    from snapmaker_slicer_mcp.presets import Catalog, normalize_filament_profile
    catalog=Catalog(engine,[],scratch/'drafts'); config={}
    for kind,name in [('printer','Snapmaker U1 (0.4 nozzle)'),('print','0.20mm Standard @Snapmaker U1 (0.4 nozzle)'),('filament','Generic PLA')]:
        profile=catalog.get_preset_config(kind,name)['config']
        if kind=='filament': profile=normalize_filament_profile(profile)
        config.update(profile)
    config.update(curr_bed_type='Cool Plate',cool_plate_temp=['50'],cool_plate_temp_initial_layer=['50'],additional_cooling_fan_speed=['0'],post_process=[],enable_support='0')
    return config

def native_version():
    with (APP/'Info.plist').open('rb') as f: info=plistlib.load(f)
    return info.get('CFBundleShortVersionString'),info.get('CFBundleVersion')

CASES = {
 'outer_wall_speed': ([20,20],[60,60]),
 'inner_wall_speed': ([25,25],[75,75]),
 'layer_height': (.12,.28), 'wall_loops': (2,4),
 'sparse_infill_density': ('10%','40%'), 'enable_support': (False,True),
 'support_type': ('normal(auto)','tree(auto)'),
 'nozzle_temperature': ([205],[225]),
 'cool_plate_temp': ([45],[55]),
 'additional_cooling_fan_speed': ([0],[0]),
}

def assert_effect(key, low, high):
    """Independent semantic expectations; never consult saved config comments."""
    if key in ('outer_wall_speed','inner_wall_speed'):
        label='Outer wall' if key=='outer_wall_speed' else 'Inner wall'
        a=feature_moves(low,label); b=feature_moves(high,label)
        assert a and b, f'{label}: no extrusion moves'
        # First-layer speeds/acceleration do not qualify as regional speed proof.
        expected=1200 if key=='outer_wall_speed' else 1500
        assert any(abs(m['f']-expected)<1 for m in a)
        assert max(m['f'] for m in b)>max(m['f'] for m in a)*1.5
    elif key=='layer_height':
        a=sorted({m['z'] for m in low['moves'] if m['feature']=='Outer wall'})
        b=sorted({m['z'] for m in high['moves'] if m['feature']=='Outer wall'})
        assert len(a)>len(b)*1.5
        assert any(abs(y-x-.12)<.005 for x,y in zip(a,a[1:]))
        assert any(abs(y-x-.28)<.005 for x,y in zip(b,b[1:]))
    elif key in ('wall_loops','sparse_infill_density'):
        label='Inner wall' if key=='wall_loops' else 'Sparse infill'
        a=feature_moves(low,label); b=feature_moves(high,label)
        assert a and b
        assert sum(m['length'] for m in b)>sum(m['length'] for m in a)*1.5
    elif key in ('enable_support','support_type'):
        a=[m for m in low['moves'] if 'Support' in m['feature']]
        b=[m for m in high['moves'] if 'Support' in m['feature']]
        if key=='enable_support': assert not a and b
        else:
            assert a and b
            # Real support extrusion geometry differs between normal and tree.
            assert abs(sum(m['length'] for m in a)-sum(m['length'] for m in b))>10
    elif key in ('nozzle_temperature','cool_plate_temp'):
        ops=('M104','M109') if key=='nozzle_temperature' else ('M140','M190')
        targets=(205,225) if key=='nozzle_temperature' else (45,55)
        for obs,target in zip((low,high),targets):
            assert any(op in ops and args.get('S')==target for op,args in obs['commands'])
    elif key=='additional_cooling_fan_speed':
        # Owner policy forbids a nonzero test: explicit OFF-only evidence.
        for obs in (low,high):
            fans=[v.get('S',0) for op,v in obs['commands'] if op=='M106' and v.get('P')==2]
            assert fans and max(fans)==0
    else: raise AssertionError(f'No semantic oracle for {key}')
