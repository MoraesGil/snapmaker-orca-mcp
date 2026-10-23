# Slicer architecture design

Status: proposed for owner review; implementation has not started.

Base: merged PR #1, commit `dde96a946451befac37a5021a1ab17749f0cc31b`.

## Intent and accepted scope

Organize Snapmaker Orca MCP around its editing and slicing domain, with explicit application boundaries and replaceable infrastructure. Adopt the standalone FastMCP framework while preserving observable MCP, REST and CLI behavior. The owner approved merging the slicer work and proceeding to architecture after clarifying its scope.

The product remains a local/headless interface to the official Snapmaker Orca slicing engine. Source 3MFs and installed presets remain read-only; private revisions preserve geometry, painting, IDs, transforms and provenance. This phase does not add Moonraker/Klipper clients or physical printer operations.

## Current baseline

- Package `snapmaker-slicer-mcp` is 0.4.0, Python 3.11+, with SDK `mcp>=1.30,<2` and the SDK-bundled FastMCP. The repository uses uv and pytest.
- There are 34 MCP tools, three resource templates and eight REST routes, plus the installed Python CLI and independent Bash slicing launcher. Their names, schemas, defaults, resource URIs and documented behavior form the compatibility boundary.
- `Project` combines 3MF storage, XML edits, geometry operations and serialization. `Editor` combines use-case coordination, revision persistence and direct document manipulation. `Slicer` combines admission, caching, artifact storage and subprocess execution.
- MCP and REST share the same backend. Bounded G-code line extraction is duplicated; REST additionally protects downloads with a verified snapshot. Consolidation must retain that protection.
- Required native integration on clean PR head `67a2728` passed 1,271 tests with one absent-private-fixture skip. The merged tree is identical. Native evidence covers ten property cases and one modifier case, not every catalog property or physical printing.

The architecture assessment is modular code with partial SOLID/DRY application. File moves alone do not establish DDD or Clean Architecture.

## Approach and trade-offs

Use an incremental refactor: capture the existing public contracts, isolate infrastructure behind application-owned ports, extract domain rules, then migrate the transport framework. Each step must leave the suite usable. Protocol changes and responsibility changes should be separate commits so failures can be attributed.

Two alternatives were considered: retain SDK v1 and defer the framework migration, or migrate the framework before separating responsibilities. Retaining v1 reduces the immediate transport change but postpones the requested framework standardization. Migrating first introduces new protocol behavior before the current coupling is reduced. The selected order uses the existing tests and explicit boundaries to check the framework transition.

This is one Python package and one backend process. Use domain modeling where it captures real invariants; do not introduce separate services, an event bus or a generic dependency-injection framework.

## Domain model and responsibilities

Use three cooperating areas: project editing, presets/settings and slicing jobs. Their common language includes project, revision, plate, object, part, modifier, filament slot, flow variant, preset, slice request and generated artifact.

Introduce small immutable value types for project/object/plate identifiers, SHA-256 revision tokens, positive XYZ box dimensions, finite object-local coordinates and bounded G-code line ranges. Keep valid negative modifier centers, existing slot numbering and current native string/array shapes. Domain types validate meaning; transport schemas validate wire input.

Project revisions are identified by immutable content hashes and an explicit source/revision relationship. Editing a batch either publishes one validated new revision or leaves the current revision unchanged. A selected plate is editing state; a logical material slot is not a physical toolhead assignment.

Settings normalization receives the extracted schema as data. It must not load package files implicitly. Keep intentional policy rejection of transport credentials, external scripts and printer identity changes, and the explicit custom-G-code opt-in.

Domain code uses ordinary Python types/dataclasses and pure functions. It must not import MCP, Starlette, Pydantic, filesystem/process libraries or XML/ZIP implementations. The 3MF representation stays in a format adapter; do not construct a second in-memory copy of every mesh merely to create domain objects.

## Application and dependency direction

Application use cases coordinate loading, reading, editing, preset drafts, slicing, recovery and artifact queries. They depend on domain types and narrow ports. They do not instantiate infrastructure or reach into another service's private lock/state.

Required port responsibilities are:

| Port responsibility | Boundary |
| --- | --- |
| Project revision repository | Source snapshots, revision state, compare-and-swap publication and selected-plate persistence |
| 3MF document reader/editor | Describe and apply typed edits while preserving unknown payloads and metadata; write staged revisions |
| Native slicing/layout runner | Execute explicitly bound native slice, arrange/orient operations and return typed results/deltas |
| Artifact repository | Cache/job records, retained outputs, canonical paths and verified read snapshots |
| Preset repository | Read-only installed inheritance and artifact-local draft persistence |

Split read/mutation or slice/layout capabilities where consumers need different operations. Do not make each use case depend on a single interface containing every operation in the product. Concrete filesystem/3MF/native implementations may satisfy several narrow protocols.

The composition root constructs concrete adapters and injects them into use cases. Only that root knows the complete assembly. Both MCP and REST receive the same application/backend instances and native admission coordinator.

## Package organization

