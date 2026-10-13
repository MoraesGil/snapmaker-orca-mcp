# Snapmaker slicer MCP — slicer first

Scope confirmed and extended on 2026-10-12: preserve native slicing and add project/object/preset/plate editing, including automatic arrangement. Preview, HTML and frontend state belong to a separate project.

## Contract

- A small Python MCP/CLI layer invokes the official Snapmaker Orca C++ engine in its native `--slice` mode. Native slicing remains unchanged.
- Input: one 3MF project, a plate number (`0` means all) and optional force-reslice. Optional working-copy edits use the same engine; there is no printer access.
- Output: fresh G-code per requested printable plate, an intact copy of the source 3MF, native exported settings, per-plate G-code configuration blocks and a JSON manifest with hashes, actual engine commit, command, timings and diagnostics.
- Slicing uses `--slice`; `--export-settings` records configuration. Arrangement/orientation use native `--export-stls` pose probes and validate transforms against every exported vertex before applying them to the original component structure. `--export-3mf`, thumbnail rendering, GUI startup, Xvfb and VNC are excluded.
- Native binaries may share libraries/code with the desktop distribution. This does not authorize starting a GUI; the execution path must run without a display and without GUI initialization. Native integration must be tested, not inferred from the build flag `SLIC3R_GUI`.
- The upstream is `Snapmaker/OrcaSlicer`, branch `main`. Builds are bound to a source commit, executable hash and resource hash. An upstream advance produces an update/rebuild notice. The MCP never fetches, builds or replaces its engine automatically.
- Native resource paths must match the executable's standard package layout. Startup verifies content hashes; serving checks file identities/sizes/times and resolved resource location before reuse/execution and after slicing. Drift requires explicit rebinding/restart. Custom Linux FHS builds are outside this prototype.
- Cache keys include exact input bytes, plate selection and engine identity. A cache hit returns the stored G-code with `cached=true`; it is not described as a new slice. Force-reslice bypasses reuse. Changed/corrupt artifacts must not be reused.
- The cache index binds the complete result manifest by hash, including its full plate list. Returning a valid artifact from another request or a partially edited manifest is not a cache hit.
- Preserve full G-code as an artifact. MCP resources expose bounded line ranges for inspection, without returning millions of lines in a tool response.
- Configuration scope is explicit: native global export and per-plate CONFIG_BLOCK; this is not a claim of complete final per-region settings. No fallback profile is invented or silently selected by the wrapper. Native defaults/substitutions may still occur and are recorded through settings/logs with their coverage limit.
- Compatibility is explicit: `allow_newer_file=true` passes the official `--allow-newer-file` flag, necessary for projects rejected by the CLI's legacy producer-major-version gate (2.x producer vs internal 01.10). It does not edit the source or print settings, does not guarantee every newer format is supported, and is recorded in the manifest/cache key. Set it false for strict version checking. No automatic retry or alternate engine is used.
- Nonempty post-processing scripts are unsupported, since the CLI and GUI behavior may differ. Errors and timeouts preserve diagnostics and do not produce successful cache entries.

## Working-copy editing

