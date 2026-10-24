"""Commanded deposition and event audit; no G-code rewriting or firmware calls.

Supports modal linear/XY arc motion, absolute/relative E and XYZ, G92, units,
M220/M221 and retraction debt. Opaque firmware macros are reported, not simulated.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from collections import defaultdict
from bisect import bisect_left

SIGNATURE = '__SYNTH_TOWER_V1__'
PARAM = re.compile(r'([XYZEFIJKRSTP])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))', re.I)
LABEL = re.compile(r'; printing object (.*?) id:(\d+) copy (\d+)')


def _arc_path(start, end, values, *, clockwise, absolute_ij, offsets, units):
    """Resolve the same native XY arc for bounds and commanded occupancy."""
    sx, sy, ex, ey = start['X'], start['Y'], end['X'], end['Y']
    dx, dy = ex-sx, ey-sy
    chord = math.hypot(dx, dy)
    ij = 'I' in values or 'J' in values
    if ij and 'R' in values:
        raise ValueError('Ambiguous arc with both I/J and R')
    if ij:
        if absolute_ij and not {'I', 'J'} <= values.keys():
            raise ValueError('An absolute arc center requires both I and J')
        cx = values.get('I', 0.)*units + (offsets['X'] if absolute_ij else sx)
        cy = values.get('J', 0.)*units + (offsets['Y'] if absolute_ij else sy)
        radius = math.hypot(sx-cx, sy-cy)
        if radius < 1e-9:
            raise ValueError('Invalid zero-radius arc')
        end_radius = math.hypot(ex-cx, ey-cy)
        # Native XY/IJ serialization at 0.001 mm can leave a few microns
        # of radial mismatch. This is an absolute bound, not a radius ratio.
        if end_radius < 1e-9 or abs(radius-end_radius) > .003:
            raise ValueError('Inconsistent I/J arc endpoint radius')
        full_circle = chord < 1e-9
    elif 'R' in values:
        radius = abs(values['R']*units)
        if radius < 1e-9:
            raise ValueError('Invalid zero-radius arc')
        if chord < 1e-9:
            raise ValueError('Ambiguous R arc with coincident endpoints')
        half = chord/2
        if half > radius + max(1e-12, radius*1e-12):
            raise ValueError('Invalid R arc: chord exceeds its diameter')
        height = math.sqrt(max(0., (radius-half)*(radius+half)))
        side = -1 if clockwise else 1
        if values['R'] < 0:
            side = -side
        cx, cy = (sx+ex)/2 - side*dy/chord*height, (sy+ey)/2 + side*dx/chord*height
        radius = math.hypot(sx-cx, sy-cy)
        full_circle = False
    else:
        raise ValueError('Arc without a center/radius')
    first = math.atan2(sy-cy, sx-cx)
    last = math.atan2(ey-cy, ex-cx)
    sweep = ((first-last) if clockwise else (last-first)) % math.tau
    if not full_circle and sweep == 0:
        raise ValueError('Arc sweep is below coordinate precision')
    return cx, cy, radius, first, math.tau if full_circle else sweep, clockwise


def _arc_bounds(start, end, values, *, clockwise, absolute_ij, offsets, units, path=None):
    """Bound the XY sweep, retaining literal endpoints rounded by the emitter."""
    cx, cy, radius, first, sweep, clockwise = path or _arc_path(
        start, end, values, clockwise=clockwise, absolute_ij=absolute_ij,
        offsets=offsets, units=units)
    sx, sy, ex, ey = start['X'], start['Y'], end['X'], end['Y']
    points = [(sx, sy), (ex, ey)]
    for angle, point in ((0., (cx+radius, cy)), (math.pi/2, (cx, cy+radius)),
                         (math.pi, (cx-radius, cy)), (3*math.pi/2, (cx, cy-radius))):
        advance = ((first-angle) if clockwise else (angle-first)) % math.tau
        if advance <= sweep + 1e-12:
            points.append(point)
    return [min(x for x, y in points), min(y for x, y in points),
            max(x for x, y in points), max(y for x, y in points)]


@dataclass
class Footprint:
    """Conservative commanded bead occupancy in sparse 0.5 mm Y strips.

    X intervals are merged, not filled across gaps between separate paths.
    A shared strip/interval is a conservative occupancy conflict, not proof of
    physical collision. Arcs use chords with <=0.01 mm sagitta, added to the
    bead radius; the native endpoint-radius tolerance is covered as well.
    Coordinates beyond +/-2048 mm and more than 200000 intervals in a block
    are rejected rather than allocating an unbounded path index.
    """
    rows: dict[int, list[tuple[float, float]]] = field(default_factory=dict)
    intervals: int = 0
    bounds: list[float] = field(default_factory=lambda: [math.inf,math.inf,-math.inf,-math.inf])
    pitch = .5
    sagitta = .01
    extent = 2048.
    max_intervals = 200000

    def _add(self, row, low, high):
        spans = self.rows.setdefault(row, [])
        index = bisect_left(spans, (low, -math.inf))
        if index and spans[index-1][1] >= low:
            index -= 1
            low = spans[index][0]
        stop = index
        while stop < len(spans) and spans[stop][0] <= high:
            high = max(high, spans[stop][1]); stop += 1
        self.intervals += 1 - (stop-index)
        if self.intervals > self.max_intervals:
            raise ValueError('Commanded footprint exceeds the finite occupancy interval bound')
        spans[index:stop] = [(low, high)]

    def add_line(self, start, end, radius):
        sx, sy = start; ex, ey = end
        if (not all(math.isfinite(v) for v in (*start, *end, radius)) or radius < 0
                or max(abs(sx), abs(sy), abs(ex), abs(ey)) + radius > self.extent):
            raise ValueError('Commanded footprint exceeds the finite occupancy coordinate bound')
        # The epsilon covers closed strip boundaries and floating point math.
        radius += 1e-9
        self.bounds = [min(self.bounds[0],sx-radius,ex-radius),
                       min(self.bounds[1],sy-radius,ey-radius),
                       max(self.bounds[2],sx+radius,ex+radius),
                       max(self.bounds[3],sy+radius,ey+radius)]
        dx, dy = ex-sx, ey-sy
        for row in range(math.floor((min(sy,ey)-radius)/self.pitch),
                         math.floor((max(sy,ey)+radius)/self.pitch)+1):
            if dy:
                a = (row*self.pitch-radius-sy)/dy
                b = ((row+1)*self.pitch+radius-sy)/dy
                first, last = max(0., min(a,b)), min(1., max(a,b))
                if first > last:
                    continue
                x, X = sx+first*dx, sx+last*dx
            else:
                x, X = sx, ex
            self._add(row, min(x,X)-radius, max(x,X)+radius)

    def add_arc(self, path, start, end, radius, first_fraction):
        cx, cy, r, angle, sweep, clockwise = path
        # Stable even for a very large radius and tiny sweep.
        step = min(math.pi/2, 4*math.asin(min(1., math.sqrt(self.sagitta/(2*r)))))
        count = max(1, math.ceil(sweep*(1-first_fraction)/step))
        if count > 4096:
            raise ValueError('Commanded arc exceeds the finite occupancy chord bound')
        sign = -1 if clockwise else 1
        delta = abs(math.hypot(end['X']-cx, end['Y']-cy)-r)
        covered_radius = radius + self.sagitta + delta
        def point(fraction):
            a = angle + sign*sweep*fraction
            return cx+r*math.cos(a), cy+r*math.sin(a)
        previous = (start['X'],start['Y']) if not first_fraction else point(first_fraction)
        for i in range(1,count+1):
            fraction = first_fraction + (1-first_fraction)*i/count
            current = (end['X'],end['Y']) if i==count else point(fraction)
            self.add_line(previous,current,covered_radius)
            previous = current

    def add_motion(self, start, end, path, radius, fraction):
        if path:
            self.add_arc(path,start,end,radius,fraction)
        else:
            first = tuple(start[a] + fraction*(end[a]-start[a]) for a in 'XY')
            self.add_line(first,(end['X'],end['Y']),radius)

    def update(self, other):
        self.bounds = [min(self.bounds[0],other.bounds[0]),min(self.bounds[1],other.bounds[1]),
                       max(self.bounds[2],other.bounds[2]),max(self.bounds[3],other.bounds[3])]
        for row, spans in other.rows.items():
            for low, high in spans:
                self._add(row,low,high)

    def intersects_box(self, box):
        if (self.bounds[2]<box[0] or self.bounds[0]>box[2]
                or self.bounds[3]<box[1] or self.bounds[1]>box[3]):
            return False
        for row in range(math.floor(box[1]/self.pitch), math.floor(box[3]/self.pitch)+1):
            for low, high in self.rows.get(row, ()):
                if low > box[2]: break
                if high >= box[0]: return True
        return False

    def intersects(self, other):
        a,b=self.bounds,other.bounds
        if a[2]<b[0] or a[0]>b[2] or a[3]<b[1] or a[1]>b[3]:
            return False
        for row, spans in self.rows.items():
            theirs = other.rows.get(row, ())
            i = j = 0
            while i<len(spans) and j<len(theirs):
                a,b=spans[i],theirs[j]
                if a[0]<=b[1] and b[0]<=a[1]: return True
                if a[1]<b[0]: i += 1
                else: j += 1
        return False


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
    footprint: Footprint = field(default_factory=Footprint)


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
    fallback_width_depositions: int = 0

    def is_tower(self, name):
        return name in self.tower_names if self.tower_names is not None else name.startswith(SIGNATURE)

    def schedule(self):
        return {round(layer.z, 6): sorted({p.tool for p in layer.phases
                if any(not self.is_tower(b.name) for b in p.blocks)}) for layer in self.layers}


def trace(text, *, diameters, tower_names=None, brim_boxes=None, fallback_line_width_mm=.8):
    """Parse native object-labelled deposition. Reject unknown print-time priming."""
    if (not math.isfinite(fallback_line_width_mm)
            or not 0 < fallback_line_width_mm <= 10):
        raise ValueError('Invalid fallback bead width')
    # Complete native output declares ;WIDTH. The explicit fallback supports
    # synthetic/minimal fixtures and is not a measured physical bead width.
    width, width_declared, fallback_width_depositions = fallback_line_width_mm, False, 0
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
        if line.startswith(';WIDTH:'):
            width = float(line.split(':',1)[1])
            if not math.isfinite(width) or not 0 < width <= 10:
                raise ValueError('Invalid or unbounded native bead WIDTH')
            width_declared = True
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
        arc = None
        if op in {'G2', 'G3'}:
            xy = True
            arc = _arc_path(pos, new, values, clockwise=op == 'G2',
                            absolute_ij=absolute_ij, offsets=offsets, units=units)
            bounds = _arc_bounds(pos, new, values, clockwise=op == 'G2',
                                 absolute_ij=absolute_ij, offsets=offsets, units=units, path=arc)
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
            # Only the final fraction deposits after repaying retract debt.
            fraction = max(0., min(1., 1-positive/de))
            # Cover increases in commanded flow, without shrinking a bead
            # declared by ;WIDTH when flow is reduced.
            radius = width/2 * max(1.,flow[tool])
            motion_footprint = None
            deposition_label=label
            if not deposition_label and feature=='brim' and z is not None and z==layers[0].z:
                # Native first-layer brims precede object comments. Only an
                # explicit spatial match can attribute one to our auxiliary.
                motion_footprint=Footprint()
                motion_footprint.add_motion(pos,new,arc,radius,fraction)
                deposited=motion_footprint.bounds
                matches=[name for name,box in (brim_boxes or {}).items()
                         if deposited[0]>=box[0] and deposited[1]>=box[1]
                         and deposited[2]<=box[2] and deposited[3]<=box[3]]
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
                if volume > 0:
                    if not width_declared:
                        fallback_width_depositions += 1
                    if motion_footprint is not None:
                        block.footprint.update(motion_footprint)
                    else:
                        block.footprint.add_motion(pos,new,arc,radius,fraction)
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
                 auxiliary, startup, tower_names, fallback_width_depositions)


def audit_tower(result, *, targets):
    failures, checked, structural = set(), 0, 0
    for phase_index, phase in enumerate(result.phases):
        seen_model = False; tower = []
        for block in phase.blocks:
            # Native first-layer brims precede the foundation and models.
            # They remain in deposition/occupancy evidence, but setup-only
            # brims in the initial unchanged phase are not cleaning visits.
            if phase_index == 0 and not phase.changed and block.features == {'brim'}:
                continue
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
