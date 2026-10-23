"""Bundled opt-in add_custom_tower MCP plugin."""
import asyncio

from .contracts import TowerOptions
from .workflow import CustomTowerPlugin


def register(mcp, editor, slicer):
    plugin=CustomTowerPlugin(editor,slicer)

    @mcp.tool()
    async def add_custom_tower(project_id: str, plate: int, options: TowerOptions,
                               expected_sha256: str) -> dict:
        """Build/recalculate a signed custom tower from a fresh tower-free native slice.

        Uses private revisions and shared native admission. Disables native tower,
        preserves source geometry/painting/presets and publishes only after a fresh
        complete tower-first event audit. Existing signed auxiliaries are excluded;
        a real model named tower is retained. No printer access or G-code rewriting.
        Structure grows at minimum infill without a toolchange; priming budgets are
        applied only immediately after real changes. Physical cleaning is unproven.
        """
        return await slicer.run_slice(plugin.add,project_id,plate,options,expected_sha256)

    @mcp.tool()
    async def custom_tower_status(project_id: str) -> dict:
        """Report signed auxiliaries and whether a model/config edit invalidated their audit."""
        return await asyncio.to_thread(plugin.status,project_id)
