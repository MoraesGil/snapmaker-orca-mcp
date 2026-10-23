"""Dependency-free complementary Y profiles; native engine generates the infill."""
from __future__ import annotations

import math
from collections import defaultdict


def profile(width):
    points = []
    for i in range(36):
        angle = 2 * math.pi * i / 36 + math.pi / 2
        radius = 1 + .28 * math.cos(3 * (angle - math.pi / 2))
        points.append((radius * math.cos(angle), radius * math.sin(angle)))
    span = max(x for x, _ in points) - min(x for x, _ in points)
    return [(x * width / span, y * width / span) for x, y in points]


def area_perimeter(points):
    pairs = list(zip(points, points[1:] + points[:1]))
    area = sum(x*Y-X*y for (x,y),(X,Y) in pairs) / 2
    perimeter = sum(math.hypot(X-x, Y-y) for (x,y),(X,Y) in pairs)
    return area, perimeter


def pass_density(*, area, height, perimeter, line_width, target, changed, structural_density):
    if not changed:
        return structural_density
    walls = perimeter * line_width
    available = area - walls
    if available <= line_width**2:
        raise ValueError('A tower region is too thin for contour plus infill')
    # Overestimate required infill by 20%; actual positive extrusion is the gate.
    density = max(structural_density, math.ceil(100 * (target * 1.2 / height - walls) / available))
    if density > 100:
        raise ValueError('Purge target cannot fit the declared maximum tower dimensions')
    return min(100, int(math.ceil(density/5)*5))


