"""Native XY arc footprints must follow the commanded sweep, not its circle."""
import math

import pytest

from snapmaker_slicer_mcp.plugins.custom_tower.gcode import trace


def _arc_trace(start, command, *, before_xy='', after_xy=''):
    text = (
        'G17\nG21\nG90\nG91.1\nM83\nT0\nM104 S230\nM140 S50\n'
        ';LAYER_CHANGE\n;Z:0.2\nG0 Z0.2\n'
        f'{before_xy}\nG0 X{start[0]} Y{start[1]}\n{after_xy}\n'
        '; printing object Model id:0 copy 0\n;TYPE:Outer wall\n'
        f'{command} E1 F1200\n; stop printing object Model id:0 copy 0\n'
    )
    return trace(text, diameters=[1.75])


@pytest.mark.parametrize(('start', 'command', 'expected'), [
    ((10, 0), 'G3 X0 Y10 I-10 J0', [0, 0, 10, 10]),
    ((0, 10), 'G2 X10 Y0 I0 J-10', [0, 0, 10, 10]),
    ((10, 0), 'G2 X0 Y10 I-10 J0', [-10, -10, 10, 10]),
    ((0, 10), 'G3 X10 Y0 I0 J-10', [-10, -10, 10, 10]),
    ((10, 0), 'G3 X-10 Y0 I-10 J0', [-10, 0, 10, 10]),
    ((10, 0), 'G2 X-10 Y0 I-10 J0', [-10, -10, 10, 0]),
    ((8, -6), 'G3 X8 Y6 I-8 J6', [8, -6, 10, 6]),
    ((8, 6), 'G2 X8 Y-6 I-8 J-6', [8, -6, 10, 6]),
    ((8, -6), 'G2 X8 Y6 I-8 J6', [-10, -10, 8, 10]),
], ids=[
    'ccw-minor', 'cw-minor', 'cw-major', 'ccw-major',
    'upper-semicircle', 'lower-semicircle',
    'ccw-crosses-zero', 'cw-crosses-zero', 'major-excludes-zero',
])
def test_ij_bounds_include_only_quadrants_in_the_commanded_sweep(start, command, expected):
    block = _arc_trace(start, command).phases[0].blocks[0]
    assert block.bounds == pytest.approx(expected, abs=1e-8)
    assert block.model_bounds == pytest.approx(expected, abs=1e-8)


@pytest.mark.parametrize('command', [
    'G2 X10 Y0 I-10 J0', 'G3 X10 Y0 I-10 J0',
    'G2 I-10 J0', 'G3 I-10 J0',
])
def test_native_ij_full_circles_keep_all_four_extrema(command):
    assert _arc_trace((10, 0), command).phases[0].blocks[0].bounds == pytest.approx(
        [-10, -10, 10, 10], abs=1e-8,
    )


def test_tiny_arc_with_a_thousand_mm_radius_does_not_expand_to_its_circle():
    result = _arc_trace((100, 100), 'G3 X100.001 Y100 I0.0005 J1000')
    bounds = result.phases[0].blocks[0].bounds
    assert bounds == pytest.approx([100, 100, 100.001, 100], abs=1e-6)
    assert bounds[2] - bounds[0] < .0011


SQRT_TWO = math.sqrt(2)


@pytest.mark.parametrize(('op', 'radius', 'expected'), [
    ('G3', SQRT_TWO, [0, 1-SQRT_TWO, 2, 0]),
    ('G2', SQRT_TWO, [0, 0, 2, SQRT_TWO-1]),
    ('G3', -SQRT_TWO, [1-SQRT_TWO, -1-SQRT_TWO, 1+SQRT_TWO, 0]),
    ('G2', -SQRT_TWO, [1-SQRT_TWO, 0, 1+SQRT_TWO, 1+SQRT_TWO]),
    ('G3', 1, [0, -1, 2, 0]),
    ('G2', 1, [0, 0, 2, 1]),
], ids=['ccw-minor', 'cw-minor', 'ccw-major', 'cw-major', 'ccw-semicircle', 'cw-semicircle'])
def test_signed_radius_selects_the_minor_or_major_arc(op, radius, expected):
    block = _arc_trace((0, 0), f'{op} X2 Y0 R{radius}').phases[0].blocks[0]
    assert block.bounds == pytest.approx(expected, abs=1e-8)


