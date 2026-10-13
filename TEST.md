# Validation record — 2026-10-12

This record separates wrapper checks from native engine evidence. No print was started or printer contacted. No desktop UI, virtual display, Xvfb or VNC was used.

## Wrapper and MCP boundary

```sh
uv run --project tools/slicer_mcp --extra test pytest tools/slicer_mcp/tests -q
```

Behavioral tests cover source preservation, all/single plate selection, fresh outputs rather than embedded old G-code, verified cache/force/corruption, swapped cache pointers, input/engine identity, compatibility flags, failures, timeout termination, source recovery, corrupt unrelated manifests, engine drift during serving, actual package resource layout, bounded resources, CLI JSON and MCP stdio calls/errors.

These tests intentionally use a small external process double at the native boundary. They exercise orchestration and the MCP protocol, not slicing algorithms. A real-engine acceptance run is recorded below.

Historical 0.1.0 wrapper/protocol run: **19 passed**. `uv build --project tools/slicer_mcp` produced an sdist and wheel; the wheel was installed in a separate clean virtual environment, where the installed command's `--help` succeeded.

## Official native build

- Repository/ref: `Snapmaker/OrcaSlicer`, `main`.
- Tested build commit: `441f73cc8d91d84f574a9d910e84fb8eeb099cf5`.
- Official [Build all run 38162124732](https://github.com/Snapmaker/OrcaSlicer/actions/runs/38162124732), macOS artifact `11715785490`, `Snapmaker_Orca_Mac_universal_V2.4.1`.
- Downloaded artifact SHA-256, verified against GitHub metadata: `794343c32747aecd339c6d1a90d237c472bd8ac23775c31293c6b9e206942d81`.
- Executable SHA-256: `6f4808e45dd5fd325102362229920af3bc9e784a4366cb2e59a141df300cd41d`.
- Resource-tree SHA-256: `cc1413839fa305f6759cd73f9ee4890b5cde0504aa0a0bb694c92cdc576e2d72`.
- Host: macOS ARM64. Standard official bundle, executed directly in native `--slice` mode; `DISPLAY` and `WAYLAND_DISPLAY` removed from the child environment.

The bundle shares dependencies with the desktop distribution. The upstream action guard skips `GUI_Run` for native slicing; this does not assert that a `SLIC3R_GUI=OFF` build is ready. No native C++ changes were made by the wrapper.

## Real input and result

One existing small Snapmaker U1 0.4 mm whistle project, plate 1, 0.16 mm layers. Source SHA-256: `a2765234f4a206ba55928492f6b7f1d3a95fc38345a09bbc7d83bb036831b49d`. Private project paths, models and generated files remain outside the published package.

The producer-version gate rejected this converted project in strict mode. Explicit `allow_newer_file=true` uses the official native compatibility flag; source bytes remain unchanged. No print setting override or retry with an alternative engine was used.

Observed native acceptance:

| Check | Observed result |
| --- | --- |
| Installed CLI slice | Exit 0; new nonempty plate 1 G-code; native time 2.13 s |
| MCP stdio `slice_3mf`, `force=true` | New 801,810-byte G-code, matching its returned SHA-256 |
| MCP native time, final repeat | 1.14 s |
| MCP uncached request time, final repeat | 1.85 s |
| MCP verified cache request time, final repeat | 0.23 s; same job and G-code hash |
| MCP startup, including hashes/update check, final repeat | 8.34 s, outside request timings |
| MCP G-code/props resources | Read successfully; 592 per-plate CONFIG_BLOCK entries |
| Original and stored 3MF | Byte-identical before/after slicing |
| MCP `recover_3mf` | Returned exact preserved source, `reconstructed=false` |
| Update check | Reported newer main; engine remained at its bound commit |

Times describe this input and host. They are not a throughput guarantee or a benchmark against Node.js. Cache inspection and engine checks also have a cost. Keep the MCP server running to amortize startup.

An earlier native MCP run measured 3.32 s slicing, 4.20 s per uncached request and 0.20 s for verified cache. Both runs preserved source bytes and returned the complete 801,810-byte G-code. This variation is why the small-input timings are reported as observations.

## Explicit main update — Linux `9bf2f18`

Source inspection confirms [commit 9bf2f18](https://github.com/Snapmaker/OrcaSlicer/commit/9bf2f18eaff7cc0655cbdeef6a215660085ecb0f) changes tree/manual support: painted layers no longer admit automatic supports on unpainted overhangs. The automatic-support branch is unchanged by that commit. It was already the fork's native source baseline; the runtime needed updating.

An official Linux package became available and was incorporated into the tested container recipe:

- Source/ref: official main `9bf2f18eaff7cc0655cbdeef6a215660085ecb0f`.
- [Build run 38288173465](https://github.com/Snapmaker/OrcaSlicer/actions/runs/38288173465), artifact `11766257337`, `Snapmaker_Orca_Linux_ubuntu_2404_V2.4.1`.
- ZIP SHA-256 verified against GitHub: `53d6aba539ca5363b246fe2a6500cd03c6a13898316711235b29e45af60f9dab`.
- AppImage SHA-256: `bc03ec0d3add911b3e90afa1a9373f2c53e4d11692b58bfc71031657f42e8617`.
- ELF SHA-256: `b9f6c2a9d59e0fb43761c7ce3c5c5c908f68f68991e27bd314d40a88ebb66cf9`.
- Resource SHA-256: `cc1413839fa305f6759cd73f9ee4890b5cde0504aa0a0bb694c92cdc576e2d72`.
- Public Dockerfile build passed. Local image ID: `sha256:56fc338e12f6e44468cb1f615fc7d601e9506692eefbc9f3a8abf6518bd6e548` (not published to a registry).
- Environment: Ubuntu 24.04 amd64, 4 CPU limit / 4 GiB RAM, under emulation on macOS ARM64. GTK/WebKit/GL are linked libraries, not a running desktop. No display variables, display server, Xvfb, VNC or TTY. Runtime network disabled during acceptance.

The image's default stdio command passed real MCP initialization, tool discovery, slicing, line/props resources, verified cache and exact source recovery. Same intact U1 input hash as above; fresh G-code was 797,794 bytes; per-plate CONFIG_BLOCK exported 592 values. Observed native time 5.30 s, uncached request 5.89 s, cache request 0.24 s, startup 3.04 s. These emulated/capped-container timings are not a native Linux or macOS performance benchmark.

The default update check correctly reported `unavailable` with network disabled. It did not pretend the engine was up to date, nor fetch/compile/replace anything. An online `status --check-updates` from the same image then matched upstream main at `9bf2f18`, with `update_check=checked`, `update_available=false` and no notices. Updating was an explicit operator action, as documented in [LINUX.md](LINUX.md).

Global/per-plate exported settings matched the older macOS acceptance, but the executable command streams were not identical. This comparison changes both platform and source commit; it does not isolate the manual-support fix or certify GUI equivalence. The newer runtime was tested on the small whistle, not a painted-support regression corpus.

## Editing 0.2.0: isolated development and native acceptance

During isolated development, the original `codex/slicer-mcp` checkout was restored to commit `5676d117eb013e8fad1bf720c6e6066baeb38916`, package 0.1.0, with a clean Git status and **19 passing tests**. Editing work used `codex/slicer-mcp-editing` in a separate Git worktree, `.temp/worktrees/slicer-mcp-editing`. The old stable image and shared MCP client configuration were left unchanged during that phase.

The editing suite exercises stable object/part identities, split components and painting payloads, native XML vector/string codecs, affine transformations, linked-cut references, plate grid reflow and parked objects, Z bands/adaptive profiles, stale revision rejection, read-only preset sources, flow-mode draft preservation and compatibility metadata. The private source fixture test skips explicitly if the local 3MF is unavailable; it does not make a public checkout depend on a private file.

Final full suite: **241 passed in 13.14 s**. Independent review reproduced **108 focused tests**, plus 38 private segment/slot cases and 19 literal-default guard cases. Review found no remaining issue in that bounded scope; it is not native or GUI equivalence evidence.

The 0.2.0 wheel SHA-256 is `2aeb435de1f166eb4f42df0961f6f51542a4e58fbf88af534fe0e1b18d20fd54`. Its installed test image is `snapmaker-slicer-mcp:editing-0.2.0-main-9bf2f18`, ID `sha256:1872c7fcd96adf88c36a1fc30b6e97450551a7f33f6f12216cb4512baf4a65c1`. Package inspection confirmed 699 setting records, attribution and AGPL license, with no private 3MF/G-code/runtime payloads in the wheel or sdist. The stable image remains `56fc338e` as recorded above.

Native acceptance uses the same official Linux `9bf2f18` executable/resources recorded above, 4 CPUs / 4 GiB RAM, with network disabled and no display. An installed 0.2.0 wheel exposed **33 MCP tools**. The first sequence made 26 successful calls through the real MCP stdio protocol, including preset selection, object overrides, duplication/rotation, native arrangement/orientation, Z bands, adaptive layers/reset, two plates, source recovery and draft preset CRUD. The second sequence made 22 calls covering automatic overflow onto a second plate, rejected single-plate overflow without a revision commit, parking/restoring an occupied plate, and explicit machine/process/filament selection followed by a part override and fresh slicing. The third sequence made 14 successful calls through the same installed image, plus an expected rejection of malformed flow-step encoding without advancing the revision, and two fresh slices verifying segmented Silk/PETG selection.

Observed output from the final installed image (**62 successful MCP calls**, 9 fresh slice jobs, 11 G-code files):

| Native result | Fresh G-code bytes | Native time | Request time |
| --- | ---: | ---: | ---: |
| Duplicate + rotate + arrange | 1,806,831 | 3.21 s | 3.72 s |
| Z-band layer override | 1,861,283 | 3.17 s | 3.63 s |
| Adaptive layer profile | 1,396,256 | 3.21 s | 3.69 s |
| Two edited plates | 931,617 + 925,101 | 3.21 s | 3.72 s |
| Automatic distribution of two 200 mm cubes | 661,570 + 661,570 | 2.17 s | 2.66 s |
| Delete occupied plate, retain geometry | 661,570 | 3.21 s | 3.68 s |
| Explicit presets and part override | 791,509 | 3.18 s | 3.65 s |
| Silk high-flow, segmented material 1 | 781,520 | 3.16 s | 3.61 s |
| PETG, segmented material 2 | 798,215 | 3.27 s | 3.76 s |

The source whistle and split mesh/painting payloads remained unchanged. The overflow fixture SHA-256 was `0d46bf159b5f218aef75b5d3ce26a3122e2412538a0cc968bbd049e6e59cfd1b`; its original bytes and mesh payloads also remained unchanged. All five declared canonical preset JSON files retained their hashes. The machine preset was a private read-only snapshot; its source hash was `53c0518b158b630f484e4d83cb91da9786e2056b445c1f0331b08916c416d4b7`.

All native PETG checks observed the selected Cool Plate at **65/65 C**, first-layer nozzle **245 C** then **240 C**, and only `M106 P2 S0` for the auxiliary fan. Startup preheat/cleaning commands use other temperatures; this is not a claim that every nozzle-temperature command is 240/245 C. The fixture's printable part used one PETG slot. Unused PLA slots do not establish multi-material tool-role correctness. Global/plate configuration exports do not exhaustively certify final per-region overrides.

The adaptive profile implementation was compared against 45 cases compiled from the pinned upstream C++ functions. Output lengths matched and the maximum absolute height difference was `1.49e-7` mm. Supported-domain restrictions and the engine pin are explicit in the API; unsupported configurations fail instead of silently approximating them.

The 19 native filament flow-variant keys use per-material segments. A private fixture with segment lengths `[2,1,1,1]` retained unrelated slots/modes while selecting Silk high-flow at offset 1, saving both draft modes, and then selecting PETG in material 2 at offset 2. Native acceptance checks temperatures, material limits, bed temperature and auxiliary commands for each separately sliced material. This is not a simultaneous multi-material or physical head-mapping test. The valid fixture SHA-256 is `65f692ce647fb5edc02da5257359ce848ddef453ae7d4cde026cff207049af19`; all unchanged component payloads came from the whistle source.

An early synthetic fixture used JSON numbers for segment sizes. The native JSON parser requires string arrays, logged `invalid json array` and ignored that key. The valid fixture uses `["2","1","1","1"]`; selection/save now reject the malformed representation atomically. API `set_config` still accepts numeric values and serializes them correctly. Native G-code comments intentionally contain only the active variant per material; their shorter vectors are not missing source variants.

For an absent `filament_adhesiveness_category`, the stable 0.1.0 native baseline exported `["100"]`, matching the pinned C++ default. Final selection backfills that validated default for unrelated materials and applies only the selected preset's value (Silk 100, PETG 300), returning `native_defaults_applied` with source/version. Nullable defaults, C++ expressions, invalid types/ranges, non-finite values and mismatched engine/source commits fail atomically. No selected material's value is copied into the other slots as a fallback.

Private acceptance manifests, per-call results, G-code hashes and native logs are retained under `.temp/editing-acceptance`; private models and outputs are not package contents. These measurements are input-specific, under emulation, and do not establish GUI equivalence or physical print quality.

## Main/default promotion

The operator subsequently authorized publishing the MCP on the fork's `main` and using 0.2.0 as the local default. A fresh pre-integration suite passed **241 tests in 13.78 s**. All 11 installed Python/catalog files matched the release source byte for byte. The release image tag is `snapmaker-slicer-mcp:0.2.0-main-9bf2f18`, retaining the same immutable image ID and native engine as the editing acceptance above. Promotion of this Python layer does not compile or upgrade the native engine. Repeat the package suite after integration and exercise the registered stdio launcher before accepting a new local installation.

## Reported Enforma failure: separate legacy runtime

The reported execution used `keychain-fatiador`, a Linux ARM64 2.3.4 runtime, and ended with return code 139 after `calc_exclude_triangles`. That container was not updated or restarted by this work.

A stable snapshot of its exact 97,378,739-byte input had SHA-256 `f23941b4161c35f3da62afb513ad738d7a949118d72ce17bd058ca50b7f6e1c1`. Direct native CLI execution with the official `9bf2f18` image completed with exit 0 in **201.154 s**, generating a new 241,527,818-byte G-code (SHA-256 `00ad5b8ca604b04d45d32a094c7e79910f775a3acb0cb54e65eddcca776ee048`). CPU limit was 4, RAM 8 GiB, network disabled; input bytes remained identical.

This diagnostic used the explicit CLI override `--post-process ''` to disable an embedded external script. The MCP correctly rejected that original project before launching the engine because a stored process preset contains a nonempty post-processing script. The wrapper guard was retained. Direct CLI success with that override is not a successful unchanged MCP request, GUI parity or a print approval. The script was in stored preset `0.20 Standard`; the active process was `0.12 Ultra HD`. Native logs still contain `calc_exclude_triangles` and wall-reversal diagnostics; exit 0 does not erase those warnings. The generated file contained 22 extruded objects, with T0–T2 PLA at 220 C and T3 Silk at 230 C during object extrusion, bed at 50 C and no positive explicit auxiliary-fan commands. Physical head loading, macro expansion and GUI parity remain unverified.

## Remaining limits

The retained native macOS binding is `441f73c`; no newer macOS package was validated in this task. The Linux container tested here uses `9bf2f18`. These are separate bindings; the previous macOS runtime was not overwritten or mislabeled.

Corrupt results, timeout and failure behavior are covered by boundary tests. The initial acceptance above covered one real U1 plate on both runtimes; the editing acceptance adds native multi-plate runs on Linux. A multi-material comparison corpus is still outside this evidence. Windows deployment and GUI-versus-CLI equivalence remain unvalidated. Global and per-plate exports are not exhaustive final per-region configuration evidence. Container identity is recorded here; shared libraries are not individually hashed as part of the MCP's executable/resources cache identity.

Re-slices can differ in timestamps/custom placeholders. Standalone G-code cannot reconstruct source meshes or painting; recovery requires the stored original. A retained G-code artifact preserves its exact bytes.
