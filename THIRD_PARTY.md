# Third-party attribution

Portions of this package derive from **Max Ellis's OrcaSlicer MCP**:
[MaxEllis/orcaslicer-mcp](https://github.com/MaxEllis/orcaslicer-mcp),
commit `ccfacb43c2fd5a23973bfdabce89c74b2f6933fe` (package version 0.1.17),
licensed **AGPL-3.0-only**. Copyright remains with Max Ellis and the respective
contributors. The package license is included in `LICENSE.txt`.

The native slicer remains Snapmaker/OrcaSlicer. No Max Ellis fork binary,
RemoteAPI controller, GUI connection, printer client, or slicing algorithm is
incorporated by this adaptation.

| Original file | Use here |
| --- | --- |
| `src/orcaslicer_mcp/schema_extract.py` | Adapted in `src/snapmaker_slicer_mcp/schema_extract.py`; parsing setting definitions and source metadata. |
| `src/orcaslicer_mcp/settings_schema.py` | Setting lookup/search behavior adapted in `presets.py`; loads our generated native schema. |
| `src/orcaslicer_mcp/guard.py` | Protected key classification and credential redaction adapted in `presets.py`; array handling and local file editing replace the fork API assumptions. |

SHA-256 of the original files at the pinned commit:

```text
schema_extract.py  bc4da448dc1aa238b856c15f8ae29577a1cfc329d40af196c03bf20dcff4d9ff
settings_schema.py 73c00b57b64d5d022e73a0ba3e162a5ea1bdf3902e425aa212c348686e6ed697
guard.py           72e99143a46ac717e06d9d19454d12fd76dd44771340934efed315cf56d5f6e9
LICENSE            0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0
```

Local changes include removal of GUI/API error classes and environment bypasses,
explicit G-code opt-in, fixed array cardinality, artifact-local preset drafts,
source-file inheritance with SHA-256 provenance, and no writes to system/user/SOT
preset sources. The schema extractor additionally handles C++ block comments,
brace defaults, native dynamic settings, and explicit engine source identity.

`data/settings.json` is regenerated from **our pinned Snapmaker source**,
`9bf2f18eaff7cc0655cbdeef6a215660085ecb0f`; it is not Max Ellis's generated schema.
Its `_meta` records source hashes and generator origin. C++ default expressions
are documentation and are never evaluated. Missing project vectors may use a
validated literal primitive default only when it is non-nullable and the schema
matches the bound engine commit; the selection result records this backfill.
Engine/schema commit differences
are reported explicitly and never trigger an update or compilation.

To regenerate from the same native source, from `tools/slicer_mcp`:

```sh
python -m snapmaker_slicer_mcp.schema_extract --src ../../src/libslic3r/PrintConfig.cpp --out src/snapmaker_slicer_mcp/data/settings.json --source-commit 9bf2f18eaff7cc0655cbdeef6a215660085ecb0f
```

The generator checks the Git blobs of `PrintConfig.cpp`, `PrintConfig.hpp`,
`ProjectSchemaVersion.cpp`, and `version.inc` against that native commit. It
includes dynamic machine-axis limits, nullable filament overrides, native flow
enum constants, and the project schema key. CLI flags and G-code placeholders
are excluded. Source templates, legacy aliases, and unknown existing values
remain available in raw preset data; file inheritance is not native runtime
normalization or final per-region resolution.

This attribution covers the reused Python helpers. It does not assert U1,
multimaterial, physical-print, GUI/CLI, or fork/official-engine equivalence.
