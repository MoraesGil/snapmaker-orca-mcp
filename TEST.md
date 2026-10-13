# Validation and evidence

This record separates Python/MCP checks, real native slicing and physical printing. No printer operation or GUI-equivalence certification is implied by a passing test suite.

## Reproduce the public tests

From this standalone repository root:

```sh
uv sync --frozen --extra test
uv run pytest tests -q
uv build
```

The extraction baseline at `6e56b1f` passed **240 tests with 1 skip**. The integrated 0.3.0 suite passed **267 tests with 1 skip** on macOS/Python 3.12. HTTP tests use real ASGI/TCP transports, including three simultaneous clients; native algorithms remain process doubles in this public suite.

Tests use small process doubles at the native boundary to exercise source/revision preservation, stable IDs and component payloads, configuration codecs, atomic edits, stale SHA rejection, cache corruption, timeouts, recovery, preset inheritance/drafts, flow segments, settings metadata and MCP protocol behavior. These tests do not run the real slicing algorithms.

The optional private geometry-fixture test skips when its input is absent. The recorded fixture SHA-256 is `a2765234f4a206ba55928492f6b7f1d3a95fc38345a09bbc7d83bb036831b49d`; its model and outputs are not distributed. Four public native preset fixtures include an independent [provenance manifest](tests/fixtures/snapmaker-profiles/PROVENANCE.json). Public tests require neither the old fork nor a C++ source checkout.

## Official native engine evidence

The retained Linux runtime is official `Snapmaker/OrcaSlicer` main:

| Identity | Recorded value |
| --- | --- |
| Source commit | `9bf2f18eaff7cc0655cbdeef6a215660085ecb0f` |
| Official build | [Run 38288173465](https://github.com/Snapmaker/OrcaSlicer/actions/runs/38288173465), artifact `11766257337` |
| Extracted executable SHA-256 | `b9f6c2a9d59e0fb43761c7ce3c5c5c908f68f68991e27bd314d40a88ebb66cf9` |
| Resource-tree SHA-256 | `cc1413839fa305f6759cd73f9ee4890b5cde0504aa0a0bb694c92cdc576e2d72` |
| Recorded environment | Ubuntu 24.04 amd64, 4 CPUs / 4 GiB, emulated on macOS ARM64, native requests offline. |

Historical installed **0.2.0** acceptance used this same native binding and exposed 33 tools. It exercised private project edits, native arrangement/orientation, plate overflow, adaptive layers, flow-mode selection, preset drafts, resources and source recovery. The final sequences recorded 62 successful MCP calls, 9 fresh slice jobs and 11 G-code files. Sources and canonical presets retained their hashes. These are historical native-boundary results, not acceptance of the new HTTP transport.

Small private fixtures completed fresh requests in roughly **4 seconds** in that capped/emulated environment. This is an input-specific observation, not a benchmark, throughput promise or comparison with another slicer. Large or complex projects can behave differently.

Adaptive profile output was independently compared against 45 cases compiled from the pinned native C++ functions. Output lengths matched; maximum absolute height difference was `1.49e-7 mm`. This covers the documented supported domain, not every printer/nozzle/shrinkage configuration.

Segmented material selection was exercised with native string-encoded steps `["2","1","1","1"]`, five-value flow vectors and four material slots. Native output confirmed the separately selected material's active settings. This does not prove simultaneous multimaterial tool roles or physical head loading.

## Shared HTTP acceptance

Installed 0.3.0 acceptance on 2026-10-13 used one persistent Ubuntu 24.04 amd64 container, emulated on macOS ARM64 with 4 CPUs / 8 GiB and the unchanged executable/resource hashes above. Three HTTP clients shared 33 tools, three resource templates and one editor/artifact store. The checks below passed:

- Invalid/missing bearer tokens, unexpected Host values and disallowed supplied Origins are rejected.
- Three concurrent clients initialize against the same service, list 33 tools and read the three resource templates.
- Client reconnects reuse that backend; they do not create containers or editor instances.
- A fresh native slice returns new nonempty output whose hashes match; cache reuse validates retained artifacts.
- Resources and recovery refer to the captured immutable project revision.
- A slow slice permits unrelated project-state reads; the original source and read-only preset hashes remain unchanged.

The fresh saved-source request took **4.97 s** end to end (**4.40 s** in the native process); the edited private revision took **4.22 s** natively. These are observations of one small private fixture. Missing/wrong tokens and invalid supplied Origins were rejected; exact Host checks passed in the transport tests. Resources, verified cache, source recovery, stale-edit rejection and reconnecting to the same project passed. The input and all five canonical preset files retained their hashes.

Four locally installed material presets resolved. One local process preset referenced a parent name absent from this engine's resources; resolution failed explicitly. No preset was overwritten and no alternative was silently adopted. This does not block slicing a complete saved project with embedded settings.

Two fresh slices of the same input on the same installed backend produced different command ordering, despite identical native settings and engine identity. Timestamp changes alone do not explain those differences. Exact output-byte preservation applies to retained, hash-verified artifacts; fresh-slice byte determinism is not claimed.

The HTTP process-double suite and installed native acceptance answer different questions. Authentication/liveness success alone does not establish that a particular 3MF slices correctly.

## What remains outside the evidence

- **GUI equivalence:** native CLI success does not certify the GUI's fully resolved settings or every final per-region override.
- **Physical printing:** no print quality, material behavior, mechanical fit or physical color/head matching is certified.
- **Rendering:** no preview renderer or screenshot-based validation is shipped.
- **Platforms:** Linux amd64 native acceptance is recorded above. Historical macOS acceptance used the separate older `441f73c` binding. Windows and native Linux ARM64 deployment remain unvalidated.
- **Recovery:** verified association retrieves a retained source; standalone G-code does not reconstruct meshes, painting or hierarchy.
- **Identity:** binding checks executable/resources, while the supplied source commit remains an operator declaration. Shared libraries are not individually part of that cache identity.
- **New slices:** timestamps, custom placeholders and observed native path-order variation can change G-code bytes. Exact byte preservation applies to retained artifacts.

See [Linux setup](LINUX.md), [usage](docs/usage.md) and [contribution guidance](CONTRIBUTING.md) to reproduce and report bounded results without publishing private models or logs.
