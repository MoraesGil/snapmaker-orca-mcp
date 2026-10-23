"""Surgical, explicitly signed 3MF auxiliaries. Never classify models by shape/name."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
import copy
from xml.etree import ElementTree as ET

from ...project import CORE, IDENTITY, _child, _children, _metadata, _set_metadata, _native_option_value
from .gcode import SIGNATURE
from .geometry import mesh_groups, profile


def geometry_fingerprint(project, oid):
    """Integrity tag, not authentication. Refuse damaged/foreign claimed outputs."""
    def canonical(node):
        return [node.tag,sorted(node.attrib.items()),(node.text or '').strip(),[canonical(e) for e in node]]
    root=project._object(oid)
    resources={int(e.get('id')):e for e in project._resources() if e.get('id','').isdigit()}
    pending=[int(e.get('objectid')) for e in root.iter() if e.tag.endswith('component')]
    visited={oid}; parts=[]
    while pending:
        pid=pending.pop()
        if pid in visited: continue
        if pid not in resources: raise ValueError('Signed tower references an unsupported external resource')
        visited.add(pid); node=resources[pid]; parts.append(canonical(node))
        pending.extend(int(e.get('objectid')) for e in node.iter() if e.tag.endswith('component'))
    settings=copy.deepcopy(project._object_settings(oid))
    if settings is not None: _set_metadata(settings,'slicer_mcp_auxiliary',None)
    body={'root':canonical(root),'parts':sorted(parts,key=lambda p:json.dumps(p,sort_keys=True)),
          'settings':canonical(settings) if settings is not None else None}
    return hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()


def signed_towers(project):
    result = []
    for obj in project.list_objects():
        node = project._object_settings(obj['object_id'])
        meta = _metadata(node) if node is not None else {}
        try:
            marker = json.loads(meta.get('slicer_mcp_auxiliary', '{}'))
        except (ValueError, TypeError):
            continue
        token = marker.get('token', '')
        if (marker.get('schema') == 1 and marker.get('producer') == 'custom-tower'
                and marker.get('role') == 'synthetic_priming_tower'
                and isinstance(token, str) and len(token) == 32
                and all(c in '0123456789abcdef' for c in token)
                and obj['name'] == SIGNATURE + token):
            if marker.get('geometry_sha256') != geometry_fingerprint(project,obj['object_id']):
                raise ValueError('Signed tower integrity changed; review it before automatic replacement')
            result.append(obj['object_id'])
    return result


def remove_signed(project, plate):
    members = {i['object_id'] for i in project.list_plates() if i['plate'] == plate for i in i['objects']}
    removed = []
    for oid in signed_towers(project):
        if oid not in members:
            continue
        instances = next(o['instances'] for o in project.list_objects() if o['object_id'] == oid)
        if any(i['plate'] != plate for i in instances):
            raise ValueError('A signed tower shared between plates cannot be replaced safely')
        parts = _children(project._object_settings(oid), 'part')
        owned = {int(e.get('id')) for e in parts}
        project.delete_object(oid); removed.append(oid)
        referenced = {int(e.get('objectid')) for e in project.model.iter() if e.tag.endswith('component')}
        referenced.update(int(e.get('objectid')) for e in project._items())
        for child in list(project._resources()):
            if int(child.get('id', '0')) in owned - referenced:
                project._resources().remove(child)
    return removed


def normalize_demo(project, ids):
    """Explicit demo IDs must already be assembled on plate 1; enable them."""
    if not ids:
        return
    plates = project.list_plates()
    first = next((p for p in plates if p['plate'] == 1), None)
    if first is None or not set(ids) <= {o['object_id'] for o in first['objects']}:
        raise ValueError('Declared assembled demos must already belong to plate 1')
    declared={(o['object_id'],o['instance_id']) for o in first['objects'] if o['object_id'] in ids}
    for oid in ids:
        for index,item in enumerate(project._items(oid)):
            if (oid,index) in declared: item.set('printable', '1')
    _set_metadata(project._plate(1), 'plater_name', 'DEMO - assembled largest model - DO NOT PRINT')
    _set_metadata(project._plate(1), 'locked', 'true')


def place(plan, result, options, printable_area):
    try:
        points = [[float(v) for v in p.split('x')] for p in printable_area]
        if len(points) != 4 or len({p[0] for p in points}) != 2 or len({p[1] for p in points}) != 2:
            raise ValueError
        bed = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
    except (ValueError, TypeError):
        raise ValueError('Tower placement currently requires a rectangular printable_area') from None
    pp=profile(options.maximum_width_mm); margin=options.brim_mm+options.clearance_mm
    local=[min(x for x,y in pp)-margin,min(y for x,y in pp)-margin,
           max(x for x,y in pp)+margin,max(y for x,y in pp)+margin]
    obstacles=[b.bounds for p in result.phases for b in p.blocks if not result.is_tower(b.name)]
    def valid(center):
        x,y=center; bounds=[local[0]+x,local[1]+y,local[2]+x,local[3]+y]
        return (bounds[0]>=bed[0] and bounds[1]>=bed[1] and bounds[2]<=bed[2] and bounds[3]<=bed[3]
                and all(bounds[2]<b[0] or bounds[0]>b[2] or bounds[3]<b[1] or bounds[1]>b[3] for b in obstacles))
    if options.center_xy:
        if not valid(options.center_xy): raise ValueError('Requested tower/brim position collides or leaves the bed')
        return list(options.center_xy)
    candidates=[]
    for x in range(math.ceil(bed[0]-local[0]), math.floor(bed[2]-local[2])+1, 4):
        for y in range(math.ceil(bed[1]-local[1]), math.floor(bed[3]-local[3])+1, 4):
            if valid((x,y)): candidates.append([x,y])
    if not candidates: raise ValueError('Tower/brim cannot fit without moving approved models')
    # Nearest to the average job footprint, with stable tie breaks.
    cx=sum((b[0]+b[2])/2 for b in obstacles)/len(obstacles)
    cy=sum((b[1]+b[3])/2 for b in obstacles)/len(obstacles)
    return min(candidates,key=lambda p: ((p[0]-cx)**2+(p[1]-cy)**2,p[0],p[1]))


def add_to_project(project, plate, plan, options, center, bindings):
    """One first-in-order logical object; complementary per-tool/density volumes."""
    old_ids = project._root_ids()
    profiles = project._read_profiles()
    token = hashlib.sha256(json.dumps({'plate':plate,'plan':plan,'options':options.model_dump(mode='json'),
                                      'bindings':bindings},sort_keys=True).encode()).hexdigest()[:32]
    name=SIGNATURE+token
    used={int(e.get('id')) for e in project.model.iter() if e.get('id','').isdigit()}
    used.update(int(e.get('id')) for e in project.settings.iter() if e.get('id','').isdigit())
    used.update(int(e.get('objectid')) for e in project.model.iter() if e.get('objectid','').isdigit())
    oid=max(used or {0})+1
    obj=ET.Element(f'{{{CORE}}}object',id=str(oid),type='model',name=name)
    components=ET.SubElement(obj,f'{{{CORE}}}components')
    meta=ET.Element('object',id=str(oid))
    _set_metadata(meta,'name',name)
    marker={'schema':1,'producer':'custom-tower','role':'synthetic_priming_tower','token':token,
            'bindings':bindings,'options':options.model_dump(mode='json'), 'plate':plate}
    _set_metadata(meta,'slicer_mcp_auxiliary',json.dumps(marker,sort_keys=True,separators=(',',':')))
    base={'enable_support':False,'flush_into_objects':False,'flush_into_support':False,
          'brim_type':'outer_only','brim_width':options.brim_mm,'brim_object_gap':0,
          'wall_loops':1,'bottom_shell_layers':0,'bottom_shell_thickness':0,
          'top_shell_layers':0,'top_shell_thickness':0,'infill_combination':False,
          'sparse_infill_pattern':'rectilinear','seam_slope_type':'none',
          'extruder':options.model_tools[0]+1}
    for key in ['outer_wall_line_width','inner_wall_line_width','sparse_infill_line_width',
                'internal_solid_infill_line_width','initial_layer_line_width']:
        base[key]=options.line_width_mm
    for key in ['outer_wall_speed','inner_wall_speed','sparse_infill_speed','internal_solid_infill_speed',
                'initial_layer_speed','initial_layer_infill_speed','bridge_speed','internal_bridge_speed',
                'gap_infill_speed','small_perimeter_speed']:
        base[key]=[options.speed_mm_s]
    for k,v in base.items(): _set_metadata(meta,k,_native_option_value(k,v))
    for index, ((tool,density),(vertices,faces)) in enumerate(sorted(mesh_groups(plan).items()),1):
        pid=oid+index
        part_obj=ET.SubElement(project._resources(),f'{{{CORE}}}object',id=str(pid),type='model')
        mesh=ET.SubElement(part_obj,f'{{{CORE}}}mesh'); vs=ET.SubElement(mesh,f'{{{CORE}}}vertices'); fs=ET.SubElement(mesh,f'{{{CORE}}}triangles')
        for vertex in vertices:
            ET.SubElement(vs,f'{{{CORE}}}vertex',dict(zip('xyz',(format(v,'.12g') for v in vertex))))
        for face in faces:
            ET.SubElement(fs,f'{{{CORE}}}triangle',dict(zip(('v1','v2','v3'),map(str,face))))
        ET.SubElement(components,f'{{{CORE}}}component',objectid=str(pid))
        part=ET.SubElement(meta,'part',id=str(pid),subtype='normal_part')
        for k,v in {'name':f'T{tool} contour and infill {density}%', 'extruder':tool+1,
                    'sparse_infill_density':f'{density}%', 'wall_loops':1,
                    'top_shell_layers':0,'bottom_shell_layers':0,
                    'top_shell_thickness':0,'bottom_shell_thickness':0}.items():
            _set_metadata(part,k,_native_option_value(k,v))
    project._resources().insert(0,obj); project.settings.insert(0,meta)
    plates=project.list_plates(); origin=project._plate_origin(plate-1,len(plates),project._bed_dimensions())
    transform=IDENTITY[:9]+[center[0]+origin[0],center[1]+origin[1],0.]
    item=ET.Element(f'{{{CORE}}}item',objectid=str(oid),transform=' '.join(format(v,'.12g') for v in transform),printable='1')
    _child(project.model,'build').insert(0,item)
    instance=ET.Element('model_instance')
    for k,v in {'object_id':oid,'instance_id':0,'identify_id':oid}.items(): _set_metadata(instance,k,v)
    target=project._plate(plate); target.insert(len(_children(target,'metadata')),instance)
    project._remap_ranges(old_ids)
    project._write_profiles(profiles)
    project.config.update(enable_prime_tower='0',purge_in_prime_tower='0',print_order='as_obj_list',print_sequence='by layer')
    _set_metadata(target,'enable_prime_tower','0'); _set_metadata(target,'purge_in_prime_tower','0')
    marker['geometry_sha256']=geometry_fingerprint(project,oid)
    _set_metadata(meta,'slicer_mcp_auxiliary',json.dumps(marker,sort_keys=True,separators=(',',':')))
    project.data['Metadata/snapmaker_custom_tower.json']=(json.dumps(marker,sort_keys=True)+'\n').encode()
    return oid,name
