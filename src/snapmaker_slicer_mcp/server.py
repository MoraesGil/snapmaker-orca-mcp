"""MCP transport exposes the same native operation as the CLI."""
import asyncio
import json

from mcp.server.fastmcp import FastMCP
from pydantic import StrictFloat, StrictInt

from .service import Slicer
from .editor import Editor


def build_server(slicer: Slicer, preset_dirs=(), *, plugins=()) -> FastMCP:
    mcp = FastMCP("Snapmaker Slicer MCP", instructions=(
        "Slice complete saved 3MFs with the official Snapmaker engine. Editing tools only change "
        "private working revisions; preserve source files and installed/canonical _GENERIC presets. "
        "External presets and unfamiliar high-flow/high-speed materials are comparison inputs. "
        "Present comparisons and ask the user before adopting changes to established material settings. "
        "Preset mutations affect artifact drafts only. Re-slice after every project edit. "
        "No GUI, printer control or automatic engine updates."
    ))
    editor = Editor(slicer, preset_dirs)

    @mcp.tool()
    async def load_model(path: str) -> dict:
        """Load a complete saved 3MF into a private, versioned editing workspace.

        Returns project_id, revision sha256 and working path. The input and
        installed presets are read-only. Use project_id for the editing tools.
        """
        return await asyncio.to_thread(editor.load_model, path)

    @mcp.tool()
    async def get_status(project_id: str) -> dict:
        """Describe the working project, selected plate, objects and revision."""
        return await asyncio.to_thread(editor.get_status, project_id)

    @mcp.tool()
    async def get_config(project_id: str, keys: list[str] | None = None) -> dict:
        """Read saved project settings (optionally selected keys), with secrets redacted."""
        return await asyncio.to_thread(editor.get_config, project_id, keys)

    @mcp.tool()
    async def set_config(project_id: str, changes: dict, expected_sha256: str | None = None,
                         allow_custom_gcode: bool = False) -> dict:
        """Edit project settings and return a new 3MF revision; never edits global presets.

        Pass the last sha256 to reject stale edits. Filament arrays retain their
        slot count/order. Custom G-code requires allow_custom_gcode; external
        post-processing scripts and transport credentials are unsupported.
        """
        return await asyncio.to_thread(editor.set_config, project_id, changes, expected_sha256, allow_custom_gcode)

    @mcp.tool()
    async def edit_project(project_id: str, operations: list[dict], expected_sha256: str | None = None,
                             allow_custom_gcode: bool = False) -> dict:
        """Apply several edits atomically, each with an operation name and its arguments.

        Supports set_config, set_object_config, add_modifier, duplicate/delete/transform_object,
        add/delete_plate, move_object_to_plate, arrange_plate, auto_orient,
        set_height_range, set_layer_height and reset_layer_height. Failure leaves
        the current revision unchanged; every successful edit needs a fresh slice.
        """
        return await asyncio.to_thread(editor.edit_project, project_id, operations, expected_sha256, allow_custom_gcode)

    async def operation(project_id, operation_name, expected_sha256=None, **values):
        return await asyncio.to_thread(editor.edit_project, project_id,
                                       [{'operation': operation_name, **values}], expected_sha256)

    @mcp.tool()
    async def list_objects(project_id: str) -> dict:
        """List stable root object IDs, parts, instance ordinals and transforms."""
        return await asyncio.to_thread(editor.list_objects, project_id)

    @mcp.tool()
    async def set_object_config(project_id: str, object_id: int, changes: dict,
                                part_id: int | None = None, expected_sha256: str | None = None) -> dict:
        """Edit object/part overrides. extruder=0 inherits; 1..N selects a filament slot."""
        return await operation(project_id, 'set_object_config', expected_sha256,
                               object_id=object_id, part_id=part_id, changes=changes)

    @mcp.tool()
    async def add_modifier(project_id: str, object_id: StrictInt, center: list[StrictFloat], dimensions: list[StrictFloat],
                           changes: dict, name: str = "Modifier", expected_sha256: str | None = None) -> dict:
        """Add a box centered at absolute object-local XYZ mm before instance transforms.

        Affects all instances. Dimensions are positive XYZ mm. Supports regional
        outer_wall_speed, inner_wall_speed, sparse_infill_speed, top_surface_speed
        and internal_solid_infill_speed overrides as explicit arrays (e.g. [20]).
        Re-slice after editing.
        """
        return await operation(project_id, 'add_modifier', expected_sha256, object_id=object_id,
                               center=center, dimensions=dimensions, changes=changes, name=name)

    @mcp.tool()
    async def duplicate_object(project_id: str, object_id: int, expected_sha256: str | None = None) -> dict:
        """Duplicate a complete object while preserving its component meshes and painting."""
        return await operation(project_id, 'duplicate_object', expected_sha256, object_id=object_id)

    @mcp.tool()
    async def delete_object(project_id: str, object_id: int, expected_sha256: str | None = None) -> dict:
        """Remove the specified object from the working copy; preserves the input project."""
        return await operation(project_id, 'delete_object', expected_sha256, object_id=object_id)

    @mcp.tool()
    async def transform_object(project_id: str, object_id: int, translate: list[float] | None = None,
                               rotate: list[float] | None = None, scale: list[float] | None = None,
                               instance_id: int | None = None, expected_sha256: str | None = None) -> dict:
        """Apply XYZ translation deltas in mm, rotation deltas in degrees and scale factors.

        Rotation/scaling preserve the instance pivot. Omit instance_id to edit
        every instance of the object. Original component meshes remain intact.
        """
        return await operation(project_id, 'transform_object', expected_sha256, object_id=object_id,
                               translate=translate, rotate=rotate, scale=scale, instance_id=instance_id)

    @mcp.tool()
    async def arrange_plate(project_id: str, plate: int | None = None, expected_sha256: str | None = None) -> dict:
        """Arrange a plate with the native engine; 0 reorganizes all unlocked plates.

        All-plate arrangement persists new plate counts and memberships.
        A single-plate arrangement must fit that plate; overflow fails atomically.
        """
        values = {} if plate is None else {'plate': plate}
        return await operation(project_id, 'arrange_plate', expected_sha256, **values)

    @mcp.tool()
    async def auto_orient(project_id: str, plate: int | None = None, expected_sha256: str | None = None) -> dict:
        """Orient selected/explicit plate objects; 0 selects all unlocked plates."""
        values = {} if plate is None else {'plate': plate}
        return await operation(project_id, 'auto_orient', expected_sha256, **values)

    @mcp.tool()
    async def list_plates(project_id: str) -> dict:
        """List 1-based plates and their object/instance memberships."""
        return await asyncio.to_thread(editor.list_plates, project_id)

    @mcp.tool()
    async def add_plate(project_id: str, name: str = '', expected_sha256: str | None = None) -> dict:
        """Add an empty plate to the working project."""
        return await operation(project_id, 'add_plate', expected_sha256, name=name)

    @mcp.tool()
    async def select_plate(project_id: str, plate: int) -> dict:
        """Select the default target for subsequent editing and slice_project calls."""
        return await asyncio.to_thread(editor.select_plate, project_id, plate)

    @mcp.tool()
    async def delete_plate(project_id: str, plate: int, expected_sha256: str | None = None) -> dict:
        """Remove a working plate, parking its objects off-bed; retain at least one plate.

        Geometry is preserved. Use move_object_to_plate to restore parked objects.
        """
        return await operation(project_id, 'delete_plate', expected_sha256, plate=plate)

    @mcp.tool()
    async def move_object_to_plate(project_id: str, object_id: int, plate: int,
                                    instance_id: int | None = None, expected_sha256: str | None = None) -> dict:
        """Move an object's instances to a 1-based plate, preserving their local offsets."""
        return await operation(project_id, 'move_object_to_plate', expected_sha256,
                               object_id=object_id, plate=plate, instance_id=instance_id)

    @mcp.tool()
    async def set_layer_height(project_id: str, object_id: int, mode: str, quality: float = 0.5,
                                expected_sha256: str | None = None) -> dict:
        """Generate adaptive layers (quality 0=finer..1=coarser); reset clears profiles and Z bands."""
        return await operation(project_id, 'set_layer_height', expected_sha256,
                               object_id=object_id, mode=mode, quality=quality)

    @mcp.tool()
    async def set_height_range(project_id: str, object_id: int, min_z: float | None = None,
                                max_z: float | None = None, layer_height: float | None = None,
                                clear: bool = False, expected_sha256: str | None = None) -> dict:
        """Set/update a Z-band's layer height in mm, relative to the object; clear removes bands."""
        return await operation(project_id, 'set_height_range', expected_sha256, object_id=object_id,
                               min_z=min_z, max_z=max_z, layer_height=layer_height, clear=clear)

    @mcp.tool()
    async def list_presets(type: str | None = None, include_system: bool = False) -> dict:
        """List declared read-only preset sources and working drafts; no global writes."""
        return await asyncio.to_thread(editor.catalog.list_presets, type, include_system)

    @mcp.tool()
    async def get_preset_config(type: str, name: str) -> dict:
        """Resolve a named preset's inheritance with hashes/provenance; redact credentials."""
        return await asyncio.to_thread(editor.catalog.get_preset_config, type, name)

    @mcp.tool()
    async def select_preset(project_id: str, type: str, name: str, filament_slot: int = 0,
                             flow_mode: str = 'standard', expected_sha256: str | None = None) -> dict:
        """Apply a resolved preset to the working project; filament_slot is 0-based.

        Explicit flow_mode selects standard/high_flow values where supported.
        Installed and canonical presets are never changed.
        """
        return await asyncio.to_thread(editor.select_preset, project_id, type, name,
                                       filament_slot, flow_mode, expected_sha256)

    @mcp.tool()
    async def save_preset(project_id: str, type: str, name: str, filament_slot: int = 0,
                           detach: bool = False, allow_custom_gcode: bool = False) -> dict:
        """Save current working settings as an artifact draft, never over an installed preset."""
        return await asyncio.to_thread(editor.save_preset, project_id, type, name, filament_slot, detach, allow_custom_gcode)

    @mcp.tool()
    async def edit_preset(type: str, name: str, changes: dict, allow_custom_gcode: bool = False) -> dict:
        """Edit an artifact draft only. Installed/canonical presets remain read-only."""
        return await asyncio.to_thread(editor.catalog.edit_preset, type, name, changes, allow_custom_gcode)

    @mcp.tool()
    async def rename_preset(type: str, name: str, new_name: str) -> dict:
        """Rename an artifact draft only; cannot rename installed/canonical presets."""
        return await asyncio.to_thread(editor.catalog.rename_preset, type, name, new_name)

    @mcp.tool()
    async def delete_preset(type: str, name: str) -> dict:
        """Delete an artifact draft only; cannot remove installed/canonical presets."""
        return await asyncio.to_thread(editor.catalog.delete_preset, type, name)

    @mcp.tool()
    def describe_setting(key: str) -> dict:
        """Describe a setting from the Snapmaker source catalog, including schema version."""
        return editor.catalog.describe_setting(key)

    @mcp.tool()
    def search_settings(query: str, limit: int = 25) -> dict:
        """Search Snapmaker setting names, labels and descriptions."""
        return editor.catalog.search_settings(query, limit)

    @mcp.tool()
    async def slice_project(project_id: str, plate: int | None = None, force: bool = False,
                             allow_newer_file: bool = True) -> dict:
        """Slice the current working revision; omitted plate uses the selected plate, 0=all."""
        return await slicer.run_slice(editor.slice_project, project_id, plate, force, allow_newer_file)

    @mcp.tool()
    async def apply_and_slice(project_id: str, changes: dict, plate: int | None = None,
                              expected_sha256: str | None = None) -> dict:
        """Edit working settings and force a fresh official native slice of the new revision."""
        return await slicer.run_slice(editor.apply_and_slice, project_id, changes, plate, expected_sha256)

    @mcp.tool()
    async def slice_3mf(input_3mf: str, plate: int = 0, force: bool = False, allow_newer_file: bool = True) -> dict:
        """Slice the saved 3MF using the bound official native engine. 0=all plates.

        Returns G-code paths/hashes, original source, settings and inspection
        resource URIs. force bypasses verified cache. allow_newer_file explicitly
        bypasses the native legacy producer-version gate and is recorded in the
        result/cache identity; disable it for strict version checking.
        No GUI or printer access.
        """
        return await slicer.run_slice(slicer.slice_3mf, input_3mf, plate, force, allow_newer_file)

    @mcp.tool()
    async def recover_3mf(gcode_file: str) -> dict:
        """Recover a preserved original for a known generated G-code hash.

        This does not reconstruct a mesh from G-code. Unknown/ambiguous sources
        fail explicitly; recovered source bytes are checked against their hash.
        """
        return await asyncio.to_thread(slicer.recover_3mf, gcode_file)

    @mcp.tool()
    async def engine_status(check_updates: bool = True) -> dict:
        """Report exact engine identity and optionally check upstream main.

        New commits produce an update/rebuild notice. Never downloads, compiles,
        switches or replaces the native engine.
        """
        return await asyncio.to_thread(slicer.engine.status, check_updates)

    @mcp.resource("slicer://jobs/{job_id}/manifest")
    def manifest(job_id: str) -> str:
        return json.dumps(slicer.job(job_id), ensure_ascii=False)

    @mcp.resource("slicer://jobs/{job_id}/props")
    def props(job_id: str) -> str:
        slicer.job(job_id)
        return (slicer.root / "runs" / job_id / "props.json").read_text()

    @mcp.resource("slicer://jobs/{job_id}/plates/{plate}/lines/{start}/{count}")
    def gcode_lines(job_id: str, plate: int, start: int, count: int) -> str:
        return json.dumps(slicer.read_lines(job_id, int(plate), int(start), int(count)), ensure_ascii=False)

    from .plugins import register_plugins
    register_plugins(plugins, mcp, editor, slicer)
    return mcp
