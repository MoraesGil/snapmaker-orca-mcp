# Client and tool usage

Use a complete **saved 3MF**, readable by the server. Keep models and installed/canonical presets read-only; choose a separate writable artifact directory. Engine binding and container setup are in [LINUX.md](../LINUX.md).

## Connect an MCP client

[README.md](../README.md) includes a stdio configuration. Stdio is the default and suits an individual client.

For several agents, start one authenticated Streamable HTTP service and connect each client to its `/mcp` endpoint. Clients that accept this JSON configuration shape can use:

```json
{
  "mcpServers": {
    "snapmaker-orca": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer <token-from-your-private-file>"
      }
    }
  }
}
```

Replace the token placeholder using your client's secret storage; HTTP configuration and variable interpolation vary by client. An MCP client handles initialization and protocol headers. The examples below are `tools/call` parameters, not standalone raw HTTP requests.

Host allowlists contain exact host headers, including the port when present. Origin allowlists contain exact origins including the scheme; a request without an Origin header is accepted. Wildcards are rejected. The bearer token is read from its file at startup; an explicit service restart is needed to rotate it. `GET /health` reports minimal liveness and still enforces the Host/Origin policy.

All authenticated clients share the backend's filesystem, artifact store and project state. Load a separate project workspace for independent work; when sharing a project, pass the latest SHA and explicit plate targets.

## Load and inspect

```json
{"name":"load_model","arguments":{"path":"/input/project.3mf"}}
```

Retain the returned `project_id`, `sha256` and working `path`. Loading copies the saved source into an isolated workspace and records its identity.

```json
{"name":"get_config","arguments":{"project_id":"<project-id>","keys":["layer_height","curr_bed_type","filament_type"]}}
```

Use `list_objects` to obtain actual object/part IDs and instance ordinals, and `list_plates` to inspect membership. Do not infer a physical printer head from a logical filament slot.

## Apply an atomic edit

```json
{
  "name": "edit_project",
  "arguments": {
    "project_id": "<project-id>",
    "expected_sha256": "<latest-revision-sha256>",
    "operations": [
      {"operation": "set_config", "changes": {"layer_height": 0.16}},
      {"operation": "arrange_plate", "plate": 0}
    ]
  }
}
```

Every successful geometry/settings edit returns a new private 3MF revision and invalidates old embedded G-code/thumbnails. A failed batch leaves the current revision unchanged. A stale SHA fails instead of overwriting a newer edit.

`set_object_config` supports a whole object or a specified `part_id`. Its `extruder=0` means inherit; `1..N` selects a logical material slot. `transform_object` uses translation deltas in millimeters, rotation deltas in degrees and scale factors, retaining the instance pivot.

## Plates and variable layers

- Plate numbers are **1-based**. An omitted plate generally uses the selected plate; `plate=0` applies to all applicable/unlocked plates.
- `arrange_plate(plate=0)` persists native plate count, membership and transforms. A selected-plate arrangement that needs another plate fails atomically.
- `delete_plate` retains geometry by parking objects off-bed. Restore them with `move_object_to_plate`. The last plate cannot be removed.
- `set_height_range` defines object-relative Z bands in millimeters. Clearing bands preserves an existing continuous profile.
- `set_layer_height(mode="adaptive", quality=0.5)` generates a profile; quality `0` is finest and `1` coarsest. `mode="reset"` clears both the profile and Z bands.

The adaptive adapter is pinned to source `9bf2f18` and requires millimeter units, uniform nozzle/layer limits and 100% Z shrinkage compensation. Unsupported configurations and incompatible engine bindings fail explicitly.

## Preset selection and drafts

`list_presets` and `get_preset_config` expose declared file inheritance, provenance and hashes. `describe_setting` / `search_settings` inspect the source-derived catalog. These are JSON/source views, not GUI normalization or exhaustive per-region settings.

Treat external or unfamiliar high-flow/high-speed profiles as comparison inputs. Confirm adoption with the project owner before explicitly selecting new material values. No automatic diagnose/compare function is shipped.

The following example assumes an operator-supplied, read-only `_GENERIC_PETG` exists in the configured preset roots:

