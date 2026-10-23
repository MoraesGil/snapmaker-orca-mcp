"""Project-local bed priority and actual deposition temperature checks."""
import math


def array(value):
    return value if isinstance(value,list) else str(value).split(',')


def main_material_bed(project,options,plate=None):
    # Other plates need their own verified key mapping before this backend
    # changes any bed values. Never change the selected plate type implicitly.
    if project.config.get('curr_bed_type')!='Cool Plate':
        raise ValueError('Custom tower backend currently requires an explicit Cool Plate project')
    count=len(array(project.config.get('filament_type',[])))
    changes={}
    for key in ('cool_plate_temp','cool_plate_temp_initial_layer'):
        source=array(project.config.get(key,[]))
        if len(source)!=count: raise ValueError('Explicit per-tool Cool Plate temperature vectors are required')
        main={float(source[t]) for t in options.model_tools}
        if len(main)!=1 or not all(math.isfinite(v) and 0<v<=150 for v in main):
            raise ValueError('Model colours need one compatible bed target per print stage')
        target=source[options.model_tools[0]]
        updated=source.copy(); updated[options.interface_tool]=target
        if updated!=source: changes[key]={'before':source,'after':updated}
    if changes:
        others=[p for p in project.list_plates() if p['plate']!=plate
                and not (p['plate']==1 and options.demo_object_ids)]
        if others and (plate is not None or len(project.list_plates())>1):
            raise ValueError('Bed vectors are project-wide; cannot alter another production plate implicitly')
        for key,delta in changes.items(): project.config[key]=delta['after']
    return changes


def material_value(config,key,tool):
    from ...presets import _schema_doc
    count=len(array(config['filament_type']))
    values=array(config.get(key,[]))
    if key in _schema_doc()['_meta']['flow_variant_keys']['filament']:
        steps=[int(v) for v in array(config.get('filament_flow_step_size',['1']*count))]
        if len(steps)!=count or any(s<1 for s in steps) or len(values)!=sum(steps):
            raise ValueError(f'Ambiguous filament flow layout for {key}')
        support=array(config.get('filament_flow_support',['standard']*sum(steps)))
        if len(support)!=sum(steps): raise ValueError('Ambiguous flow mode declarations')
        offset=sum(steps[:tool]); segment=support[offset:offset+steps[tool]]
        if 'standard' not in segment: raise ValueError('Standard flow mode is not declared for this tool')
        return float(values[offset+segment.index('standard')])
    if len(values)!=count: raise ValueError(f'Explicit per-tool values required for {key}')
    return float(values[tool])


def check_temperatures(result,project,options):
    failures=set()
    for phase in result.phases:
        first=phase.z==result.layers[0].z
        nozzle=material_value(project.config,'nozzle_temperature_initial_layer' if first else 'nozzle_temperature',phase.tool)
        bed=material_value(project.config,'cool_plate_temp_initial_layer' if first else 'cool_plate_temp',options.model_tools[0])
        expected=(nozzle,bed)
        for block in phase.blocks:
            if block.temperatures!={expected}: failures.add('deposition_target_differs_from_project')
    if result.auxiliary_on: failures.add('auxiliary_fan_enabled')
    return sorted(failures)
