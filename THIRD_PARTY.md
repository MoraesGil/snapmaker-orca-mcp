# Third-party attribution

Snapmaker Orca MCP is distributed under the [GNU AGPLv3](LICENSE.txt). Copyright and attribution of reused material remain with its respective authors.

## Max Ellis: three adapted Python helpers

Portions derive from **Max Ellis's OrcaSlicer MCP**, [MaxEllis/orcaslicer-mcp](https://github.com/MaxEllis/orcaslicer-mcp), package **0.1.17**, pinned commit `ccfacb43c2fd5a23973bfdabce89c74b2f6933fe`, licensed **AGPL-3.0-only**. Copyright remains with Max Ellis and the respective contributors.

| Original file | Adaptation |
| --- | --- |
| `src/orcaslicer_mcp/schema_extract.py` | Setting-definition/source metadata parsing in `src/snapmaker_slicer_mcp/schema_extract.py`. |
| `src/orcaslicer_mcp/settings_schema.py` | Setting lookup/search behavior in `presets.py`, using the Snapmaker-derived schema. |
| `src/orcaslicer_mcp/guard.py` | Protected-key classification and credential redaction in `presets.py`, adapted for native arrays and local drafts. |

Original-file SHA-256 at the pinned commit:

```text
schema_extract.py  bc4da448dc1aa238b856c15f8ae29577a1cfc329d40af196c03bf20dcff4d9ff
settings_schema.py 73c00b57b64d5d022e73a0ba3e162a5ea1bdf3902e425aa212c348686e6ed697
guard.py           72e99143a46ac717e06d9d19454d12fd76dd44771340934efed315cf56d5f6e9
LICENSE            0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0
```

Local changes replace GUI/RemoteAPI assumptions with versioned file edits, native array/cardinality validation, explicit custom-G-code opt-in, artifact-local drafts and hash-recorded inheritance. The extractor handles block comments, brace defaults, dynamic native settings and source identity.

The MaxEllis tool design also informed feature names. This adaptation does not incorporate its fork binary, GUI connection, RemoteAPI controller or printer client, and does not claim its complete diagnose/compare feature set.

## Snapmaker/OrcaSlicer: native engine, schema and fixtures

The native slicer is the separately supplied official [Snapmaker/OrcaSlicer](https://github.com/Snapmaker/OrcaSlicer), using recorded main commit `9bf2f18eaff7cc0655cbdeef6a215660085ecb0f`. The wrapper does not replace its slicing algorithms or redistribute a native build in the Python wheel.

`src/snapmaker_slicer_mcp/data/settings.json` is generated from that official source, not from Max Ellis's generated schema. Its metadata records source hashes, the native commit and generator origin. The adaptive profile implementation is ported from pinned native functions; its supported domain and validation limits are documented in [TEST.md](TEST.md).

C++ default expressions are documented and never evaluated. A missing project vector can use a validated non-nullable primitive JSON literal only when schema and bound engine commits match; selection reports that backfill and its source.

Four unchanged **public preset fixtures** from official `resources/profiles/Snapmaker` are included for catalog tests under the upstream AGPL license:

| Fixture | SHA-256 |
| --- | --- |
| `filament/Generic PLA.json` | `94ea1409679980d640d6df784741167ba6024bbe29afb5fc7c08f4facf74fa21` |
| `filament/Generic PLA @U1 base.json` | `df0303b27028cadacc81edc975cda2671150228ff86590591c543a7cacd9832a` |
| `filament/fdm_filament_pla_category.json` | `861ebe22c5353bdb0be85580c97979f7670bbe834e82c3d60aaf2db3ce82a767` |
| `filament/fdm_filament_common.json` | `a423bfe10babffa549f7de8209dd272e9ad36a3fa9e51740ffca0d81d677cb49` |

Their [provenance manifest](tests/fixtures/snapmaker-profiles/PROVENANCE.json) identifies the source repository, commit, paths and hashes. These fixtures are test inputs, not installation of a user's presets. Private models, private presets, G-code, tokens and original diagnostic logs are not distributed.

### Regenerate the schema from an explicit official source checkout

Run from this standalone repository root. Supply an official Git checkout containing the pinned source and its neighboring native files:

```sh
uv run python -m snapmaker_slicer_mcp.schema_extract \
  --src /explicit/official/source/src/libslic3r/PrintConfig.cpp \
  --out src/snapmaker_slicer_mcp/data/settings.json \
  --source-commit 9bf2f18eaff7cc0655cbdeef6a215660085ecb0f
```

The generator verifies Git blobs for `PrintConfig.cpp`, `PrintConfig.hpp`, `ProjectSchemaVersion.cpp` and `version.inc` against the declared commit. No old fork checkout is required. It includes native dynamic limits, nullable overrides, flow-variant keys/constants and the project schema key; CLI flags and G-code placeholders are excluded.

File inheritance and source metadata do not establish compiled runtime defaults, final per-region resolution, physical printing or GUI/CLI equivalence.

## Original cover artwork

`docs/assets/hero.png` was created for this repository using the built-in image-generation tool. It illustrates the conceptual agent → 3MF → G-code workflow, without third-party logos or an invented product UI. It is not a native preview or an example of measured print output.
