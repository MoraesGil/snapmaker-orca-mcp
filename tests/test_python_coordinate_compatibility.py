"""Preserve supported Python coordinate forms at the JSON boundary."""
import pytest

from snapmaker_slicer_mcp.presets import validate_changes


def test_tuple_point_in_vector_normalizes_without_mutating_input():
    changes = {'extruder_offset': [(1.0, 2.0)]}
    assert validate_changes(changes, {'extruder_offset': ['0x0']}) == {'extruder_offset': ['1.0x2.0']}
    assert changes == {'extruder_offset': [(1.0, 2.0)]}


def test_tuple_vector_is_still_rejected():
    with pytest.raises(ValueError):
        validate_changes({'extruder_offset': ((1.0, 2.0),)}, {'extruder_offset': ['0x0']})


def test_non_json_non_coordinate_value_is_still_rejected():
    with pytest.raises(ValueError):
        validate_changes({'layer_height': object()}, {'layer_height': '0.2'})
