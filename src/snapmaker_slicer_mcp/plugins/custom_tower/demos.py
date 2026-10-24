"""Verify an existing authoritative assembly; never invent placement or resize CAD."""
from collections import Counter
import hashlib
import json
import math
import posixpath

from ...project import PRODUCTION, _children, _child, _read_xml, _transform, compose_transform
from .receipts import canonical
from .archive import signed_towers


def leaf_signatures(project, instances):
    trees={project._model_name:project.model}
    leaves=[]
    def descend(path,oid,transform,ancestors):
        key=(path,oid)
        if key in ancestors or len(ancestors)>64: raise ValueError('Cyclic/deep demo component hierarchy')
        if path not in trees: trees[path]=_read_xml(project.data[path])[0]
        obj=next((e for e in trees[path].findall('.//{*}resources/{*}object') if e.get('id')==str(oid)),None)
        if obj is None: raise ValueError('Demo references a missing component')
        mesh=_child(obj,'mesh')
        if mesh is not None:
            matrix=transform[:9]
            gram=[[sum(matrix[i*3+k]*matrix[j*3+k] for k in range(3)) for j in range(3)] for i in range(3)]
            scale=math.sqrt(sum(gram[i][i] for i in range(3))/3)
            if scale<=0 or any(abs(gram[i][j]-(scale*scale if i==j else 0))>1e-5 for i in range(3) for j in range(3)):
                raise ValueError('Nonuniform demo transforms require an explicit CAD assembly review')
            digest=hashlib.sha256(json.dumps(canonical(mesh),sort_keys=True).encode()).hexdigest()
            leaves.append((digest,round(scale,6)))
        settings=project._object_settings(oid) if path==project._model_name else None
        ignored={p.get('id') for p in _children(settings,'part')
                 if p.get('subtype','normal_part') not in {'normal_part','negative_part'}} if settings is not None else set()
        for component in obj.findall('{*}components/{*}component'):
            if component.get('objectid') in ignored: continue
            target=component.get(f'{{{PRODUCTION}}}path',path)
            target=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(path),target)) if target!=path else path
            descend(target,int(component.get('objectid')),
                    compose_transform(_transform(component.get('transform')),transform),ancestors|{key})
    for oid,index in instances:
        items=project._items(oid)
        if not 0<=index<len(items): raise ValueError('Unknown demo/source instance')
        descend(project._model_name,oid,_transform(items[index].get('transform')),set())
    return Counter(leaves)


def validate_demo(project,ids,source_plate):
    plates={p['plate']:p for p in project.list_plates()}
    if source_plate not in plates or source_plate==1:
        raise ValueError('Declare the largest production source plate for the demo')
    if 1 not in plates: raise ValueError('Demos must be on plate 1')
    owned=set(signed_towers(project))
    source=[(i['object_id'],i['instance_id']) for i in plates[source_plate]['objects'] if i['object_id'] not in owned]
    demo=[(i['object_id'],i['instance_id']) for i in plates[1]['objects'] if i['object_id'] in ids]
    if {oid for oid,_ in demo}!=set(ids): raise ValueError('Declared demos must already belong to plate 1')
    expected=leaf_signatures(project,source); actual=leaf_signatures(project,demo)
    if not expected or actual!=expected:
        raise ValueError('Demo must use identical source part geometry, painting and physical scale')
    topology=Counter({h:sum(n for (digest,_),n in expected.items() if digest==h) for h,_ in expected})
    max_scale=max(s for _,s in expected)
    for number,plate in plates.items():
        if number in (1,source_plate): continue
        signatures=leaf_signatures(project,[(i['object_id'],i['instance_id']) for i in plate['objects'] if i['object_id'] not in owned])
        other=Counter({h:sum(n for (digest,_),n in signatures.items() if digest==h) for h,_ in signatures})
        if topology==other and max((s for _,s in signatures),default=0)>max_scale+1e-6:
            raise ValueError('Demo reference is not the largest instance of these source meshes')
    return {'plate':1,'source_plate':source_plate,'identical_meshes_and_scale':True,
            'assembly_placement':'existing authoritative assembly preserved; not inferred by the plugin'}