- `load_model` copies a complete saved 3MF into a private project workspace. Every successful edit commits a new immutable revision with a SHA-256. The optional expected revision rejects stale edits. A failed batch never advances project state.
- Preserve unchanged ZIP entry payloads, original meshes/painting, root and part IDs, component references and instance transforms. Duplicate operations allocate new root/instance identities while retaining component geometry. Edited outputs invalidate embedded G-code, thumbnails and related slice metadata.
- Support global/object/part settings; object duplication/deletion/transforms; plate creation/selection/deletion/moves; native arrangement/orientation; height-range overrides and adaptive/reset layer profiles. Deleting an occupied plate parks its instances off-bed, preserving geometry and identity; explicit moves restore them. The last plate cannot be deleted.
- Native all-plate arrangement persists both world poses and the resulting plate count/membership, verified against every instance's exported vertices. Locked instances remain protected and are rebased if the plate grid changes. Selected-plate overflow fails atomically; callers may choose all-plate arrangement explicitly.
- Object IDs are stable root IDs. Instance and filament-slot indexes are 0-based; plates are 1-based and slice plate 0 means all. `extruder=0` inherits and `1..N` selects a filament slot.
- Translation is an XYZ delta in mm; rotations are XYZ deltas in degrees; scales are nonzero per-axis factors. Rotation/scaling preserve the instance pivot. Plate-grid changes rebase existing instances to preserve local placements.
- The setting schema is extracted from the pinned Snapmaker source, not the MaxEllis upstream fork. Metadata identifies source/engine versions. Unknown keys, invalid types/values, ambiguous filament cardinality, credentials and executable post-process hooks are rejected. Custom G-code edits require an explicit opt-in.
- Preset sources are declared read-only. Resolve inheritance with provenance and reject missing/cyclic/ambiguous parents. Only artifact drafts can be edited/renamed/deleted; no draft operation may overwrite an installed or canonical preset. `_GENERIC` profiles remain unchanged. Other materials/presets serve for comparison unless a user approves a working-project change.
- Filament selection follows native per-material flow segments and changes only the requested slot/mode, including its active mode. Other materials and flow variants remain intact. A missing project vector may be backfilled with a validated non-nullable literal default from the matching native source; the result reports its provenance. Unknown expressions and mismatched versions fail explicitly.
- Adaptive profile generation is a bounded port of the official source calculation, pinned to engine commit `9bf2f18`, with explicit unsupported-case errors; native slicing still consumes and evaluates the saved profile. Native quality `0` is finest and `1` coarsest. Millimeter units, uniform nozzle/layer limits and 100% Z shrinkage are required. Reset clears profiles and Z bands; clearing Z bands alone retains profiles. No claim of full GUI adaptive equivalence is inferred from this port.
- `slice_project` slices the exact current working revision. `apply_and_slice` commits settings then slices; failure of slicing preserves the committed revision and native diagnostics. Existing direct `slice_3mf` behavior stays compatible.

## Reverse direction

3MF to G-code is slicing. G-code alone cannot reconstruct the original editable/refatiable project: mesh, painting, modifiers and hierarchy are lost. `recover_3mf` may recover a preserved source whose generated G-code hash matches the supplied file. It fails when the source is absent, corrupt or ambiguous. It does not invent geometry or relabel a toolpath-only package as a project.

Re-slicing is not promised to produce byte-identical G-code: timestamps and custom placeholders can change. Retaining the original generated G-code preserves exact bytes. Equality of executable toolpaths with the official GUI requires its own comparison corpus.

## Acceptance

1. Native U1 slice without GUI/display produces new nonempty G-code, using unchanged source input.
2. Multiple requested plates are individually identified; embedded old G-code is not returned as a fresh slice.
3. Repeat input/engine uses verified cache; force-reslice runs again; failed/corrupt results are not reused.
4. Source recovery preserves exact source bytes and rejects an unrelated G-code.
5. Advancing upstream changes only the update notice until an operator installs a new build. The new identity invalidates cache automatically.
6. The installed CLI and MCP stdio protocol both return the real native result. Boundary test doubles are not engine-equivalence evidence.
7. Editing preserves the source and installed preset hashes, unchanged mesh/painting payloads and valid object/plate references. Native slices exercise the final working revision.
8. Automatic arrangement/orientation executes without a display/renderer and retains native probe logs and per-vertex affine validation. Adaptive support limits are reported explicitly.

## Source baseline

Official main inspected: `9bf2f18eaff7cc0655cbdeef6a215660085ecb0f`.
Native macOS artifact tested: `441f73cc8d91d84f574a9d910e84fb8eeb099cf5`, run `38162124732`, artifact `11715785490`. Its newer native macOS build remains pending.

Explicitly updated Linux runtime: official main `9bf2f18eaff7cc0655cbdeef6a215660085ecb0f`, run `38288173465`, artifact `11766257337`. Native CLI/MCP passed in Ubuntu 24.04 amd64 without a display server. The source fix affects tree/manual support. The acceptance input does not itself exercise painted supports; this is runtime integration evidence, not a bug-specific or GUI-equivalence certification. See `TEST.md` and the small `Dockerfile`/`LINUX.md` recipe.

Native CLI behavior: `src/Snapmaker_Orca.cpp` GUI_Run guard around 1142; per-plate G-code export around 5180; graphical thumbnails inside export_to_3mf around 5308; settings around 4757. Project-only vs toolpath-only 3MF: `src/slic3r/GUI/Plater.cpp` around 20481–20544 and `CLI::export_project` around 6290.
