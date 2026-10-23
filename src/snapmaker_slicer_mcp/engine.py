"""Compatibility import for the official native-engine adapter."""
import sys

from .adapters.orca import engine as _implementation
from .adapters.orca.engine import (
    Engine as Engine,
    file_hash as file_hash,
    package_resources as package_resources,
    tree_hash as tree_hash,
)

sys.modules[__name__] = _implementation
