"""Only exclusive brims in the initial, unchanged phase are setup occupancy."""
import math

import pytest

from snapmaker_slicer_mcp.plugins.custom_tower.gcode import trace, audit_tower

NAME='__SYNTH_TOWER_V1__'+'a'*32
HEAD='G90\nM83\nT0\nM104 S230\nM140 S50\n;Z:0.2\n;WIDTH:0.5\nG0 Z0.2\n'


def block(label,feature,x=50,e=.1,z=.2):
    return (f'; printing object {label} id:0 copy 0\n;TYPE:{feature}\n'
            f'G0 X{x} Y10 Z{z}\nG1 X{x+1} Y10 E{e} F1200\n'
            f'; stop printing object {label} id:0 copy 0\n')


def functional_tower(z=.2):
    return block(NAME,'Outer wall',e=.1,z=z)+block(NAME,'Sparse infill',e=.1,z=z)


def same_native_order(z=.2,brim_e=.1):
    return (block(NAME,'Brim',e=brim_e,z=z)
            +block('External brim','Brim',10,e=brim_e,z=z)
            +functional_tower(z)+block('Model','Outer wall',10,z=z))


def parsed(text):
    return trace(text,diameters=[1.75]*2,tower_names={NAME})


def test_initial_unchanged_brim_order_does_not_look_like_a_cleaning_return():
    result=parsed(HEAD+same_native_order())
    audit=audit_tower(result,targets={0:1})
    assert audit['passed'],audit['failures']
    assert audit['changes_checked']==0
    assert audit['structural_visits_without_change']==1
    # Ignoring logical setup must not remove commanded material, occupancy,
    # temperature or other evidence from any trace block.
    phase=result.phases[0]
    assert phase.volume_mm3==pytest.approx(.5*math.pi*(1.75/2)**2)
    assert phase.blocks[0].features=={'brim'}
    assert phase.blocks[0].footprint.rows and phase.blocks[1].footprint.rows
    assert all(b.temperatures=={(230.,50.)}for b in phase.blocks)


def test_functional_tower_then_model_then_tower_still_fails_without_a_change():
    result=parsed(HEAD+functional_tower()+block('Model','Outer wall',10)+functional_tower())
    assert not result.phases[0].changed
    audit=audit_tower(result,targets={0:1})
    assert not audit['passed']
    assert 'tower_return_after_model'in audit['failures']


def test_first_foundation_after_a_real_model_is_still_rejected():
    result=parsed(HEAD+block('Model','Outer wall',10)+functional_tower())
    audit=audit_tower(result,targets={0:1})
    assert 'tower_return_after_model'in audit['failures']


def test_initial_brim_does_not_hide_a_real_model_before_the_foundation():
    result=parsed(HEAD+block(NAME,'Brim')+block('Model','Outer wall',10)+functional_tower())
    audit=audit_tower(result,targets={0:1})
    assert 'tower_return_after_model'in audit['failures']


def test_a_later_unchanged_phase_keeps_all_brim_order_checks():
    result=parsed(HEAD+block('Model','Outer wall',10)+';Z:0.4\n'+same_native_order(.4))
    assert not result.phases[1].changed
    audit=audit_tower(result,targets={0:1})
    assert not audit['passed']
    assert 'tower_return_after_model'in audit['failures']


def test_a_real_change_in_the_first_phase_has_no_brim_exception():
    result=parsed(HEAD+'T1\n'+same_native_order())
    assert result.phases[0].changed
    audit=audit_tower(result,targets={1:.1})
    assert not audit['passed']
    assert 'tower_return_after_model'in audit['failures']


def test_blocks_with_brim_and_other_features_are_not_setup_only():
    text=HEAD+block(NAME,'Brim')
    text+=(f'; printing object Mixed id:0 copy 0\n;TYPE:Brim\n'
           'G0 X10 Y10\nG1 X11 E.1 F1200\n;TYPE:Outer wall\nG1 X12 E.1\n'
           '; stop printing object Mixed id:0 copy 0\n')
    result=parsed(text+functional_tower())
    assert result.phases[0].blocks[1].features=={'brim','outer wall'}
    audit=audit_tower(result,targets={0:1})
    assert 'tower_return_after_model'in audit['failures']


def test_initial_brim_only_does_not_count_as_a_structural_cleaning_visit():
    result=parsed(HEAD+block(NAME,'Brim')+block('External brim','Brim',10))
    audit=audit_tower(result,targets={0:1})
    assert audit['passed']
    assert audit['structural_visits_without_change']==0
    assert result.phases[0].volume_mm3>0


def test_real_change_still_requires_full_purge_volume_contour_and_infill():
    # A small valid pass before the model cannot borrow enough volume from a
    # different phase's very large initial brim.
    text=HEAD+same_native_order(brim_e=100)+';Z:0.4\nT1\n'
    text+=functional_tower(.4)+block('Model','Outer wall',10,z=.4)
    audit=audit_tower(parsed(text),targets={1:1})
    assert not audit['passed']
    assert audit['changes_checked']==1
    assert 'insufficient_deposition_volume'in audit['failures']
