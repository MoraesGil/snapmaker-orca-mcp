"""Synthetic regressions for event auditing and demo instance preservation."""
import copy
from xml.etree import ElementTree as ET

from test_editor import project as synthetic_project

from snapmaker_slicer_mcp.project import Project, _child, _set_metadata
from snapmaker_slicer_mcp.plugins.custom_tower.archive import normalize_demo
from snapmaker_slicer_mcp.plugins.custom_tower.contracts import TowerOptions
from snapmaker_slicer_mcp.plugins.custom_tower.gcode import trace
from snapmaker_slicer_mcp.plugins.custom_tower.workflow import audit_candidate


TOWER = '__SYNTH_TOWER_V1__' + 'a' * 32


def _block(name, z, *, tower=False, interface=False):
    x = 50 if tower else 10
    feature = 'Support interface' if interface else 'Outer wall'
    return (
        f'; printing object {name} id:{1 if tower else 0} copy 0\n'
        f';TYPE:{feature}\nG0 X{x} Y10 Z{z}\n'
        f'G1 X{x + 2} Y10 E1 F1200\n'
        f';TYPE:{feature if interface else "Sparse infill"}\n'
        f'G1 X{x} Y12 E1\n'
        f'; stop printing object {name} id:{1 if tower else 0} copy 0\n'
    )


def _sample(*, tower, extra_tower_phases=False, model_temperatures=(200, 230)):
    """The normal job has two tool changes at Z0.4, with a T0-only base."""
    first, second = model_temperatures
    text = f'G90\nM83\nT0\nM140 S50\n;LAYER_CHANGE\n;Z:0.2\nM104 T0 S{first}\n'
    if tower:
        text += _block(TOWER, .2, tower=True)
    text += _block('Model', .2)
    text += ';LAYER_CHANGE\n;Z:0.4\n'
    if extra_tower_phases:
        text += 'T1\nM104 T1 S240\n' + _block(TOWER, .4, tower=True)
        text += f'T0\nM104 T0 S{second}\n' + _block(TOWER, .4, tower=True)
    text += 'T1\nM104 T1 S240\n'
    if tower:
        text += _block(TOWER, .4, tower=True)
    text += _block('Model', .4, interface=True)
    text += f'T0\nM104 T0 S{second}\n'
    if tower:
        text += _block(TOWER, .4, tower=True)
    return text + _block('Model', .4)


def _trace(text):
    return trace(text, diameters=[1.75, 1.75], tower_names={TOWER})


def _options():
    return TowerOptions(
        model_tools=[0], interface_tool=1, primary_object_id=2,
        minimum_width_mm=16, maximum_width_mm=32,
        purge_volume_mm3={0: 1, 1: 1},
    )


def _plan():
    return {'rows': [
        {'z': .2, 'regions': [
            {'tool': 0, 'purpose': 'structure', 'target_mm3': 0, 'density': 10},
        ]},
        {'z': .4, 'regions': [
            {'tool': 1, 'purpose': 'prime_after_change', 'target_mm3': 1, 'density': 20},
            {'tool': 0, 'purpose': 'prime_after_change', 'target_mm3': 1, 'density': 20},
        ]},
    ]}


def test_rejects_extra_toolchanges_with_only_tower_deposition_at_the_same_z():
    baseline = _trace(_sample(tower=False))
    valid = _trace(_sample(tower=True))
    extra_changes = _trace(_sample(tower=True, extra_tower_phases=True))

    assert audit_candidate(baseline, valid, _plan(), _options())['passed']
    result = audit_candidate(baseline, extra_changes, _plan(), _options())
    assert not result['passed'], (
        'Two extra T1/T0 changes must be rejected even when both print only '
        f'the tower and the external material schedule is unchanged: {result}'
    )


def test_rejects_swapped_deposition_temperatures_at_the_same_tool_heights():
    baseline = _trace(_sample(tower=False, model_temperatures=(200, 230)))
    valid = _trace(_sample(tower=True, model_temperatures=(200, 230)))
    swapped = _trace(_sample(tower=True, model_temperatures=(230, 200)))

    assert audit_candidate(baseline, valid, _plan(), _options())['passed']
    result = audit_candidate(baseline, swapped, _plan(), _options())
    assert not result['passed'], (
        'T0 must retain 200 C at Z0.2 and 230 C at Z0.4; preserving only '
        f'the unordered set of temperatures is insufficient: {result}'
    )


def test_enabling_demo_preserves_a_disabled_instance_of_the_same_root_on_plate_two(tmp_path):
    current = Project(synthetic_project(tmp_path / 'synthetic-demo.3mf'))
    demo = current._items(2)[0]
    demo.set('printable', '0')
    production = copy.deepcopy(demo)
    _child(current.model, 'build').append(production)
    plate_two = ET.SubElement(current.settings, 'plate')
    _set_metadata(plate_two, 'plater_id', 2)
    _set_metadata(plate_two, 'plater_name', 'Production')
    instance = ET.SubElement(plate_two, 'model_instance')
    for key, value in {'object_id': 2, 'instance_id': 1, 'identify_id': 75}.items():
        _set_metadata(instance, key, value)

    normalize_demo(current, [2])

    instances = current.list_objects()[0]['instances']
    assert {item['plate']: item['printable'] for item in instances} == {1: True, 2: False}
