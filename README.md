# Snapmaker Slicer MCP

A small MCP server and CLI over the official Snapmaker Orca native slicing engine. Give an agent a saved 3MF; receive fresh G-code per plate, configuration JSON and hashes. Optionally edit a private working revision before slicing. Python coordinates the process; the official C++ engine does the slicing.

There is no previewer in this scope. The wrapper invokes native processing without opening the desktop UI or running Xvfb/VNC. It never sends printer commands. This is a community prototype in a fork, not an official Snapmaker product.

Version 0.2.0 exposes 33 MCP tools. For a packaged Linux installation, follow [LINUX.md](LINUX.md); the Python layer and official engine are versioned independently. Upgrading this fork does not silently replace the bound engine or installed user presets.

## Install and bind an engine

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required for the following commands. From the repository root:

```sh
uv sync --project tools/slicer_mcp
uv run --project tools/slicer_mcp snapmaker-slicer-mcp --help
```

Obtain a native build of **Snapmaker/OrcaSlicer main**, keeping its matching libraries and resources. Use the exact source commit of that build. Binding measures local hashes; the commit is an operator declaration, not inferred or certified from the executable. Release and feature-branch builds must not be labeled as main.

Example for a standard macOS bundle:

```sh
uv run --project tools/slicer_mcp snapmaker-slicer-mcp \
  --engine /absolute/runtime/engine.json bind-engine \
  --binary '/absolute/runtime/Snapmaker Orca.app/Contents/MacOS/Snapmaker_Orca' \
  --resources '/absolute/runtime/Snapmaker Orca.app/Contents/Resources' \
  --commit ACTUAL_40_CHARACTER_MAIN_BUILD_COMMIT
```

Standard Linux `bin/<executable>` + `resources/` layouts (including the upstream extra build-directory fallback) and Windows adjacent `resources/` are recognized. Custom Linux FHS resource paths are not supported. Native slicing has been validated on macOS and in Ubuntu 24.04 amd64 with the official main package. Windows remains unvalidated. A Linux server needs the required shared libraries; see the tested [Docker recipe](LINUX.md). The MCP does not build or install the engine.

Keep the bound engine files stable while serving. Hashes are checked at startup; file identities, sizes and modification/change times are checked around cache reuse and native execution. A changed engine fails until explicitly bound and restarted. Declared resources must match the directory the native executable actually resolves.

## Use from an agent

MCP stdio configuration; replace every example path with an absolute path:

```json
{
  "mcpServers": {
    "snapmaker-slicer": {
      "command": "/absolute/OrcaSlicer-MCP/tools/slicer_mcp/.venv/bin/snapmaker-slicer-mcp",
      "args": [
        "--engine", "/absolute/runtime/engine.json",
        "--artifacts", "/absolute/artifacts",
        "--presets", "/absolute/read-only/presets",
        "serve"
      ]
    }
  }
}
```

Both the engine and the 3MF must be readable on the MCP server's filesystem. No global client configuration is modified by installation.

| Tool | Contract |
| --- | --- |
| `slice_3mf(input_3mf, plate=0, force=false, allow_newer_file=true)` | Slice one exact saved project. `0` requests all printable plates; positive plate numbers are 1-based. Returns paths, hashes, timings and inspection resources. |
| `recover_3mf(gcode_file)` | Recover an intact stored source associated with the supplied G-code hash; fail if absent or ambiguous. |
| `engine_status(check_updates=true)` | Show the bound engine and check upstream main. New commits produce an update/rebuild notice only. |

## Edit a project before slicing

`load_model(path)` accepts a saved, complete 3MF and returns a `project_id`, working path and revision SHA-256. The source is copied intact. Each successful edit produces a new working 3MF under the artifact directory; installed presets and the input file are read-only. Supply `expected_sha256` on edits when multiple callers might otherwise overwrite each other's work. Failed batches leave the current revision unchanged.

