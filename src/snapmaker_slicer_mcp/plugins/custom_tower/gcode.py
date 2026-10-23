"""Commanded deposition and event audit; no G-code rewriting or firmware calls.

Supports modal linear/XY arc motion, absolute/relative E and XYZ, G92, units,
M220/M221 and retraction debt. Opaque firmware macros are reported, not simulated.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from collections import defaultdict

SIGNATURE = '__SYNTH_TOWER_V1__'
PARAM = re.compile(r'([XYZEFIJKRSTP])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))', re.I)
LABEL = re.compile(r'; printing object (.*?) id:(\d+) copy (\d+)')


@dataclass
class Block:
    name: str
    volume_mm3: float = 0.
    features: set[str] = field(default_factory=set)
    bounds: list[float] = field(default_factory=lambda: [math.inf, math.inf, -math.inf, -math.inf])
    top_z: float = -math.inf
    speeds: list[float] = field(default_factory=list)
    model_bounds: list[float] = field(default_factory=lambda: [math.inf, math.inf, -math.inf, -math.inf])
    temperatures: set[tuple] = field(default_factory=set)


@dataclass
class Phase:
    z: float
    tool: int
    changed: bool
    line: int
    blocks: list[Block] = field(default_factory=list)
    volume_mm3: float = 0.


@dataclass
class Layer:
    z: float
    phases: list[Phase] = field(default_factory=list)
    tools: set[int] = field(default_factory=set)


@dataclass
class Trace:
    phases: list[Phase]
    layers: list[Layer]
    empty_selections: list[int]
    macros: list[str]
    temperatures: dict
    auxiliary_on: list[int]
    startup_unlabelled_moves: int
    tower_names: set[str] | None = None

    def is_tower(self, name):
        return name in self.tower_names if self.tower_names is not None else name.startswith(SIGNATURE)

    def schedule(self):
        return {round(layer.z, 6): sorted({p.tool for p in layer.phases
                if any(not self.is_tower(b.name) for b in p.blocks)}) for layer in self.layers}


def trace(text, *, diameters, tower_names=None, brim_boxes=None):
    """Parse native object-labelled deposition. Reject unknown print-time priming."""
    pos = dict(X=0., Y=0., Z=0., E=0.)
    offsets = dict(X=0., Y=0., Z=0.)
    absolute, relative_e, units, absolute_ij = True, False, 1., False
    tool, z, feature, label = None, None, '', None
    debt, flow = defaultdict(float), defaultdict(lambda: 1.)
    phases, layers, empty, macros, auxiliary = [], [], [], set(), []
    temperatures = {'nozzles': defaultdict(set), 'bed': set()}
    feed, speed_factor, started, startup = 0., 1., False, 0
    phase, block, pending = None, None, None
    nozzle_targets, bed_target = {}, None

    def finish_block():
        nonlocal block
        if block and block.volume_mm3:
            if abs(block.top_z - z) > .002:
                raise ValueError('Independent support/ramp heights are outside the shared native layer grid')
        block = None

    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith(';Z:'):
            finish_block()
            z = float(line.split(':', 1)[1])
            if not math.isfinite(z) or z <= 0 or layers and z <= layers[-1].z + 1e-6:
                raise ValueError('Native Z layers must be positive and strictly increasing')
            layers.append(Layer(z)); phase = None; label = None
        if line.startswith((';TYPE:', '; FEATURE:')):
            feature = line.split(':', 1)[1].strip().lower()
        match = LABEL.match(line)
        if match:
            finish_block(); label = match[1]
        elif line.startswith('; stop printing object'):
            finish_block(); label = None
        code = line.split(';', 1)[0].strip()
        if not code:
            continue
        op = code.split()[0].upper()
        values = {k.upper(): float(v) for k, v in PARAM.findall(code[len(op):])}
        if any(not math.isfinite(v) for v in values.values()):
            raise ValueError('Nonfinite G-code parameter')
        if re.fullmatch(r'T\d+', op):
            selected = int(op[1:])
            if not 0 <= selected < len(diameters):
                raise ValueError('Undeclared tool selection')
            if selected != tool:
                finish_block()
                if pending is not None:
                    empty.append(pending)
                pending = number if z is not None else None
                tool = selected; phase = None
            continue
        if op == 'G90': absolute = True
        if op == 'G91': absolute = False
        if op == 'M82': relative_e = False
        if op == 'M83': relative_e = True
        if op == 'G20': units = 25.4
        if op == 'G21': units = 1.
        if op == 'G90.1': absolute_ij = True
        if op == 'G91.1': absolute_ij = False
        if op in {'G18', 'G19', 'M200', 'ACTIVATE_EXTRUDER', 'SET_GCODE_OFFSET','G10','G11'}:
            raise ValueError(f'Unsupported motion/extrusion mode: {op}')
        if op == 'G92':
            for axis in 'XYZ':
                if axis in values: offsets[axis] = pos[axis] - values[axis] * units
            if 'E' in values: pos['E'] = values['E'] * units
        if op == 'M220': speed_factor = values.get('S', 100) / 100
        if op == 'M221': flow[int(values.get('T', tool or 0))] = values.get('S', 100) / 100
        if op in {'M104', 'M109'} and 'S' in values:
            nozzle_targets[int(values.get('T', tool or 0))] = values['S']
        if op in {'M140', 'M190'} and 'S' in values: bed_target = values['S']
        if op == 'M106' and values.get('P') == 2 and values.get('S', 255) > 0:
            auxiliary.append(number)
        if '_' in op:
            macros.add(op)
        if op not in {'G0', 'G1', 'G2', 'G3'}:
            continue
        new = dict(pos)
        for axis in 'XYZ':
            if axis in values:
                new[axis] = values[axis] * units + (offsets[axis] if absolute else pos[axis])
        if 'E' in values:
            new['E'] = values['E'] * units + (pos['E'] if relative_e else 0.)
        de = new['E'] - pos['E']
        feed = values.get('F', feed / units) * units
        bounds = [min(pos['X'], new['X']), min(pos['Y'], new['Y']),
                  max(pos['X'], new['X']), max(pos['Y'], new['Y'])]
        xy = math.hypot(new['X'] - pos['X'], new['Y'] - pos['Y']) > 1e-8
        if op in {'G2', 'G3'}:
            xy = True
            if 'I' in values or 'J' in values:
                cx = (values.get('I', 0.) * units + (offsets['X'] if absolute_ij else pos['X']))
                cy = (values.get('J', 0.) * units + (offsets['Y'] if absolute_ij else pos['Y']))
                radius = math.hypot(pos['X'] - cx, pos['Y'] - cy)
                if radius < 1e-9: raise ValueError('Invalid zero-radius arc')
                bounds = [cx-radius, cy-radius, cx+radius, cy+radius]
            elif 'R' in values:
                radius = abs(values['R'] * units)
                if radius * 2 + 1e-6 < math.hypot(new['X']-pos['X'], new['Y']-pos['Y']):
                    raise ValueError('Invalid R arc')
                bounds = [bounds[0]-2*radius, bounds[1]-2*radius,
                          bounds[2]+2*radius, bounds[3]+2*radius]
            else:
                raise ValueError('Arc without a center/radius')
        if tool is not None and de < 0:
            debt[tool] += -de
        positive = max(0., de)
        if tool is not None:
            repaid = min(debt[tool], positive)
            debt[tool] -= repaid; positive -= repaid
            # Native wipe E segments use five decimal places. Their rounded
            # sum can differ from the single unretract by a few 1e-5 mm.
            # This exemption only repays a real prior retract, never fresh E.
            if not xy and repaid > 0 and positive <= .0001:
                positive = 0.
            if not xy and ('X' in values or 'Y' in values) and positive <= .0001:
                # A tiny native seam segment can collapse to one point when
                # XY is serialized at 0.001 mm and E at 0.00001 mm.
                positive = 0.
        if positive > 1e-8:
            deposition_label=label
            if not deposition_label and feature=='brim' and z is not None and z==layers[0].z:
                # Native first-layer brims precede object comments. Only an
                # explicit spatial match can attribute one to our auxiliary.
                matches=[name for name,box in (brim_boxes or {}).items()
                         if bounds[0]>=box[0] and bounds[1]>=box[1]
                         and bounds[2]<=box[2] and bounds[3]<=box[3]]
                if len(matches)>1: raise ValueError('Ambiguous native brim ownership')
                deposition_label=matches[0] if matches else '__NATIVE_EXTERNAL_BRIM__'
            if not deposition_label:
                if started or z is not None:
                    raise ValueError('Unlabelled/unclassified print-time preparation extrusion')
                startup += 1
            elif not xy:
                raise ValueError('Unclassified E-only preparation inside an object')
            else:
                if not feature or feature=='custom':
                    raise ValueError('Unclassified native extrusion feature during printing')
                if tool is None or z is None:
                    raise ValueError('Deposition without a declared tool/layer')
                started = True
                if phase is None:
                    phase = Phase(z, tool, pending is not None, number)
                    phases.append(phase); layers[-1].phases.append(phase)
                pending = None
                if block is not None and block.name!=deposition_label: finish_block()
                if block is None:
                    block = Block(deposition_label); phase.blocks.append(block)
                volume = positive * math.pi * (float(diameters[tool])/2)**2 * flow[tool]
                block.volume_mm3 += volume; phase.volume_mm3 += volume
                block.features.add(feature); block.top_z = max(block.top_z, new['Z'], pos['Z'])
                block.temperatures.add((nozzle_targets.get(tool),bed_target))
                block.speeds.append(feed / 60 * speed_factor)
                block.bounds = [min(block.bounds[0], bounds[0]), min(block.bounds[1], bounds[1]),
                                max(block.bounds[2], bounds[2]), max(block.bounds[3], bounds[3])]
                if not feature.startswith('support') and feature != 'custom':
                    block.model_bounds = [min(block.model_bounds[0], bounds[0]), min(block.model_bounds[1], bounds[1]),
                                          max(block.model_bounds[2], bounds[2]), max(block.model_bounds[3], bounds[3])]
                layers[-1].tools.add(tool)
                if tool in nozzle_targets: temperatures['nozzles'][tool].add(nozzle_targets[tool])
                if bed_target is not None: temperatures['bed'].add(bed_target)
        pos = new
    finish_block()
    # Shutdown selections after the last deposition are not print phases.
    if not phases:
        raise ValueError('No labelled native deposition')
    temperatures['nozzles'] = {str(k): sorted(v) for k, v in temperatures['nozzles'].items()}
    temperatures['bed'] = sorted(temperatures['bed'])
    return Trace(phases, [l for l in layers if l.phases], empty, sorted(macros), temperatures,
                 auxiliary, startup, tower_names)


def audit_tower(result, *, targets):
    failures, checked, structural = set(), 0, 0
    for phase in result.phases:
        seen_model = False; tower = []
        for block in phase.blocks:
            if result.is_tower(block.name):
                if seen_model: failures.add('tower_return_after_model')
                tower.append(block)
            else:
                seen_model = True
                if phase.changed and not tower: failures.add('model_before_tower')
        if phase.changed:
            checked += 1
            if not tower: failures.add('missing_tower_after_change')
            if sum(b.volume_mm3 for b in tower) + 1e-5 < targets.get(phase.tool, 36):
                failures.add('insufficient_deposition_volume')
            features = set().union(*(b.features for b in tower))
            if not any('wall' in f or 'perimeter' in f for f in features):
                failures.add('missing_tower_contour')
            if not any('infill' in f or 'solid' in f or 'bridge' in f for f in features):
                failures.add('missing_tower_infill')
        elif tower:
            structural += 1
    if result.empty_selections: failures.add('empty_selection')
    if result.auxiliary_on: failures.add('auxiliary_fan_enabled')
    return {'passed': not failures, 'failures': sorted(failures), 'changes_checked': checked,
            'structural_visits_without_change': structural,
            'opaque_macros': result.macros, 'startup_unlabelled_moves': result.startup_unlabelled_moves,
            'physical_cleaning_validated': False}
