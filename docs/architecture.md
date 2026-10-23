# Python service architecture

The optional Python service coordinates project edits and a separately installed
official Snapmaker Orca engine. The native engine still performs slicing in its
own process; the Python framework does not implement the slicing algorithms.
The standalone Bash launcher remains independent of Python and the server.

| Package | Responsibility |
| --- | --- |
| `domain/types`, `domain/entities`, `domain/settings` | Immutable values, persisted result shapes and setting validation/normalization. No filesystem, process or transport imports. |
| `application/contracts` | Typed document, preset, revision, native execution and artifact capabilities. |
| `application/services` | Edit/preset/slice use cases, bounded line extraction and native admission. Dependencies arrive through constructors. |
| `adapters/three_mf` | Read/write the preserved 3MF graph, geometry and XML serialization. |
| `adapters/orca` | Engine identity, native layout/editing bridge and subprocess lifecycle. |
| `adapters/storage` | Immutable revisions, atomic publication, preset drafts, verified artifacts and cache persistence. |
| `adapters/mcp`, `adapters/http` | FastMCP tools/resources, authenticated ASGI/REST and CLI conversion. |
| `bootstrap.py` | Compose concrete adapters and shared services for the public entry points. |

The original Python imports remain as compatibility facades. Application and
domain code depend on protocols and domain values; infrastructure implements
those capabilities. Architecture tests reject imports back into the concrete
adapters, including imports under `TYPE_CHECKING`.

This is a small domain-oriented architecture. It uses explicit invariants and
use cases without requiring an aggregate or a class for each individual setting.
The setting catalog is dynamic JSON, with recursive `JsonValue`/`JsonObject`
contracts and validation at external data boundaries. Public entities and narrow
ports use dataclasses, TypedDicts and protocols. `py.typed` exposes annotations
to Python consumers.

## Native execution and persistence

One native admission coordinator belongs to each slicer instance. MCP and REST
share that instance, artifacts and project services. Async requests wait for
admission without occupying worker threads. A disconnected caller cannot
release the gate while its native thread is still running. Native layout and
slicing also share a process lock.

The slicing use case validates the request, captures the source identity,
verifies the bound engine, checks the cache and creates an immutable source
snapshot before invoking the native runner. The runner owns process-group
timeout handling, diagnostics and job-local temporary files. Storage validates
the produced plates and persists their hashes. Cache hits verify the manifest,
source, properties and every generated G-code file; stored absolute paths never
override the current artifact root.

Edits use a revision repository transaction and compare the expected source
hash. Publication remains atomic; application services decide which operations
to apply and when to commit them. Canonical source projects and installed
presets remain read-only. Preset writes target artifact-local drafts.

## Checks and compatibility

```sh
uv sync --frozen --extra test
uv run --frozen --extra test mypy
uv run --frozen --extra test pytest tests -q
```

Mypy checks the whole first-party package in strict mode, also rejecting
explicit or unimported `Any`. Positive and negative consumer tests check that
invalid domain values, configuration payload types and capability implementations
are rejected statically. Python's runtime validation remains necessary for
untrusted JSON and native files.

Golden wire tests freeze tool names, documentation, argument/output schemas,
resource templates, REST OpenAPI and representative errors. They compare stdio
and authenticated Streamable HTTP independently. The transport upgrade also has
an isolated SDK-v1 interoperability check; updating a framework does not
authorize changing the golden contract.

`scripts/benchmark_wrapper.py` compares synchronous wrapper operations on
deterministic small, medium and large fixtures. It checks fixture and engine
identity before reporting latency and Python allocation ratios. Native execution
time and wrapper time are reported separately; transport latency and native
process memory are outside these measurements. See [property regressions](property-regressions.md)
for the required real-engine cases and [TEST.md](../TEST.md) for recorded results
and coverage limits.