| Tools | Working-copy behavior |
| --- | --- |
| `get_status`, `get_config`, `list_objects`, `list_plates` | Inspect the revision, settings, stable object/part IDs, instance transforms and plate membership. |
| `set_config`, `set_object_config` | Change global or object/part overrides. Filament arrays keep their native slot order and flow-segment cardinality. `extruder=0` inherits; `1..N` selects a slot. |
| `duplicate_object`, `delete_object`, `transform_object` | Duplicate/remove objects or change XYZ translation, rotation and scale while retaining original component meshes and painting. |
| `add_plate`, `select_plate`, `delete_plate`, `move_object_to_plate` | Manage 1-based plate numbers. Deleting an occupied plate parks its objects off-bed without deleting geometry. Move them back explicitly. The last plate cannot be deleted. |
| `arrange_plate`, `auto_orient` | Ask the bound native engine for placement/orientation and apply verified transforms. `arrange_plate(plate=0)` also persists the resulting plate count and membership. Locked plates remain protected. |
| `set_height_range`, `set_layer_height` | Set Z-band layer heights, generate an adaptive profile, or reset variable layers. Unsupported adaptive configurations fail explicitly. |
| `edit_project` | Apply several operations as one atomic revision. |
| `slice_project`, `apply_and_slice` | Slice the selected/explicit plate of the current revision, or edit settings and force a fresh slice. `plate=0` requests all printable plates. |
| `list_presets`, `get_preset_config`, `select_preset` | Inspect declared preset sources/inheritance and explicitly apply a preset to the working project. Filament slot numbers are 0-based. |
| `save_preset`, `edit_preset`, `rename_preset`, `delete_preset` | Manage artifact drafts only. Existing installed/canonical preset names cannot be overwritten. |
| `describe_setting`, `search_settings` | Inspect the setting catalog extracted from the Snapmaker source, with its source and engine versions. |

Example MCP sequence:

```text
load_model(path="/models/project.3mf")
get_config(project_id="<returned id>", keys=["layer_height", "curr_bed_type"])
set_config(project_id="<id>", changes={"layer_height": 0.16}, expected_sha256="<last sha256>")
arrange_plate(project_id="<id>")
slice_project(project_id="<id>", plate=0, force=true)
```

After an edit, embedded G-code and rendered preview artifacts are invalidated. The edited 3MF is available at its returned path and can be opened in the official slicer. A slice's preserved source is that exact working revision. `apply_and_slice` keeps a successful edit even if the subsequent native slice fails, so the revision and diagnostics remain inspectable.

Automatic edits default to the selected plate. A selected-plate arrangement that needs another plate fails without committing; use `arrange_plate(plate=0)` for automatic distribution across plates. Resetting layer heights clears both adaptive profiles and Z-band overrides. Clearing only Z bands preserves an existing continuous profile.

Repeat global `--presets PATH` for each additional read-only preset root. Nothing imports or overwrites `_GENERIC` profiles automatically. Their temperatures, material settings and cooling remain as saved unless a caller explicitly requests a change to its working project. External or unfamiliar high-flow/high-speed profiles are comparison inputs, not automatic replacements. Confirm adoption of proposed values with the user. Only drafts under `<artifacts>/preset-drafts` support preset mutations. Explicit `flow_mode=standard|high_flow` applies the chosen mode in the selected material, using the native segment offset and active volume type. Other material segments and unselected variants stay intact. Saving a filament draft reads its active variant and retains the parent's other flow variants; native single-value padding is respected for declared variants. Native preset scalar/vector compatibility is normalized during explicit selection; ignored compatibility metadata, legacy, transport and identity fields are reported in the result. If an older project lacks a vector, selection can use a validated literal native default from the matching bound source and reports that backfill. Nullable defaults, C++ expressions and defaults from a different engine version fail explicitly.

Flow segment sizes in saved 3MFs must use native string arrays; selection/save reject numeric or mixed arrays that the engine would ignore. Numeric values supplied through `set_config` are serialized to native strings.