| Area | Owns |
| --- | --- |
| `domain/` | Value types, editing/settings invariants and domain results |
| `application/` | Use cases, typed commands/results and application-owned port protocols |
| `adapters/three_mf/` | ZIP/XML representation, component/plate/layer edits and serialization |
| `adapters/native/` | Engine identity, resources, subprocess lifecycle and native geometry translation |
| `adapters/storage/` | Filesystem revisions, cache, artifacts and preset drafts |
| `adapters/mcp/` | FastMCP assembly, resources and tool groups for projects, settings, presets and slicing |
| `adapters/http/` | REST routes, HTTP lifecycle and access policy |
| `bootstrap.py` and `cli.py` | Application assembly and installed command entry point |

Preserve supported Python entry points with thin compatibility facades where necessary. Move tests of private helpers to their actual owners; do not keep cross-layer private imports just to preserve an internal filename. Retain one owner for XML namespace synchronization and one shared native admission policy.

## Framework migration

Target standalone FastMCP 4.1.3, with an explicit compatible major-version bound and a reviewed uv lockfile. It builds on MCP SDK v2. Declare libraries imported directly by the project as direct dependencies rather than relying accidentally on transitive installation.

Group tools by capability and compose them without changing public tool names or adding namespace prefixes. Keep `load_model`, `edit_project`, `add_modifier`, slicing and preset operations consistent with the current contract inventory. Keep all three `slicer://` resource templates unchanged.

Replace SDK-v1-specific HTTP assembly with the framework's supported HTTP application/lifespan API. Preserve authenticated `/mcp`, the eight REST paths, exact Host/Origin checks, bearer handling and minimal `/health` behavior. Starting a remote transport without its required access configuration must still fail before native work.

SDK-v2 Python attribute renames are internal changes. Do not use them as a reason to silently change existing client-visible schemas or data. Test both a supported contemporary client and an isolated SDK-v1 client against the new server. If the selected framework cannot preserve the agreed client contract, expose that incompatibility for a decision instead of weakening the contract tests.

## Native engine boundary

Keep the official executable, matching resources and measured hashes explicit. The wrapper's framework migration does not update or rebuild Orca. Preserve timeout/process-group cleanup, private datadir/TMPDIR behavior, captured diagnostics and the explicit compatibility flag.

Use the native CLI for the operations it exposes. Editing 3MF configuration/modifiers remains a document operation followed by a fresh slice. Network methods present in the desktop application's Moonraker client are not implicitly available through the CLI.

Record a capability inventory mapping each existing MCP operation to a document edit, native CLI operation or retained-artifact query and its regression evidence. This inventory must not imply parity with every desktop GUI feature or every Moonraker endpoint.

## Preserved data, concurrency and errors

- Preserve canonical source/preset hashes, component roots, object/part IDs, paint, transforms and unknown archive members. Successful edits invalidate old sliced data exactly as before.
- Preserve existing artifact layouts and reopen retained projects/jobs. Schema migrations, if required, must be explicit, tested and reversible; do not discard history during a package move.
- Capture the revision being sliced. A concurrent later edit must not change that slice's input. Native work stays serialized while unrelated reads remain available.
- Preserve stale-SHA rejection, atomic batches and existing error meanings across MCP, REST and CLI. Framework-specific exception formatting must not leak credentials or replace native diagnostics.
- Consolidate line-range validation and extraction while keeping REST snapshot/hash verification and response resource cleanup. Shared code must not downgrade an adapter's existing integrity guarantees.

## Validation and acceptance

Before changing behavior or dependencies, capture consumer-facing contracts: tool names/input schemas/defaults, resource templates, REST paths/security/errors and CLI entry points. Use these as compatibility regressions, not snapshots of private implementation details.

Add automated architecture tests that resolve imports and forbid domain-to-framework/infrastructure and application-to-adapter dependencies, including imports used only for annotations. Verify composition through explicit construction; do not substitute a large new service locator for the old coupling.

Use focused failure/passing regressions for extracted invariants and new ports, then the complete public suite. Preserve HTTP tests for malformed/duplicate credentials and headers, corrupt artifacts, verified snapshots, cancellation/admission and concurrent reads. Add the legacy-client compatibility check for stdio and Streamable HTTP where supported by the existing contract.

Run wheel/source builds, distribution-boundary checks, Bash syntax validation and the required native `--all-tests` integration at the final implementation HEAD. Attribute native evidence to that SHA/dirty state, installed engine and fresh run. Keep reports local and publish only sanitized aggregates.

Acceptance requires all existing behavior tests plus the new architecture/contract tests to pass, the 34 tools/three resource templates/eight REST routes to retain their contracts, and all eleven required native cases to execute. Retain explicit coverage gaps: 689 catalog settings lack native semantic fixtures, auxiliary cooling evidence is OFF-only, and digital tests do not certify GUI equivalence or a physical print.

## Delivery and review boundary

This document is the review artifact for the architectural direction. After owner review, write the implementation plan with dependency order, file ownership and verification gates. Implementation then proceeds in the named architecture worktree with Conventional Commits and a PR following the repository contribution workflow.

The next PR may remain a draft while design/implementation review is pending. It must describe the final delivered scope and actual checks; a committed design is not an implemented architecture.

## References

- [FastMCP migration from SDK v1](https://gofastmcp.com/getting-started/upgrading/from-mcp-sdk-v1)
- [FastMCP server composition](https://gofastmcp.com/servers/composition)
- [Official Python SDK migration](https://py.sdk.modelcontextprotocol.io/migration/)
- [Existing property/native regression contract](../../property-regressions.md)
- [Repository contribution workflow](../../../contribution-workflow.md)
