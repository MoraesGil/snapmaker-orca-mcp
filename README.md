![Give your AI a Snapmaker slicer. 3MF → G-code. No GUI. Conceptual illustration of an AI agent, a 3MF build plate and colored toolpaths.](docs/assets/hero.png)

# Snapmaker Orca MCP

**Give your AI a Snapmaker slicer.**

Headless 3MF slicing and project editing for AI agents, using the **official Snapmaker Orca native engine** through the Model Context Protocol (MCP). Start with a complete saved project; get fresh G-code, configuration JSON, logs and SHA-256 provenance.

**33 tools · Python 3.11+ · stdio + authenticated Streamable HTTP · AGPLv3**

Version **0.3.0** is a standalone community project. The package and executable remain `snapmaker-slicer-mcp`; the Python import is `snapmaker_slicer_mcp`. This project is independently maintained and is not an official Snapmaker product.

## What your agent can do

- **Slice without desktop clicks.** Process one or all printable plates with the bound native engine, without opening a GUI or starting Xvfb/VNC.
- **Edit a project safely.** Change settings and object/part overrides, duplicate or transform geometry, and manage plates in private, versioned 3MF revisions.
- **Prepare plate layouts.** Use native arrangement and orientation, including distribution across plates, plus height ranges and bounded adaptive layers.
- **Inspect the actual output.** Read new G-code, settings and diagnostics; verify hashes and recover the exact preserved source associated with an output.
- **Share one backend.** Connect several trusted agents to one HTTP service, using the same engine, projects and artifact store.

Original projects and installed/canonical presets stay read-only. Preset changes create artifact-local drafts.

## Five-minute quickstart