def make_plan(result, options, primary_name, *, density_boost=0):
    """Bind only materials used externally and taper after primary shrinkage."""
    if result.empty_selections:
        raise ValueError('Native prepass contains empty tool selections; resolve its sequence first')
    last_change = max((p.z for p in result.phases if p.changed), default=None)
    if last_change is None:
        raise ValueError('No real toolchange: a custom tower is unnecessary')
    layers = [l for l in result.layers if l.z <= last_change + 1e-5]
    tops = [l.z for l in layers]
    if len(tops) > 4000:
        raise ValueError('More than 4000 tower layers exceeds the supported generation bound')
    if len(tops) < 2:
        raise ValueError('At least two supported tower layers are required')
    step = tops[1] - tops[0]
    if any(abs(b-a-step) > 1e-5 for a,b in zip(tops, tops[1:])):
        raise ValueError('Adaptive/independent layers are not supported by this tower backend')
    if options.foundation_height_mm and options.foundation_height_mm >= tops[-1]:
        raise ValueError('Foundation flare must finish below the final tower layer')
    measured = []
    for layer in layers:
        bounds = [b.model_bounds for p in layer.phases for b in p.blocks
                  if b.name == primary_name and math.isfinite(b.model_bounds[0])]
        if not bounds:
            raise ValueError('Primary model has no measurable section at every required tower height')
        measured.append(max(max(b[2] for b in bounds)-min(b[0] for b in bounds),
                            max(b[3] for b in bounds)-min(b[1] for b in bounds)))
    remaining = measured.copy()
    for i in range(len(remaining)-2, -1, -1):
        remaining[i] = max(remaining[i], remaining[i+1])
    high, low = remaining[0], remaining[-1]
    if high-low < .001 and not options.foundation_height_mm:
        raise ValueError('Constant model envelope requires an explicit foundation_height_mm exception')
    rows, previous, bottom = [], options.maximum_width_mm, 0.
    declared = set(options.model_tools + [options.interface_tool])
    for index, (layer, envelope) in enumerate(zip(layers, remaining)):
        active = {p.tool for p in layer.phases if any(not result.is_tower(b.name) for b in p.blocks)}
        if active - declared:
            raise ValueError('External deposition uses undeclared tools')
        models = [t for t in options.model_tools if t in active]
        if not models:
            raise ValueError('No model material already used at this height for the continuous shell')
        if index == 0 and options.interface_tool in active:
            raise ValueError('Interface material on the first layer conflicts with the model-only foundation')
        if len({p.tool for p in layer.phases}) != len(layer.phases):
            raise ValueError('Repeated tool visits at one Z cannot be represented by one native region')
        height = layer.z - bottom
        fraction = ((envelope-low)/(high-low)) if high-low >= .001 else 0.
        desired = options.minimum_width_mm + (options.maximum_width_mm-options.minimum_width_mm)*fraction
        if options.foundation_height_mm:
            flare = options.minimum_width_mm + (options.maximum_width_mm-options.minimum_width_mm) * max(0., 1-bottom/options.foundation_height_mm)
            desired = max(desired, flare)
        if index == 0: desired = options.maximum_width_mm
        width = max(options.minimum_width_mm, desired, previous-2*height)
        width = min(previous, width)
        points = profile(width); total_area, perimeter = area_perimeter(points)
        core_fraction = 0.
        if options.interface_tool in active:
            # Alternate material is a single filled central region; a small
            # interlayer variation creates mechanical keys inside the shell.
            core_fraction = max(.08, options.target(options.interface_tool)*1.25/(height*total_area))
            core_fraction *= 1.025 if (index//2)%2 else .975
            if core_fraction > .45:
                raise ValueError('Interface budget would consume the main-material shell')
        phases = {p.tool: p for p in layer.phases}
        rings = []
        outer = 1.
        each = (1-core_fraction)/len(models)
        # First model tool owns the exterior; every colour follows all 3 lobes.
        for j, tool in enumerate(models):
            inner = math.sqrt(max(0., 1-each*(j+1))) if j < len(models)-1 else math.sqrt(core_fraction)
            rings.append((tool, inner, outer)); outer = inner
        if core_fraction:
            rings.append((options.interface_tool, 0., math.sqrt(core_fraction)))
        regions = []
        for tool, inner, outer in rings:
            # The narrowest local radial distance must fit a wall and infill.
            if min(math.hypot(x,y) for x,y in points)*(outer-inner) < 2.2*options.line_width_mm:
                raise ValueError('A nested colour region is too narrow for contour and infill')
            section = total_area*(outer*outer-inner*inner)
            boundary = perimeter*(outer+inner)
            changed = phases[tool].changed
            density = pass_density(area=section, height=height, perimeter=boundary,
                line_width=options.line_width_mm, target=options.target(tool), changed=changed,
                structural_density=options.structural_infill_percent)
            if changed: density = min(100, density+density_boost)
            regions.append(dict(tool=tool, inner=inner, outer=outer, density=density,
                purpose='prime_after_change' if changed else 'structure',
                target_mm3=options.target(tool) if changed else 0., area_mm2=section))
        rows.append(dict(index=index+1, bottom_z=bottom, z=layer.z, width_mm=width,
                         envelope_mm=envelope, tools=sorted(active), regions=regions))
        bottom, previous = layer.z, width
    if abs(rows[-1]['width_mm']-options.minimum_width_mm) > .01:
        raise ValueError('The requested 2:1 taper cannot follow the model with supported lateral steps')
    return {'schema': 1, 'rows': rows, 'height_mm': tops[-1],
            'minimum_width_mm': options.minimum_width_mm, 'maximum_width_mm': options.maximum_width_mm,
            'brim_mm': options.brim_mm, 'filled_geometric_center': True,
            'native_infill': True, 'physical_cleaning_validated': False}


def slab_mesh(width, inner, outer, bottom, top):
    """Closed, oriented star-shaped annular prism, or a filled central prism."""
    points = profile(width); n = len(points)
    vertices = [(x*outer,y*outer,z) for z in (bottom,top) for x,y in points]
    faces = []
    for i in range(n):
        j=(i+1)%n
        faces.extend(((i,j,j+n),(i,j+n,i+n)))
    if inner > 1e-10:
        vertices += [(x*inner,y*inner,z) for z in (bottom,top) for x,y in points]
        for i in range(n):
            j=(i+1)%n; a,b=2*n+i,2*n+j; c,d=3*n+i,3*n+j
            faces.extend(((a,d,b),(a,c,d), (i,b,j),(i,a,b),
                          (i+n,j+n,d),(i+n,d,c)))
    else:
        vertices += [(0.,0.,bottom),(0.,0.,top)]
        for i in range(n):
            j=(i+1)%n
            faces.extend(((2*n,j,i),(2*n+1,i+n,j+n)))
    return vertices, faces


def mesh_groups(plan):
    """Coalesce identical adjacent slabs; all other solids remain closed shells."""
    spans = defaultdict(list)
    for row in plan['rows']:
        for region in row['regions']:
            key = (region['tool'], region['density'])
            shape = (row['width_mm'], region['inner'], region['outer'])
            parts = spans[key]
            if parts and parts[-1][0] == shape and abs(parts[-1][2]-row['bottom_z']) < 1e-6:
                parts[-1] = (shape, parts[-1][1], row['z'])
            else:
                parts.append((shape, row['bottom_z'], row['z']))
    groups = {}
    for key, parts in spans.items():
        vertices, faces = [], []
        for shape, bottom, top in parts:
            vv, ff = slab_mesh(*shape, bottom, top); offset=len(vertices)
            vertices.extend(vv); faces.extend(tuple(v+offset for v in f) for f in ff)
        groups[key] = (vertices, faces)
    return groups
