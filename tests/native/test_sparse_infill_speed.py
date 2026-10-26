"""Sparse-infill speed persistence paired with actual extrusion feedrates."""
import pytest

from .harness import feature_moves, observe
from .property_case import paired_property


def assert_sparse_infill_speed(low, high):
    """Require commanded 20/60 mm/s on sparse infill above first layers."""
    a = feature_moves(low, 'Sparse infill')
    b = feature_moves(high, 'Sparse infill')
    assert a and b, 'Sparse infill must contain actual XY extrusion above 0.8 mm'
    assert any(abs(move['f'] - 1200) < 1 for move in a)
    assert any(abs(move['f'] - 3600) < 1 for move in b)
    assert max(move['f'] for move in b) > max(move['f'] for move in a) * 1.5


def test_sparse_infill_speed_native(native_engine, tmp_path):
    paired_property(
        native_engine, tmp_path, key='sparse_infill_speed',
        values=(([20, 20], ['20', '20']), ([60, 60], ['60', '60'])),
        oracle=assert_sparse_infill_speed,
        scope='24x24x5 mm box; Sparse infill XY extrusion above 0.8 mm; F1200 versus F3600',
    )


def sparse_commands(feed, *, feature='Sparse infill', height=1.2, extrusion=True):
    return (f'M83\nG1 Z{height}\n; FEATURE: {feature}\n'
            f'G1 X10 Y10 F{feed}' + (' E1' if extrusion else '') + '\n')


def test_sparse_infill_oracle_accepts_commanded_extrusion():
    assert_sparse_infill_speed(observe(sparse_commands(1200)), observe(sparse_commands(3600)))


@pytest.mark.parametrize('low,high', [
    ('', ''),
    ('; sparse_infill_speed = 20\n; FEATURE: Sparse infill\n',
     '; sparse_infill_speed = 60\n; FEATURE: Sparse infill\n'),
    (sparse_commands(1200), sparse_commands(1200)),
    (sparse_commands(3600), sparse_commands(3600)),
    (sparse_commands(1200, extrusion=False), sparse_commands(3600, extrusion=False)),
    (sparse_commands(1200, height=.2), sparse_commands(3600, height=.2)),
    (sparse_commands(1200, feature='Inner wall'), sparse_commands(3600, feature='Inner wall')),
])
def test_sparse_infill_oracle_rejects_missing_or_unchanged_effect(low, high):
    with pytest.raises(AssertionError):
        assert_sparse_infill_speed(observe(low), observe(high))