**Prerequisites:** Python 3.11+, [uv](https://docs.astral.sh/uv/), a complete saved 3MF, and an already obtained official native engine with its matching libraries/resources. Engine acquisition is separate from the Python installation; see [Linux/Docker setup](LINUX.md).

The validated Linux binding and adaptive adapter use official main commit `9bf2f18eaff7cc0655cbdeef6a215660085ecb0f`. Keep the engine files stable after binding.

```sh
git clone https://github.com/MoraesGil/snapmaker-orca-mcp.git
cd snapmaker-orca-mcp
uv sync --frozen

uv run snapmaker-slicer-mcp --engine /absolute/runtime/engine.json bind-engine \
  --binary /absolute/runtime/squashfs-root/bin/snapmaker-orca \
  --resources /absolute/runtime/squashfs-root/resources \
  --commit 9bf2f18eaff7cc0655cbdeef6a215660085ecb0f

uv run snapmaker-slicer-mcp \
  --engine /absolute/runtime/engine.json --artifacts /absolute/artifacts \
  slice /absolute/models/project.3mf --plate 0 --force
```

Replace the absolute paths with your own. The commands run from this repository's root. Binding records local executable/resource hashes; the supplied source commit is an operator declaration, not a certification inferred from a binary.

### One agent: stdio

Add this entry to your MCP client's configuration, using absolute server-side paths:

```json
{
  "mcpServers": {
    "snapmaker-orca": {
      "command": "/absolute/snapmaker-orca-mcp/.venv/bin/snapmaker-slicer-mcp",
      "args": [
        "--engine", "/absolute/runtime/engine.json",
        "--artifacts", "/absolute/artifacts",
        "--presets", "/absolute/read-only/presets",
        "serve", "--no-update-check"
      ]
    }
  }
}
```

Stdio is the default transport. Installation does not modify global client configuration. Both the model and engine must be readable on the server's filesystem.

### Several agents: one shared HTTP service

Create a private bearer-token file first, then start the service once:

```sh
uv run snapmaker-slicer-mcp \
  --engine /absolute/runtime/engine.json --artifacts /absolute/artifacts \
  --presets /absolute/read-only/presets \
  serve --transport streamable-http --host 127.0.0.1 --port 8080 \
  --auth-token-file /absolute/secrets/mcp-token \
  --allowed-host localhost:8080 --allowed-origin http://localhost:8080 \
  --no-update-check
```

Connect each client to `http://localhost:8080/mcp` with `Authorization: Bearer <your-token>`. Use your client's secret storage. Host/Origin allowlists must match the actual deployment. The shared backend has one filesystem and artifact store; it is intended for trusted clients.

[Linux/Docker deployment](LINUX.md) · [Client and tool examples](docs/usage.md)

## Edit, then slice

These JSON objects are the `params` of MCP `tools/call` requests. Replace IDs/hashes with values returned by the preceding call.

```json
{"name":"load_model","arguments":{"path":"/input/project.3mf"}}
```

```json
{
  "name": "edit_project",
  "arguments": {
    "project_id": "<returned-project-id>",
    "expected_sha256": "<returned-revision-sha256>",
    "operations": [
      {"operation": "set_config", "changes": {"layer_height": 0.16}},
      {"operation": "arrange_plate", "plate": 0}
    ]
  }
}
```

```json
{"name":"slice_project","arguments":{"project_id":"<returned-project-id>","plate":0,"force":true}}
```

An edit invalidates embedded G-code and thumbnails. Failed edit batches do not advance the revision. Use `expected_sha256` to reject stale edits when clients share a project. Fresh slices retain the exact captured revision and return output paths/hashes.

## The 33 tools

| Group | Tools | Purpose |
| --- | --- | --- |
| Load | `load_model` | Create a private project workspace from a saved 3MF. |
| Inspect | `get_status`, `get_config`, `list_objects`, `list_plates` | Read revision, settings, IDs, transforms and plate membership. |
| Settings | `set_config`, `set_object_config`, `edit_project` | Apply project or object/part overrides, individually or atomically. |
| Geometry | `duplicate_object`, `delete_object`, `transform_object` | Edit working geometry while preserving component payloads and painting. |
| Plates | `add_plate`, `select_plate`, `delete_plate`, `move_object_to_plate` | Manage plate membership; deleting an occupied plate parks its objects. |
| Native layout | `arrange_plate`, `auto_orient` | Apply verified native transforms; all-plate arrangement can change plate count. |
| Layers | `set_height_range`, `set_layer_height` | Apply Z bands, generate adaptive layers or reset variable layers. |
| Preset sources | `list_presets`, `get_preset_config`, `select_preset` | Resolve file inheritance and explicitly select a preset/material mode. |
| Preset drafts | `save_preset`, `edit_preset`, `rename_preset`, `delete_preset` | Manage artifact-local drafts; protect installed/canonical names. |
| Setting catalog | `describe_setting`, `search_settings` | Inspect 699 source-derived native settings. |
| Slicing | `slice_3mf`, `slice_project`, `apply_and_slice` | Slice a saved project, a working revision, or an explicit settings edit. |
| Recovery | `recover_3mf` | Retrieve a preserved source by verified G-code association. |
| Engine | `engine_status` | Report identity and optionally check upstream; updates are operator-controlled. |

Three MCP resource templates expose job manifests, properties and bounded G-code line ranges. See [usage details](docs/usage.md#inspect-and-recover-output).

## Validation and limits

- Plate numbers are **1-based**; `plate=0` requests all applicable plates. Filament selection uses **0-based slots**; object `extruder=0` inherits and `1..N` selects a slot.
- Material arrays retain slot order and native flow segments. Explicit `flow_mode="standard"|"high_flow"` changes the chosen slot/mode; draft saves preserve the parent's other variants.
- Adaptive generation is pinned to `9bf2f18` and requires millimeter units, uniform nozzle/layer limits and 100% Z shrinkage compensation. Unsupported configurations fail explicitly.
- External post-processing scripts are rejected. Source recovery depends on retained artifacts; G-code alone cannot recreate meshes, painting or project hierarchy.
- Tests and fresh native slices provide bounded digital evidence. **GUI equivalence, physical print quality and physical color/head matching are not certified.** Settings exports do not exhaustively describe every final per-region override.
- A preview renderer, printer API, automatic print diagnosis and MaxEllis's full diagnose/compare feature set are outside the shipped tools. ForjaFarm is a possible future integrator, not a runtime dependency.

[Validation record](TEST.md) documents what was tested and what remains unverified. Retained slice timings describe particular inputs and environments, not a general performance benchmark.

## Contribute

[Contributing](CONTRIBUTING.md) covers setup, focused tests, native acceptance and reproducible issue reports. Small fixtures you are permitted to publish, clearer documentation and well-scoped compatibility fixes are welcome.

```sh
uv sync --frozen --extra test
uv run pytest tests -q
uv build
```

Licensed under [GNU AGPLv3](LICENSE.txt). Adapted Python helpers credit **Max Ellis**; the native engine, source-derived schema and four public preset fixtures credit **Snapmaker/OrcaSlicer**. See [third-party attribution](THIRD_PARTY.md).
