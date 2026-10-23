"""Compatibility identity for the extracted adapter."""
import sys
from .adapters.orca import layout as _implementation
from .adapters.orca.layout import (
    Engine as Engine,
    file_hash as file_hash,
    _XML_LOCK as _XML_LOCK,
    _native_xml as _native_xml,
    _matrix as _matrix,
    _point as _point,
    _compose as _compose,
    _metadata as _metadata,
    _Geometry as _Geometry,
    _read_stl as _read_stl,
    _subtract as _subtract,
    _dot as _dot,
    _cross as _cross,
    _tolerance as _tolerance,
    _verify_baseline as _verify_baseline,
    _recover as _recover,
    _write_audit as _write_audit,
    _layout_state as _layout_state,
    _plate_origin as _plate_origin,
    _plate_frame as _plate_frame,
    _fit_plate as _fit_plate,
    _run as _run,
    native_deltas as native_deltas,
    _f32 as _f32,
    _number as _number,
    _numbers as _numbers,
    _height_ranges as _height_ranges,
    adaptive_layer_profile as adaptive_layer_profile,
)

sys.modules[__name__] = _implementation
