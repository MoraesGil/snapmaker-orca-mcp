"""Top-surface feedrate attribution on the official synthetic U1 PLA box."""
import pytest

from .harness import observe
from .property_case import paired_property


def assert_top_surface_speed(low, high):
    """Require the requested speeds on actual upper-layer top-surface extrusion."""
    for observation, expected in ((low, 1200), (high, 3600)):
        moves = [move for move in observation['moves']
                 if move['feature'] == 'Top surface' and move['z'] > 4
                 and move['e'] > 0 and move['length'] > 0]
        assert moves, 'No upper-layer top-surface extrusion'
        assert any(abs(move['f'] - expected) < 1 for move in moves), (
            f'Top surface never reaches requested F{expected}')
        assert all(move['f'] <= expected + 1 for move in moves), (
            'Top-surface extrusion exceeds its requested speed')


def test_top_surface_speed(native_engine, tmp_path):
    paired_property(
        native_engine, tmp_path, key='top_surface_speed',
        values=(([20, 20], ['20', '20']), ([60, 60], ['60', '60'])),
        oracle=assert_top_surface_speed,
        scope='24x24x5 mm U1 box; upper top surface F1200 versus F3600; '
              'PLA Cool Plate 50 C; auxiliary fan zero; layer cooling slowdown disabled',
        # Isolate the speed property from cooling slowdown. At 60 mm/s this
        # 0.2 mm fixture remains below the official PLA volumetric cap.
        config_overrides={'slow_down_for_layer_cooling': ['0']},
    )


def sample(feed, role='Top surface', z=5):
    return observe(f'M83\nG1 Z{z}\n; FEATURE: {role}\nG1 X10 E1 F{feed}\n')


def test_oracle_accepts_top_surface_extrusion(tmp_path):
    assert_top_surface_speed(sample(1200), sample(3600))


@pytest.mark.parametrize('text', ['', '; FEATURE: Top surface\n; F1200 F3600\n'])
def test_oracle_rejects_empty_or_comments(text, tmp_path):
    with pytest.raises(AssertionError):
        assert_top_surface_speed(observe(text), observe(text))


@pytest.mark.parametrize('feed', [1200, 3600])
def test_oracle_rejects_noop(feed, tmp_path):
    with pytest.raises(AssertionError):
        assert_top_surface_speed(sample(feed), sample(feed))


@pytest.mark.parametrize('role,z', [('Internal solid infill', 5), ('Outer wall', 5),
                                  ('Top surface', 0.2)])
def test_oracle_rejects_wrong_role_or_layer(role, z, tmp_path):
    with pytest.raises(AssertionError):
        assert_top_surface_speed(sample(1200, role, z), sample(3600, role, z))