@pytest.mark.parametrize('mode', ['G90.1', 'G91.1'])
def test_g92_offsets_apply_to_absolute_centers_but_not_relative_center_offsets(mode):
    center = 'I0 J0' if mode == 'G90.1' else 'I-1 J0'
    result = _arc_trace((11, 20), f'G3 X0 Y1 {center}', after_xy=f'G92 X1 Y0\n{mode}')
    assert result.phases[0].blocks[0].bounds == pytest.approx([10, 20, 11, 21])


def test_relative_xy_positions_do_not_change_relative_ij_semantics():
    result = _arc_trace((11, 20), 'G3 X-1 Y1 I-1 J0', after_xy='G91')
    assert result.phases[0].blocks[0].bounds == pytest.approx([10, 20, 11, 21])


@pytest.mark.parametrize(('start', 'command', 'after_xy', 'expected'), [
    ((1, 0), 'G3 X0 Y1 I-1 J0', '', [0, 0, 25.4, 25.4]),
    ((1, 0), 'G3 X0 Y1 I0 J0', 'G90.1', [0, 0, 25.4, 25.4]),
    ((0, 0), f'G3 X2 Y0 R{SQRT_TWO}', '', [0, 25.4*(1-SQRT_TWO), 50.8, 0]),
])
def test_g20_scales_arc_positions_centers_and_radius_to_mm(start, command, after_xy, expected):
    result = _arc_trace(start, command, before_xy='G20', after_xy=after_xy)
    assert result.phases[0].blocks[0].bounds == pytest.approx(expected, abs=1e-8)
    assert result.phases[0].volume_mm3 == pytest.approx(25.4*math.pi*(1.75/2)**2)


def test_native_rounded_ij_endpoint_is_retained_within_the_explicit_radius_tolerance():
    result = _arc_trace((1, 0), 'G3 X0 Y1.0025 I-1 J0')
    assert result.phases[0].blocks[0].bounds == pytest.approx([0, 0, 1, 1.0025])


@pytest.mark.parametrize(('start', 'command', 'after_xy'), [
    ((1, 0), 'G3 X0 Y1', ''),
    ((1, 0), 'G3 X0 Y1 I0 J0', ''),
    ((1, 0), 'G3 X0 Y1.004 I-1 J0', ''),
    ((1, 0), 'G3 X0 Y1 I-1 J0 R1', ''),
    ((0, 0), 'G3 X3 Y0 R1', ''),
    ((0, 0), 'G3 X1 Y0 R0', ''),
    ((0, 0), 'G3 X0 Y0 R1', ''),
    ((1, 0), 'G3 X0 Y1 I0', 'G90.1'),
], ids=[
    'no-center', 'zero-ij-radius', 'inconsistent-ij-radius', 'mixed-r-and-ij',
    'chord-exceeds-diameter', 'zero-r-radius', 'ambiguous-r-full-circle', 'incomplete-absolute-center',
])
def test_invalid_or_ambiguous_arc_geometry_is_rejected(start, command, after_xy):
    with pytest.raises(ValueError, match='[Aa]rc|radius|center'):
        _arc_trace(start, command, after_xy=after_xy)


def test_arc_footprint_does_not_change_extrusion_temperature_or_tool_phases():
    result = _arc_trace((10, 0), 'G3 X0 Y10 I-10 J0')
    assert [(p.z, p.tool, p.changed) for p in result.phases] == [(.2, 0, False)]
    assert result.schedule() == {.2: [0]}
    assert result.empty_selections == []
    assert result.phases[0].volume_mm3 == pytest.approx(math.pi*(1.75/2)**2)
    assert result.phases[0].blocks[0].temperatures == {(230, 50)}
