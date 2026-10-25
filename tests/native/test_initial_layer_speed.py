"""First-layer wall feedrate attribution on the official synthetic U1 PLA box."""
import pytest

from .harness import observe
from .property_case import paired_property


def assert_initial_layer_speed(low, high):
    """Require literal feedrates on XY wall extrusion at the first printed layer."""
    for observation, expected in ((low, 600), (high, 1800)):
        extrusion = [move for move in observation['moves']
                     if move['z'] > 0 and move['e'] > 0 and move['length'] > 0]
        assert extrusion, 'No positive-Z XY extrusion'
        printed = [move for move in extrusion if move['feature'] in (
            'Outer wall', 'Inner wall', 'Bottom surface', 'Top surface',
            'Internal solid infill', 'Sparse infill')]
        assert printed, 'No printable fixture extrusion'
        # The native start script extrudes a Custom purge line at Z0.2.
        # Identify the printed layer independently of that preparation move.
        first_z = min(move['z'] for move in printed)
        # The selected official U1 profile prints this fixture at Z0.25 first.
        assert abs(first_z - 0.25) < 0.001, 'Unexpected first printable layer'
        for role in ('Outer wall', 'Inner wall'):
            moves = [move for move in extrusion if move['feature'] == role
                     and abs(move['z'] - first_z) < 0.001]
            assert moves, f'No first-layer {role} extrusion'
            assert any(abs(move['f'] - expected) < 1 for move in moves), (
                f'First-layer {role} never reaches requested F{expected}')
            assert all(move['f'] <= expected + 1 for move in moves), (
                f'First-layer {role} exceeds its requested speed')


def test_initial_layer_speed(native_engine, tmp_path):
    paired_property(
        native_engine, tmp_path, key='initial_layer_speed',
        values=(([10, 10], ['10', '10']), ([30, 30], ['30', '30'])),
        oracle=assert_initial_layer_speed,
        scope='24x24x5 mm U1 box; first-layer Outer/Inner wall at Z0.25 '
              'F600 versus F1800; PLA Cool Plate 50 C; auxiliary fan zero; '
              'layer cooling slowdown disabled',
        config_overrides={'slow_down_for_layer_cooling': ['0']},
    )


def sample(feed, role=None, z=0.25, extrusion=True):
    roles = [role] if role else ['Outer wall', 'Inner wall']
    return observe('M83\n' + f'G1 Z{z}\n' + ''.join(
        f'; FEATURE: {name}\nG1 X{10 + index * 10} '
        f'E{1 if extrusion else 0} F{feed}\n'
        for index, name in enumerate(roles)))


def test_oracle_accepts_first_layer_walls():
    assert_initial_layer_speed(sample(600), sample(1800))


@pytest.mark.parametrize('text', ['', '; FEATURE: Outer wall\n; F600 F1800\n'])
def test_oracle_rejects_empty_or_comments(text):
    with pytest.raises(AssertionError):
        assert_initial_layer_speed(observe(text), observe(text))


@pytest.mark.parametrize('feed', [600, 1800])
def test_oracle_rejects_noop(feed):
    with pytest.raises(AssertionError):
        assert_initial_layer_speed(sample(feed), sample(feed))


@pytest.mark.parametrize('role,z,extrusion', [
    ('Internal solid infill', 0.25, True),
    ('Outer wall', 0.25, True),  # Missing inner walls cannot satisfy the fixture.
    (None, 0.45, True),
    (None, 0.25, False),
])
def test_oracle_rejects_wrong_role_later_layer_or_travel(role, z, extrusion):
    with pytest.raises(AssertionError):
        assert_initial_layer_speed(sample(600, role, z, extrusion),
                                   sample(1800, role, z, extrusion))


def test_oracle_rejects_requested_speed_only_on_later_walls():
    low = sample(300)
    high = sample(300)
    low['moves'].extend(sample(600, z=0.45)['moves'])
    high['moves'].extend(sample(1800, z=0.45)['moves'])
    with pytest.raises(AssertionError):
        assert_initial_layer_speed(low, high)
