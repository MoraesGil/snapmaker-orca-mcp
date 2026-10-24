"""Native prepass -> signed geometry -> native slice -> numerical gates -> CAS."""
from __future__ import annotations

import copy
import json
import math
import uuid
from pathlib import Path

from ...engine import file_hash
from ...project import Project, _children, _metadata, _set_metadata
from ...service import write_json, validate_project
from .contracts import TowerOptions
from .archive import signed_towers, remove_signed, normalize_demo, place, add_to_project
from .geometry import make_plan, profile
from .gcode import trace, audit_tower, Footprint
from .hybrid import candidate_order
from .receipts import external_fingerprint, tower_fingerprint, check_receipts
from .materials import main_material_bed, check_temperatures
from .demos import validate_demo

ENGINE_COMMIT = '9bf2f18eaff7cc0655cbdeef6a215660085ecb0f'
SEQUENCE = ('first_layer_print_sequence','other_layers_print_sequence','other_layers_print_sequence_nums')


def values(value):
    return value if isinstance(value,list) else str(value).split(',')


def read_trace(path, project, names=(), brim_boxes=None):
    diameters=[float(x) for x in values(project.config.get('filament_diameter', []))]
    if not diameters or any(not math.isfinite(v) or v<=0 for v in diameters):
        raise ValueError('Explicit filament diameters are required')
    result=trace(Path(path).read_text(),diameters=diameters,tower_names=set(names),brim_boxes=brim_boxes)
    if result.fallback_width_depositions:
        raise ValueError('Native commanded WIDTH is required for deposition occupancy; synthetic fallback is not native evidence')
    return result


def set_sequence(project, plate, result):
    """Explicit per-layer native orders. Final phases must agree in full."""
    node=project._plate(plate); previous={k:_metadata(node).get(k) for k in SEQUENCE}
    flat=[]
    for index,layer in enumerate(result.layers,1):
        tools=[p.tool for p in layer.phases]
        if len(tools)!=len(set(tools)): raise ValueError('Repeated tool phase at the same Z')
        order=tools+[t for t in range(4) if t not in tools]
        if index==1: _set_metadata(node,SEQUENCE[0],' '.join(str(t+1) for t in order))
        else: flat.extend([index,index,*[t+1 for t in order]])
    _set_metadata(node,SEQUENCE[1],' '.join(map(str,flat)))
    _set_metadata(node,SEQUENCE[2],len(result.layers)-1)
    return previous


def phase_signature(result):
    return [(round(p.z,6),p.tool,p.changed) for p in result.phases]


def temperature_signature(result):
    return {(round(p.z,6),p.tool): set().union(*(b.temperatures for b in p.blocks
             if not result.is_tower(b.name))) for p in result.phases}


