"""Sparse commanded bead occupancy refines aggregate bounding boxes."""
import math
from types import SimpleNamespace

import pytest

from snapmaker_slicer_mcp.plugins.custom_tower.archive import place
from snapmaker_slicer_mcp.plugins.custom_tower.contracts import TowerOptions
from snapmaker_slicer_mcp.plugins.custom_tower.gcode import trace, Footprint
from snapmaker_slicer_mcp.plugins.custom_tower.workflow import audit_candidate, read_trace

NAME='__SYNTH_TOWER_V1__'+'a'*32
HEAD='G17\nG90\nM83\nT0\n;Z:0.2\n;HEIGHT:0.2\n;WIDTH:0.5\nG0 Z0.2\n'
BED=['0x0','100x0','100x100','0x100']


def options(center=(50,50),**changes):
    return TowerOptions(model_tools=[0],interface_tool=1,primary_object_id=2,
        minimum_width_mm=12,maximum_width_mm=24,brim_mm=0,clearance_mm=1,
        center_xy=center,**changes)


def brims():
    return (';TYPE:Brim\nG0 X5 Y5\nG1 Y95 E1 F1200\n'
            'G0 X95 Y95\nG1 Y5 E1\n')


def model():
    return ('; printing object Model id:0 copy 0\n;TYPE:Outer wall\n'
            'G0 X5 Y2\nG1 X10 Y2 E0.1 F1200\n; stop printing object Model id:0 copy 0\n')


def tower():
    return (f'; printing object {NAME} id:1 copy 0\n;TYPE:Outer wall\n'
            'G0 X49 Y49\nG1 X51 E0.1 F1200\n;TYPE:Sparse infill\nG1 Y51 E0.1\n'
            f'; stop printing object {NAME} id:1 copy 0\n')


def test_place_can_use_gap_between_two_brims_in_one_native_block():
    baseline=trace(HEAD+brims()+model(),diameters=[1.75]*2,tower_names={NAME})
    assert baseline.phases[0].blocks[0].bounds==[5,5,95,95]
    assert place({},baseline,options(),BED)==[50,50]
    auto=place({},baseline,options(None),BED)
    assert 20<auto[0]<80


def test_native_audit_uses_the_same_refined_occupancy_for_distant_brims():
    before=trace(HEAD+brims()+model(),diameters=[1.75]*2,tower_names={NAME})
    after=trace(HEAD+tower()+brims()+model(),diameters=[1.75]*2,tower_names={NAME})
    plan={'rows':[{'z':.2,'regions':[{'tool':0,'purpose':'structural',
                                   'target_mm3':0,'density':10}]}]}
    audit=audit_candidate(before,after,plan,options())
    assert audit['passed'],audit['failures']
    bad=trace(HEAD+tower()+brims().replace('X5 Y5','X50 Y5')+model(),
              diameters=[1.75]*2,tower_names={NAME})
    rejected=audit_candidate(before,bad,plan,options())
    assert not rejected['passed']
    assert 'native_deposition_footprint_conflict' in rejected['failures']


def one_motion(start,command,*,before='',width=.5):
    text=HEAD+f';WIDTH:{width}\n; printing object Model id:0 copy 0\n;TYPE:Outer wall\n'
    text+=f'G0 X{start[0]} Y{start[1]}\n'+before+command+' F1200\n'
    return trace(text,diameters=[1.75]).phases[0].blocks[0]


def test_native_width_expands_the_bead_on_both_sides():
    block=one_motion((10,50),'G1 X90 Y50 E1',width=2)
    assert block.footprint.intersects_box([49,50.85,51,50.95])
    assert not block.footprint.intersects_box([49,52,51,53])


def test_commanded_flow_increase_expands_but_reduced_flow_does_not_shrink_occupancy():
    more=one_motion((10,50),'G1 X90 Y50 E1',width=1,before='M221 S200\n')
    assert more.footprint.intersects_box([49,50.85,51,50.95])
    less=one_motion((10,50),'G1 X90 Y50 E1',width=1,before='M221 S50\n')
    assert less.footprint.intersects_box([49,50.4,51,50.45])


def test_circle_sweep_keeps_the_core_free_and_contains_its_bead():
    block=one_motion((70,50),'G3 I-20 J0 E1')
    assert block.bounds==pytest.approx([30,30,70,70])
    assert not block.footprint.intersects_box([48,48,52,52])
    for x,y in [(70,50),(50,70),(30,50),(50,30)]:
        assert block.footprint.intersects_box([x-.01,y-.01,x+.01,y+.01])


def test_tiny_large_radius_arc_has_no_occupancy_in_the_rest_of_its_circle():
    block=one_motion((100,100),'G3 X100.001 Y100 I0.0005 J1000 E1')
    assert block.footprint.intersects_box([99.9,99.9,100.1,100.1])
    assert not block.footprint.intersects_box([90,90,91,91])


