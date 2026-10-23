"""Compatibility alias preserving REST adapter helper monkeypatches."""
import sys
from .adapters.http import rest as _implementation
sys.modules[__name__] = _implementation
