"""Flow-ratio evidence uses deposited E per identical outer-wall XY path."""
import math
from collections import defaultdict

import pytest

from .harness import observe
from .property_case import paired_property


def flow_measurements(observation):
    moves = [m for m in observation['moves']
             if m['feature'] == 'Outer wall' and m['z'] > 0.8]
    assert len(moves) >= 12, 'Insufficient real outer-wall extrusion moves'
    layers = defaultdict(list)
    for move in moves:
        assert move['length'] > 0 and move['e'] > 0
        layers[move['z']].append(move)
    assert len(layers) >= 3, 'Insufficient outer-wall layers'
    return layers


def assert_flow_effect(low, high):
    a, b = flow_measurements(low), flow_measurements(high)
    assert a.keys() == b.keys(), 'Flow comparison changed layer geometry'
    for z in a:
        # Same ordered endpoints AND segment lengths prevent changed geometry,
        # segmentation or move counts from masquerading as a flow change.
        paths = [[(m['x'], m['y'], m['length']) for m in layers[z]] for layers in (a, b)]
        assert len(paths[0]) == len(paths[1]), 'Flow comparison changed path count'
        for left, right in zip(*paths):
            assert all(math.isclose(x, y, rel_tol=0, abs_tol=0.0001)
                       for x, y in zip(left, right)), 'Flow comparison changed XY path'
        densities = [sum(m['e'] for m in layers[z]) / sum(m['length'] for m in layers[z])
                     for layers in (a, b)]
        # Five-decimal native E rounding on these long walls is far below 0.2%.
        # Check each layer, then the aggregate; no config comments are evidence.
        assert math.isclose(densities[1] / densities[0], 1.5, rel_tol=0.002), 'Wrong deposited E/path ratio'
    totals = [(sum(m['e'] for rows in layers.values() for m in rows),
               sum(m['length'] for rows in layers.values() for m in rows)) for layers in (a, b)]
    assert math.isclose((totals[1][0] / totals[1][1]) / (totals[0][0] / totals[0][1]),
                        1.5, rel_tol=0.002)


def test_native_filament_flow_ratio(native_engine, tmp_path):
    paired_property(native_engine, tmp_path, key='filament_flow_ratio',
                    values=(([0.8], ['0.8']), ([1.2], ['1.2'])), oracle=assert_flow_effect,
                    scope='One-slot official U1 PLA 24x24x5 box; identical outer-wall paths above 0.8mm; deposited E/XY scales 1.5; no physical print',
                    config_overrides={'outer_wall_speed': ['20', '20'],
                                      'filament_max_volumetric_speed': ['100']})


def sample(flow=1, *, mode='M83', role='Outer wall', span=10):
    lines = [mode, 'G92 E0', f'; FEATURE: {role}']
    absolute = 0
    for z in (1, 1.2, 1.4):
        lines.append(f'G0 X0 Y0 Z{z}')
        for x, y in ((span, 0), (span, span), (0, span), (0, 0)):
            absolute += flow
            lines.append(f'G1 X{x} Y{y} E{flow if mode == "M83" else absolute}')
    return '\n'.join(lines)


@pytest.mark.parametrize('mode', ['M82', 'M83'])
def test_flow_oracle_hand_derived_positive(mode):
    # Each 40mm square has 4mm versus 6mm E: .1 versus .15 E/mm.
    assert_flow_effect(observe(sample(1, mode=mode)), observe(sample(1.5, mode=mode)))


@pytest.mark.parametrize('bad', [
    '', '; filament_flow_ratio = 1.2\n; FEATURE: Outer wall',
    'M83\n; FEATURE: Outer wall\nG0 X10 Z1\nG1 X20 E0',
    sample(1), sample(1.5, role='Inner wall'),
    sample(1, span=20), sample(1.5, span=20),
])
def test_flow_oracle_rejects_missing_or_unrelated_evidence(bad):
    with pytest.raises(AssertionError):
        assert_flow_effect(observe(sample()), observe(bad))