def test_partial_repayment_only_deposits_the_final_fraction_of_a_line():
    block=one_motion((0,10),'G1 X100 Y10 E1',before='G1 E-0.75\n')
    assert not block.footprint.intersects_box([10,9.9,20,10.1])
    assert block.footprint.intersects_box([85,9.9,90,10.1])
    assert block.volume_mm3==pytest.approx(.25*math.pi*(1.75/2)**2)


def test_partial_repayment_trims_the_arc_sweep_before_rasterizing():
    block=one_motion((70,50),'G3 X30 Y50 I-20 J0 E1',before='G1 E-0.5\n')
    assert not block.footprint.intersects_box([64,64,65,65])
    assert block.footprint.intersects_box([35,64,36,65])
    assert block.volume_mm3==pytest.approx(.5*math.pi*(1.75/2)**2)


def test_travel_and_fully_repaid_retract_are_never_occupancy():
    block=one_motion((0,0),'G1 X100 Y100 E1\nG0 X0 Y100\nG1 X10 E.2',
                     before='G1 E-1\n')
    assert not block.footprint.intersects_box([45,45,55,55])
    assert block.footprint.intersects_box([2,99.9,5,100.1])


def test_brim_ownership_uses_only_the_deposited_bead_after_repayment():
    text=HEAD+';TYPE:Brim\nG0 X0 Y10\nG1 E-0.75\nG1 X100 Y10 E1 F1200\n'
    result=trace(text,diameters=[1.75],tower_names={NAME},brim_boxes={NAME:[74,9,101,11]})
    assert result.phases[0].blocks[0].name==NAME


def test_brim_ownership_requires_the_entire_bead_inside_the_declared_box():
    text=HEAD+';WIDTH:2\n;TYPE:Brim\nG0 X10 Y10\nG1 X12 Y10 E1 F1200\n'
    result=trace(text,diameters=[1.75],tower_names={NAME},brim_boxes={NAME:[9.9,9.9,12.1,10.1]})
    assert result.phases[0].blocks[0].name=='__NATIVE_EXTERNAL_BRIM__'


@pytest.mark.parametrize('clockwise',[False,True])
def test_arc_occupancy_contains_independent_analytic_samples_including_bead_edges(clockwise):
    # Same endpoints give a 60 degree CCW minor sweep or 300 degree CW major.
    angle=-math.pi/6
    start=(50+20*math.cos(angle),50+20*math.sin(angle))
    endpoint=(start[0],60)
    command=f"{'G2' if clockwise else 'G3'} X{endpoint[0]} Y60 I{-20*math.cos(angle)} J10 E1"
    block=one_motion(start,command)
    sweep=-5*math.pi/3 if clockwise else math.pi/3
    for i in range(201):
        a=angle+sweep*i/200
        for r in [19.75,20,20.25]:
            x,y=50+r*math.cos(a),50+r*math.sin(a)
            assert block.footprint.intersects_box([x,y,x,y]),(i,r,clockwise)


@pytest.mark.parametrize('width',['nan','inf','0','-0.1','100000'])
def test_invalid_or_unbounded_native_bead_width_is_rejected(width):
    with pytest.raises(ValueError,match='width|WIDTH'):
        one_motion((0,0),'G1 X1 E1',width=width)


def test_explicit_footprint_domain_bound_rejects_absurd_motion():
    with pytest.raises(ValueError,match='footprint|occupancy'):
        one_motion((0,0),'G1 X100000 Y100000 E1')


def test_sparse_index_rejects_excessive_disconnected_intervals():
    footprint=Footprint()
    footprint.max_intervals=2
    footprint.add_line((0,10.25),(.1,10.25),.01)
    footprint.add_line((2,10.25),(2.1,10.25),.01)
    with pytest.raises(ValueError,match='finite occupancy interval bound'):
        footprint.add_line((4,10.25),(4.1,10.25),.01)


def test_missing_path_occupancy_is_not_replaced_by_bbox_inference():
    result=trace(HEAD+brims()+model(),diameters=[1.75]*2,tower_names={NAME})
    result.phases[0].blocks[0].footprint=Footprint()
    with pytest.raises(ValueError,match='Missing commanded deposition occupancy'):
        place({},result,options(),BED)


def test_native_reader_does_not_promote_synthetic_width_fallback(tmp_path):
    source=tmp_path/'technical.gcode'
    project=SimpleNamespace(config={'filament_diameter':[1.75]})
    source.write_text(HEAD+model())
    assert read_trace(source,project).fallback_width_depositions==0
    source.write_text((HEAD+model()).replace(';WIDTH:0.5\n',''))
    with pytest.raises(ValueError,match='WIDTH.*occupancy'):
        read_trace(source,project)
    assert trace(source.read_text(),diameters=[1.75]).fallback_width_depositions==1
