"""Explicitly enabled MCP extensions. Importing the server loads no plugins."""


def register_plugins(names, mcp, editor, slicer):
    """Only named, bundled plugins run; arbitrary imports are not accepted."""
    names = tuple(names)
    if len(set(names)) != len(names) or set(names) - {'custom-tower'}:
        raise ValueError('Supported plugin: custom-tower (enable once)')
    if 'custom-tower' in names:
        from .custom_tower import register
        register(mcp, editor, slicer)