Adaptive generation uses the native quality convention: `0` is finest and `1` coarsest. Its supported domain currently requires millimeter units, uniform nozzle/layer limits and 100% Z shrinkage compensation. The generator is pinned to native source `9bf2f18`; a different bound engine requires an explicit MCP adapter update/validation before generating a new adaptive profile. Direct slicing and native arrangement/orientation retain their engine binding.

The working-copy editor does not depend on the MaxEllis GUI Remote API. Its tool design and setting extractor credit [MaxEllis/orcaslicer-mcp](https://github.com/MaxEllis/orcaslicer-mcp); see [THIRD_PARTY.md](THIRD_PARTY.md) for the pinned source and AGPL attribution. The native engine remains official Snapmaker main.

`serve` checks upstream once on startup. Call `engine_status` to check again; use `serve --no-update-check` for offline startup. There is no background updater. A new upstream commit does not change output until an operator supplies, binds and validates its new native build. The new identity automatically separates its cache.

`slice_3mf` returns `cached=true` only when the input bytes, plate selection, compatibility flags and engine identity match and the preserved source, props and all generated G-codes pass their hashes. Use `force=true` for a new slice. A cached result's native timing describes its original slice; request timing describes the present call. Requests to one server are serialized to avoid competing heavy slicer processes. Artifacts persist until the operator removes them.

The default `allow_newer_file=true` passes the official `--allow-newer-file` option. This bypasses the native CLI's legacy producer-version gate (for example, a 2.x project versus internal version 01.10). It does not change print settings, imply every future format is supported or retry failures. Set it false for strict checking. External post-processing scripts are unsupported and rejected.

Each job preserves `source.3mf`, new `plate_N.gcode` files, `native-settings.json`, `props.json`, `request.json`, `result.json` and `engine.log`. The original input is never overwritten. Failures retain available diagnostics without entering the successful cache. The default native timeout is 1800 seconds; set global `--timeout` to change it.

MCP resources provide the manifest, props and G-code line ranges:

```text
slicer://jobs/{job_id}/manifest
slicer://jobs/{job_id}/props
slicer://jobs/{job_id}/plates/{plate}/lines/{start}/{count}
```

Lines are 1-based, with `count` limited to 1..1000. The full G-code remains available at its returned artifact path. `props.json` contains the native global settings export and each plate's G-code CONFIG_BLOCK. These do not exhaustively describe every final per-region override. The wrapper invents no fallback preset; native defaults and compatibility substitutions may apply, so consult settings and diagnostics when assessing fidelity.

## Direct CLI

```sh
uv run --project tools/slicer_mcp snapmaker-slicer-mcp \
  --engine /absolute/runtime/engine.json --artifacts /absolute/artifacts \
  slice /absolute/project.3mf --plate 0

uv run --project tools/slicer_mcp snapmaker-slicer-mcp \
  --engine /absolute/runtime/engine.json status --check-updates
```

Use `slice --force` to bypass cache and `slice --strict-file-version` to disable the compatibility flag. `recover /absolute/generated.gcode` uses the same artifact directory as slicing. Successful CLI output is JSON; native diagnostics are isolated from MCP stdout.

## Reverse conversion and fidelity

3MF → G-code is slicing. G-code alone does not retain the original meshes, painting, modifiers or hierarchy. `recover_3mf` returns a preserved original by verified association; it is not a geometric reverse converter. A toolpath-only `.gcode.3mf` would not establish that the original project can be sliced again.

Re-slicing can change timestamps or custom placeholders. Byte equality is guaranteed for a retained artifact, not for every new slice. Using the official engine avoids replacing its algorithms; equivalence with the GUI's fully resolved configuration requires a comparison corpus and is not certified by this prototype.

See [SPEC.md](SPEC.md) for the scoped contract and [TEST.md](TEST.md) for measured validation, source isolation and platform limits.

```sh
uv run --project tools/slicer_mcp --extra test pytest tools/slicer_mcp/tests -q
uv build --project tools/slicer_mcp
```

License: the repository's GNU AGPLv3, reproduced in [LICENSE.txt](LICENSE.txt). The wrapper adds no slicing algorithm changes to upstream.
