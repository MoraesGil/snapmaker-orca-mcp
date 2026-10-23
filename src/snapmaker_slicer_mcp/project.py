"""Compatibility identity for the extracted adapter."""
import sys
from .adapters.three_mf import document as _implementation
from .adapters.three_mf.document import (
    _local as _local,
    _children as _children,
    _child as _child,
    _metadata as _metadata,
    _option_config as _option_config,
    _set_metadata as _set_metadata,
    _option_types as _option_types,
    _escape_cstyle as _escape_cstyle,
    _native_option_value as _native_option_value,
    _read_xml as _read_xml,
    _tree_state as _tree_state,
    _xml_bytes as _xml_bytes,
    _transform as _transform,
    compose_transform as compose_transform,
    CORE as CORE,
    PRODUCTION as PRODUCTION,
    CONFIG as CONFIG,
    SETTINGS as SETTINGS,
    RANGES as RANGES,
    PROFILE as PROFILE,
    BRIM as BRIM,
    CUT as CUT,
    CUSTOM_GCODE as CUSTOM_GCODE,
    PARKED as PARKED,
    IDENTITY as IDENTITY,
    Project as Project,
    _XML_LOCK as _XML_LOCK,
)

sys.modules[__name__] = _implementation
