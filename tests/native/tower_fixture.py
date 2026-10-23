"""Small public nested-material model; no private mesh or profile payload."""
import json
from xml.etree import ElementTree as ET
import zipfile

from snapmaker_slicer_mcp.project import CORE, _set_metadata, _xml_bytes
from snapmaker_slicer_mcp.presets import normalize_filament_profile
from snapmaker_slicer_mcp.plugins.custom_tower.geometry import slab_mesh
from .harness import official_config


def tower_fixture(path,engine,scratch,model_tools=(0,),interface=1,inverse=False):
    from snapmaker_slicer_mcp.presets import Catalog
    count=max(*model_tools,interface)+1
    config=official_config(engine,scratch)
    catalog=Catalog(engine,[],scratch/'drafts')
    profile=normalize_filament_profile(catalog.get_preset_config('filament','Generic PLA')['config'])
    for key,value in profile.items():
        if isinstance(value,list) and len(value)==1 and key not in {'compatible_printers','compatible_prints'}:
            config[key]=value*count
    config.update(filament_type=['PETG' if inverse else 'PLA']*count,
        filament_colour=['#888888','#ffcc00','#5588cc','#aa55cc'][:count],
        filament_diameter=['1.75']*count,filament_settings_id=['Synthetic']*count,
        filament_flow_step_size=['1']*count,filament_flow_support=['standard']*count,
        filament_volume_type=['standard']*count,additional_cooling_fan_speed=['0']*count,
        nozzle_temperature=['240' if inverse else '230']*count,
        nozzle_temperature_initial_layer=['240' if inverse else '230']*count,
        cool_plate_temp=['65' if inverse else '50']*count,
        cool_plate_temp_initial_layer=['65' if inverse else '50']*count,
        filament_map=[str(t) for t in range(count)],
        flush_volumes_matrix=['0']*(count*count),enable_prime_tower='1',
        layer_height='.2',initial_layer_print_height='.2',enable_support='0',
        seam_slope_type='none',print_order='as_obj_list',
        filament_max_volumetric_speed=['12']*count,reduce_crossing_wall='0')
    config['filament_type'][interface]='PLA' if inverse else 'PETG'
    config['nozzle_temperature'][interface]=config['nozzle_temperature_initial_layer'][interface]='230' if inverse else '240'
    root=ET.Element(f'{{{CORE}}}model',unit='millimeter')
    ET.SubElement(root,f'{{{CORE}}}metadata',name='Application').text='Snapmaker_Orca-2.4.1'
    ET.SubElement(root,f'{{{CORE}}}metadata',name='Snapmaker_Orca:3mfVersion').text='1'
    resources=ET.SubElement(root,f'{{{CORE}}}resources')
    obj=ET.SubElement(resources,f'{{{CORE}}}object',id='1',type='model')
    components=ET.SubElement(obj,f'{{{CORE}}}components')
    settings=ET.Element('config'); meta=ET.SubElement(settings,'object',id='1')
    _set_metadata(meta,'name','Synthetic tapered model')
    _set_metadata(meta,'extruder',model_tools[0]+1)
    grouped={}
    for i in range(90):
        bottom,top=i*.2,(i+1)*.2; width=44-i*.25
        core=.0 if i<2 else .12
        inner=1.
        for j,tool in enumerate(model_tools):
            next_inner=(1-(1-core)*(j+1)/len(model_tools))**.5 if j<len(model_tools)-1 else core**.5
            grouped.setdefault(tool,[]).append(slab_mesh(width,next_inner,inner,bottom,top)); inner=next_inner
        if core:
            grouped.setdefault(interface,[]).append(slab_mesh(width,0,core**.5,bottom,top))
    for tool,chunks in grouped.items():
        pid=tool+10; part_obj=ET.SubElement(resources,f'{{{CORE}}}object',id=str(pid),type='model')
        mesh=ET.SubElement(part_obj,f'{{{CORE}}}mesh'); vs=ET.SubElement(mesh,f'{{{CORE}}}vertices'); fs=ET.SubElement(mesh,f'{{{CORE}}}triangles')
        offset=0
        for vertices,faces in chunks:
            for v in vertices: ET.SubElement(vs,f'{{{CORE}}}vertex',dict(zip('xyz',map(str,v))))
            for f in faces: ET.SubElement(fs,f'{{{CORE}}}triangle',dict(zip(('v1','v2','v3'),(str(v+offset) for v in f))))
            offset+=len(vertices)
        ET.SubElement(components,f'{{{CORE}}}component',objectid=str(pid))
        part=ET.SubElement(meta,'part',id=str(pid),subtype='normal_part')
        _set_metadata(part,'name',f'Synthetic T{tool}')
        _set_metadata(part,'extruder',tool+1)
    ET.SubElement(ET.SubElement(root,f'{{{CORE}}}build'),f'{{{CORE}}}item',objectid='1',transform='1 0 0 0 1 0 0 0 1 130 130 0')
    plate=ET.SubElement(settings,'plate'); _set_metadata(plate,'plater_id',1)
    inst=ET.SubElement(plate,'model_instance')
    for k,v in {'object_id':1,'instance_id':0,'identify_id':1}.items(): _set_metadata(inst,k,v)
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        archive.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        archive.writestr('3D/3dmodel.model',_xml_bytes(root,[('',CORE)]))
        archive.writestr('Metadata/model_settings.config',ET.tostring(settings))
        archive.writestr('Metadata/project_settings.config',json.dumps(config))
    return path