def audit_candidate(before, after, plan, options, *, initial_speed_limit=None):
    audit=audit_tower(after,targets={t:options.target(t) for t in options.model_tools+[options.interface_tool]})
    failures=set(audit['failures'])
    if before.schedule()!=after.schedule(): failures.add('external_material_schedule_changed')
    if phase_signature(before)!=phase_signature(after): failures.add('native_phases_changed')
    if temperature_signature(before)!=temperature_signature(after): failures.add('deposition_temperature_changed')
    rows={round(r['z'],6):r for r in plan['rows']}
    structural=[]
    for phase in after.phases:
        tower=[b for b in phase.blocks if after.is_tower(b.name)]
        if not tower: continue
        row=rows.get(round(phase.z,6))
        if row is None or phase.tool not in before.schedule().get(round(phase.z,6),[]):
            failures.add('unscheduled_tower_material'); continue
        region=next(r for r in row['regions'] if r['tool']==phase.tool)
        if phase.changed != (region['purpose']=='prime_after_change'):
            failures.add('preparation_not_bound_to_change')
        if not phase.changed:
            if region['target_mm3']!=0 or region['density']!=options.structural_infill_percent:
                failures.add('preparation_before_change')
            structural.append({'z':phase.z,'tool':phase.tool,'density_percent':region['density'],
                               'measured_mm3':sum(b.volume_mm3 for b in tower),'purge_target_mm3':0})
        limit=options.speed_mm_s
        if phase.z==after.layers[0].z and initial_speed_limit is not None:
            limit=initial_speed_limit
        if any(v>limit+.05 for b in tower for v in b.speeds):
            failures.add('tower_speed_exceeded')
    # Sparse commanded bead occupancy includes supports and unlabelled brims.
    # Aggregate rectangles cannot establish a conflict. Shared occupied strips
    # are a conservative conflict, not proof of a physical collision.
    for layer in after.layers:
        tower=[b for p in layer.phases for b in p.blocks if after.is_tower(b.name)]
        objects=[b for p in layer.phases for b in p.blocks if not after.is_tower(b.name)]
        for a in tower:
            for b in objects:
                if (a.volume_mm3>0 and not a.footprint.rows
                        or b.volume_mm3>0 and not b.footprint.rows):
                    failures.add('missing_native_deposition_occupancy')
                    continue
                # Footprints already include each emitted ;WIDTH and arc
                # approximation margin, unlike the centerline block bounds.
                if a.footprint.intersects(b.footprint):
                    failures.add('native_deposition_footprint_conflict')
    return {**audit,'passed':not failures,'failures':sorted(failures),
            'structural_passes':structural,'temperatures':after.temperatures,
            'occupancy':{'conservative':True,'strip_height_mm':Footprint.pitch,
                         'arc_sagitta_margin_mm':Footprint.sagitta,
                         'fallback_width_depositions':after.fallback_width_depositions},
            'external_schedule':after.schedule()}


