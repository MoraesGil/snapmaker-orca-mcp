"""Content-bound validation receipts, independent of other signed auxiliaries."""
import copy
import hashlib
import json

from ...project import _metadata, _set_metadata, _local, _children, CONFIG, SETTINGS, PARKED
from .archive import signed_towers, remove_signed, geometry_fingerprint

SEQUENCE=('first_layer_print_sequence','other_layers_print_sequence','other_layers_print_sequence_nums')


def canonical(node):
    return [node.tag, sorted(node.attrib.items()), (node.text or '').strip(),
            [canonical(e) for e in node]]


def restore_sequence(project, plate, marker):
    node=project._plate(plate)
    owned=marker.get('owned_sequence')
    if owned and all(_metadata(node).get(k)==v for k,v in owned.items()):
        for k,v in marker.get('previous_sequence',{}).items(): _set_metadata(node,k,v)


def external_fingerprint(project):
    p=copy.deepcopy(project)
    ids=signed_towers(p)
    for oid in ids:
        marker=json.loads(_metadata(p._object_settings(oid))['slicer_mcp_auxiliary'])
        for plate in p.list_plates():
            if any(i['object_id']==oid for i in plate['objects']): restore_sequence(p,plate['plate'],marker)
    for plate in p.list_plates(): remove_signed(p,plate['plate'])
    cfg={k:v for k,v in p.config.items() if k not in {'enable_prime_tower','purge_in_prime_tower'}}
    for plate in _children(p.settings,'plate'):
        for key in ('enable_prime_tower','purge_in_prime_tower'): _set_metadata(plate,key,None)
    # Embedded slices/previews are outputs. Empty parking records created by a
    # removal are not an external print input; live memberships remain in XML.
    payloads={name:hashlib.sha256(data).hexdigest() for name,data in p.data.items()
              if name not in {p._model_name,CONFIG,SETTINGS,PARKED,'Metadata/snapmaker_custom_tower.json'}
              and not name.startswith('Metadata/plate_') and not name.endswith(('.gcode','.gcode.md5'))
              and name!='Metadata/slice_info.config'}
    value={'model':canonical(p.model),'settings':canonical(p.settings),'config':cfg,'payloads':payloads}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def tower_fingerprint(project, oid, plate):
    value={'geometry':geometry_fingerprint(project,oid),
           'items':[canonical(e) for e in project._items(oid)],
           'sequence':{k:_metadata(project._plate(plate)).get(k) for k in SEQUENCE}}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def check_receipts(project,state,target,engine):
    ids=set(signed_towers(project))
    if not ids: return
    external=external_fingerprint(project)
    receipts=state.get('custom_towers',{})
    for plate in project.list_plates():
        if target and plate['plate']!=target: continue
        owned=ids & {i['object_id'] for i in plate['objects']}
        if not owned: continue
        receipt=receipts.get(str(plate['plate']),{})
        if (receipt.get('external_fingerprint')!=external or receipt.get('engine')!=engine
                or receipt.get('towers')!={str(oid):tower_fingerprint(project,oid,plate['plate']) for oid in owned}
                or str(project.config.get('enable_prime_tower','0')) not in {'0','False','false'}
                or _metadata(project._plate(plate['plate'])).get('enable_prime_tower','0')!='0'):
            raise ValueError('Custom tower needs recalculation with add_custom_tower before slicing')
