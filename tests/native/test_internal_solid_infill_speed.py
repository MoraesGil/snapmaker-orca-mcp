"""Internal solid infill feedrates on the official synthetic U1 PLA box."""
import pytest

from .harness import feature_moves, observe
from .property_case import paired_property


def assert_internal_solid_speed(low, high):
    """Require real above-first-layer extrusion at both requested feedrates."""
    for observation, expected in ((low, 1200), (high, 3600)):
        moves = feature_moves(observation, 'Internal solid infill')
        assert moves, 'No internal solid infill extrusion above first layers'
        assert any(abs(move['f'] - expected) < 1 for move in moves), (
            f'Internal solid infill never reaches F{expected}'
        )
    assert max(m['f'] for m in feature_moves(high, 'Internal solid infill')) > max(
        m['f'] for m in feature_moves(low, 'Internal solid infill')
    ) * 1.5


def test_internal_solid_infill_speed_native(native_engine, tmp_path):
    paired_property(
        native_engine, tmp_path, key='internal_solid_infill_speed',
        values=(([20, 20], ['20', '20']), ([60, 60], ['60', '60'])),
        oracle=assert_internal_solid_speed,
        scope='24x24x5 box; U1 PLA; Cool Plate 50 C; auxiliary fan off; '
              'cooling slowdown disabled; official volumetric limit retained',
        config_overrides={'slow_down_for_layer_cooling': ['0']},
    )


def sample(feed, *, role='Internal solid infill', height=1.2, extrusion=True):
    return observe(
        f'M83\nG1 Z{height}\n; FEATURE: {role}\n'
        f'G1 X10 Y10 F{feed}' + (' E1' if extrusion else '') + '\n'
    )


def test_internal_solid_oracle_accepts_extrusion():
    assert_internal_solid_speed(sample(1200), sample(3600))


@pytest.mark.parametrize('low,high', [
    (observe(''), observe('')),
    (observe('; internal_solid_infill_speed = 20'),
     observe('; internal_solid_infill_speed = 60')),
    (sample(1200), sample(1200)),
    (sample(1200, role='Top surface'), sample(3600, role='Top surface')),
    (sample(1200, extrusion=False), sample(3600, extrusion=False)),
    (sample(1200, height=.2), sample(3600, height=.2)),
], ids=['empty', 'comments', 'no-op', 'wrong-role', 'travel', 'first-layer'])
def test_internal_solid_oracle_rejects_false_evidence(low, high):
    with pytest.raises(AssertionError):
        assert_internal_solid_speed(low, high)