class CustomTowerPlugin:
    def __init__(self, editor, slicer):
        self.editor,self.slicer=editor,slicer
        self.root=slicer.root/'custom-towers'; self.root.mkdir(exist_ok=True,mode=0o700)

    def status(self, project_id):
        with self.editor._lock:
            state=self.editor._state(project_id)
            receipt=state.get('custom_towers',{})
            project=Project(Path(state['path'])); owned=signed_towers(project)
            stale=False
            try: check_receipts(project,state,0,self.slicer.engine.identity())
            except ValueError: stale=True
        return {'project_id':project_id,'sha256':state['sha256'],'tower_object_ids':owned,
                'requires_recalculation':stale,
                'receipt':receipt,'physical_cleaning_validated':False}

    def add(self, project_id, plate, options, expected_sha256):
        options=TowerOptions.model_validate(options)
        if type(plate) is not int or plate<1: raise ValueError('Choose one existing plate (1-based)')
        if self.slicer.engine.identity()['source_commit']!=ENGINE_COMMIT:
            raise ValueError('Tower sequence backend requires the explicitly validated official engine commit')
        with self.editor._lock:
            state=copy.deepcopy(self.editor._state(project_id))
            if state['sha256']!=expected_sha256: raise ValueError('Stale project revision')
            source=Path(state['path']); project=Project(source)
            if project.model.get('unit','millimeter')!='millimeter':
                raise ValueError('Custom tower backend requires millimeter model units')
            tags={e.get('name'):e.text for e in _children(project.model,'metadata')}
            if tags.get('Snapmaker_Orca:3mfVersion')!='1':
                raise ValueError('Save a complete Snapmaker Orca project first; plain mesh import may rearrange geometry')
            members={m['object_id'] for m in project.list_plates() if m['plate']==plate for m in m['objects']}
            if options.primary_object_id not in members or options.primary_object_id in signed_towers(project):
                raise ValueError('primary_object_id must identify a real model on the target plate')
            if options.demo_object_ids and plate==1:
                raise ValueError('Plate 1 is an assembled preview; choose a production plate')
            primary=next(o for o in project.list_objects() if o['object_id']==options.primary_object_id)
            names=[o['name'] for o in project.list_objects() if o['object_id'] in members]
            if names.count(primary['name'])!=1 or len(primary['instances'])!=1 or not primary['instances'][0]['printable']:
                raise ValueError('Primary model needs a unique name and a single enabled instance')
            material=values(project.config.get('filament_type',[]))
            if not 1<=len(material)<=4 or any(t>=len(material) for t in options.model_tools+[options.interface_tool]):
                raise ValueError('Declare up to four available physical material slots')
            def family(t):
                text=str(material[t]).upper().strip()
                return 'PLA' if text.startswith('PLA') else 'PETG' if text.startswith('PETG') else text
            if len({family(t) for t in options.model_tools})!=1 or family(options.interface_tool)==family(options.model_tools[0]):
                raise ValueError('Model colours must share a material family, distinct from the declared interface family')
            if any(str(v)!='standard' for v in values(project.config.get('filament_volume_type',['standard']))):
                raise ValueError('Nonstandard/mixed flow modes need a separately validated tower backend')
            demo_validation=validate_demo(project,options.demo_object_ids,options.demo_source_plate) if options.demo_object_ids else None
            normalize_demo(project,options.demo_object_ids)
            # Restore only a sequence still owned by this plugin, never a later user edit.
            for oid in signed_towers(project):
                if oid not in members: continue
                marker=json.loads(_metadata(project._object_settings(oid))['slicer_mcp_auxiliary'])
                node=project._plate(plate)
                if all(_metadata(node).get(k)==v for k,v in marker.get('owned_sequence',{}).items()):
                    for k,v in marker.get('previous_sequence',{}).items(): _set_metadata(node,k,v)
            removed=remove_signed(project,plate)
            for legacy_id in options.legacy_tower_ids:
                if legacy_id not in members or legacy_id in removed:
                    raise ValueError('Explicit legacy tower IDs must belong to this production plate')
                obj=next(o for o in project.list_objects() if o['object_id']==legacy_id)
                if any(i['plate']!=plate for i in obj['instances']):
                    raise ValueError('Legacy auxiliary shared with another plate cannot be removed safely')
                project.delete_object(legacy_id); removed.append(legacy_id)
            project.data.pop('Metadata/snapmaker_custom_tower.json',None)
            project.config.update(enable_prime_tower='0',purge_in_prime_tower='0')
            for k in ('enable_prime_tower','purge_in_prime_tower'): _set_metadata(project._plate(plate),k,'0')
            bed_changes=main_material_bed(project,options,plate)
        run_id=uuid.uuid4().hex; run=self.root/run_id; run.mkdir(mode=0o700)
        prepass=run/'prepass.3mf'; project.save(prepass); prepass.chmod(0o600)
        bindings={'source_revision_sha256':state['sha256'],'prepass_3mf_sha256':file_hash(prepass),
                  'engine':self.slicer.engine.identity()}
        baseline_job=self.slicer.slice_3mf(str(prepass),plate,force=True)
        gcode=Path(baseline_job['plates'][0]['gcode_path'])
        bindings['prepass_gcode_sha256']=file_hash(gcode)
        baseline=read_trace(gcode,project)
        temperature_failures=check_temperatures(baseline,project,options)
        if temperature_failures:
            raise ValueError('Native prepass fails project temperature/fan checks: '+','.join(temperature_failures))
        candidates,advice=candidate_order(options.hybrid)
        attempts=[]; final=None
        for iteration in range(options.max_iterations):
            candidate=Project(prepass)
            margin=candidates[min(iteration,len(candidates)-1)]['density_margin_percent']
            plan=make_plan(baseline,options,primary['name'],density_boost=margin)
            center=place(plan,baseline,options,candidate.config.get('printable_area',[]))
            oid,label=add_to_project(candidate,plate,plan,options,center,bindings)
            previous=set_sequence(candidate,plate,baseline)
            node=candidate._object_settings(oid)
            marker=json.loads(_metadata(node)['slicer_mcp_auxiliary'])
            marker.update(previous_sequence=previous,owned_sequence={k:_metadata(candidate._plate(plate)).get(k) for k in SEQUENCE})
            _set_metadata(node,'slicer_mcp_auxiliary',json.dumps(marker,sort_keys=True,separators=(',',':')))
            output=run/f'candidate-{iteration+1}.3mf'; candidate.save(output); output.chmod(0o600)
            validate_project(output)
            saved_sha=file_hash(output)
            job=self.slicer.slice_3mf(str(output),plate,force=True)
            if job['source']['sha256']!=saved_sha:
                raise ValueError('Candidate changed during native snapshot')
            generated=Path(job['plates'][0]['gcode_path'])
            outline=profile(options.maximum_width_mm); padding=options.brim_mm+options.line_width_mm
            brim_box=[center[0]+min(x for x,y in outline)-padding,center[1]+min(y for x,y in outline)-padding,
                      center[0]+max(x for x,y in outline)+padding,center[1]+max(y for x,y in outline)+padding]
            after=read_trace(generated,candidate,[label],{label:brim_box})
            initial_limit=max(float(values(candidate.config.get(k,[options.speed_mm_s]))[0])
                              for k in ('initial_layer_speed','initial_layer_infill_speed'))
            audit=audit_candidate(baseline,after,plan,options,initial_speed_limit=initial_limit)
            extra=check_temperatures(after,candidate,options)
            if extra:
                audit['failures']=sorted(set(audit['failures'])|set(extra)); audit['passed']=False
            attempt={'iteration':iteration+1,'source_sha256':job['source']['sha256'],
                     'gcode_sha256':file_hash(generated),'audit':audit,'job_id':job['job_id']}
            attempts.append(attempt)
            write_json(run/f'audit-{iteration+1}.json',attempt)
            write_json(run/f'plan-{iteration+1}.json',plan)
            if audit['passed']:
                final=(output,job,oid,label,plan,audit); break
            # Only density shortfalls are currently auto-corrected. Never fix
            # order, supports or temperature by silently changing model settings.
            if set(audit['failures'])-{'insufficient_deposition_volume'}: break
        if final is None:
            report={'status':'rejected','project_id':project_id,'revision_unchanged':True,
                    'attempts':attempts,'advice':advice,'artifact_path':str(run)}
            write_json(run/'result.json',report)
            return report
        output,job,oid,label,plan,audit=final
        if not self.slicer._intact(job):
            raise ValueError('Audited native artifacts changed before publication')
        payload=Path(job['source']['path']).read_bytes()
        import hashlib
        if hashlib.sha256(payload).hexdigest()!=job['source']['sha256']:
            raise ValueError('Audited native snapshot changed before publication')
        validated_project=Project(Path(job['source']['path']))
        receipt={'run_id':run_id,'plate':plate,'engine':self.slicer.engine.identity(),
                 'external_fingerprint':external_fingerprint(validated_project),
                 'towers':{str(oid):tower_fingerprint(validated_project,oid,plate)}}
        with self.editor._lock:
            current=self.editor._state(project_id)
            if current['sha256']!=state['sha256'] or file_hash(source)!=state['sha256']:
                raise ValueError('Project changed during tower calculation; validated candidate was not applied')
            self.slicer.engine.verify()
            destination=self.editor.root/project_id/(uuid.uuid4().hex+'.3mf')
            # Reuse the exact validated bytes, no re-serialization after audit.
            with destination.open('xb') as handle: handle.write(payload)
            destination.chmod(0o600)
            receipts=current.get('custom_towers',{}); receipts[str(plate)]=receipt
            current.pop('custom_tower',None)
            current.update(path=str(destination),sha256=file_hash(destination),revision=current['revision']+1,
                           custom_towers=receipts)
            write_json(self.editor.root/project_id/'state.json',current)
        report={'status':'digitally_validated','project':current,'tower_object_id':oid,
                'tower_name':label,'replaced_tower_ids':removed,'bindings':bindings,'slice':job,
                'audit':audit,'advice':advice,'artifact_path':str(run),
                'project_local_bed_changes':bed_changes,
                'demo_validation':demo_validation,
                'geometry':{k:v for k,v in plan.items() if k!='rows'},
                'physical_cleaning_validated':False}
        write_json(run/'result.json',report)
        return report