```json
{
  "name": "select_preset",
  "arguments": {
    "project_id": "<project-id>",
    "type": "filament",
    "name": "_GENERIC_PETG",
    "filament_slot": 1,
    "flow_mode": "standard",
    "expected_sha256": "<latest-revision-sha256>"
  }
}
```

`filament_slot` is **0-based**. Explicit `standard` or `high_flow` selects the requested variant in that material, updating its active volume type and preserving unrelated segments/cells. A source with no requested mode uses its native first-value fallback, reported in the selection trace.

Saved native flow-step sizes must be string arrays, for example `["2","1","1","1"]`. That layout has four material slots and five cells in each of the 19 source-defined flow-variant vectors; nonvariant material vectors have four cells. Selection/save reject numeric or mixed saved step arrays. API `set_config` accepts numeric values and serializes them to native strings.

If a project lacks a touched vector, selection may fill unrelated cells from a validated primitive native default when source/schema and engine commits match. `native_defaults_applied` records the key, default, cardinality and source. Existing malformed vectors, nullable defaults, C++ expressions and mismatched bindings fail explicitly.

`save_preset` creates a draft under `<artifacts>/preset-drafts`. A filament save uses the active variant and preserves the parent's other variants, including native single-value padding. `edit_preset`, `rename_preset` and `delete_preset` operate only on such drafts. Existing installed/canonical names are protected.

## Slice the captured revision

```json
{"name":"slice_project","arguments":{"project_id":"<project-id>","plate":0,"force":true}}
```

`slice_3mf` slices an exact saved project directly; `slice_project` slices the captured working revision. `apply_and_slice` edits settings then forces a fresh slice. If its slice fails, a successful preceding edit remains available for inspection.

Each job retains `source.3mf`, `plate_N.gcode`, `native-settings.json`, `props.json`, request/result manifests and native diagnostics. Results include hashes, paths and timing. Global/per-plate settings exports do not exhaustively describe final per-region overrides.

Native execution is serialized within one service. Project-state reads can continue while a captured revision is slicing. A later edit does not change that in-flight source.

## Inspect and recover output

Three resource templates expose:

```text
slicer://jobs/{job_id}/manifest
slicer://jobs/{job_id}/props
slicer://jobs/{job_id}/plates/{plate}/lines/{start}/{count}
```

G-code lines are **1-based**, with `count` limited to `1..1000`. Full files remain at their returned artifact paths.

```json
{"name":"recover_3mf","arguments":{"gcode_file":"/artifacts/runs/<job-id>/plate_1.gcode"}}
```

Recovery verifies the G-code/source association and returns the exact retained 3MF. Unknown, missing or ambiguous associations fail. It does not reconstruct meshes, painting or hierarchy from standalone G-code.

## Cache, compatibility and engine identity

`force=true` bypasses verified cache. A cached result is reused only when input bytes, plate/compatibility options and engine identity match and retained output/source/settings hashes pass. Cached native timing belongs to the original slice; request timing describes the current call.

`allow_newer_file=true` passes the official native compatibility option to bypass its legacy producer-version gate. It does not modify print settings or guarantee support for future formats. Use `false`, or CLI `slice --strict-file-version`, for strict checking. External post-processing scripts are rejected.

`engine_status(check_updates=false)` reads local identity without an upstream query. Startup normally checks upstream once; `serve --no-update-check` disables that check. A notice never downloads or replaces an engine. The default native timeout is 1800 seconds; global `--timeout` changes it.

## Direct CLI

Global options precede the subcommand:

```sh
uv run snapmaker-slicer-mcp --engine /absolute/runtime/engine.json \
  --artifacts /absolute/artifacts slice /absolute/models/project.3mf --plate 0 --force

uv run snapmaker-slicer-mcp --engine /absolute/runtime/engine.json \
  --artifacts /absolute/artifacts recover /absolute/artifacts/runs/JOB/plate_1.gcode

uv run snapmaker-slicer-mcp --engine /absolute/runtime/engine.json status
```

Successful CLI output is JSON; native diagnostics do not contaminate MCP stdout. See [TEST.md](../TEST.md) for platform, GUI-equivalence and physical-print limits.
