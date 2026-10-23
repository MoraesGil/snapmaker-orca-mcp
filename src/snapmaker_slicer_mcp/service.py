"""Stable public imports for the composed native slicing service."""
# Preserve the public Python API and its subprocess boundary used by clients.
import subprocess as subprocess

from .adapters.orca.slicer import Slicer as Slicer
from .adapters.storage.artifacts import (
    gcode_properties as gcode_properties,
    validate_project as validate_project,
    write_json as write_json,
)
