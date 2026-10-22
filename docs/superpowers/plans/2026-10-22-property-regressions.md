# Property and modifier regressions implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. The user explicitly selected parallel Sol 6.1 low workers with isolated property branches and five-property integration batches.

**Goal:** Reproduce setting validation, 3MF persistence and bounded real G-code effects after explicit Snapmaker engine upgrades.

**Architecture:** A schema-wide contract suite accounts for all catalog keys. Independent named JSON property cases drive generic tests; opt-in native tests use generated fixtures. A box modifier is a validated editor operation and MCP tool.

**Tech Stack:** Python 3.11+, pytest, existing MCP SDK, ZIP/XML, official installed native Snapmaker Orca, Bash.

**Spec:** `docs/superpowers/specs/2026-10-22-property-regressions-design.md`

## Global Constraints

- Parent `props_test_branch`; do not merge main, push or publish.
- Workers: `gpt-6.1-sol`, reasoning `low`, fresh context, isolated worktrees and pytest basetemp.
- Never write private sources, installed presets, engine assets, active client configuration or printer state.
- 699 catalog settings include 91 keys without label/tooltip documentation; missing native evidence stays visible.
- No byte-determinism claims. Native tests inspect commands and actual geometry-related effects.
- Preserve initial dirty native-macOS work; use only generated synthetic geometry in new native tests.

## Review Focus

- New schema keys or a changed engine identity must not be silently accepted as already tested.
- Invalid vectors, booleans masquerading as coordinates and nonfinite numbers must not mutate a revision.
- A modifier must remain a modifier, preserve hierarchy/painting and use documented local XYZ coordinates.
- Native tests must fail when the claimed G-code effect disappears, even if config comments still match.
- Normal CI skips native absence explicitly; required native integration must fail on a missing engine.

## Task 1: Preserve baseline and establish independent framework

Files: initial dirty README.md, MACOS.md, bin/snapmaker-slice.sh, service.py and test_service.py; plan/spec; new `tests/regression/`, `tests/test_schema_matrix.py`, `tests/test_property_contracts.py`, `scripts/property_coverage.py`.

Interfaces: named cases use `key`, `batch`, `valid` (objects with `value`, `normalized`, `xml`), `invalid` (values), optional `native` metadata. Case files must not contain private paths. Generic fixtures reuse public editor/process doubles.

- [ ] Preserve and commit reviewed initial local changes on the parent.
- [ ] Worker writes matrix/report tests, observes missing-framework failures, then implements independently.
- [ ] Account for all 699 keys and policy rejections; preserve sources and unrelated ZIP payloads; report the 91 keys lacking extracted label/tooltip metadata without excluding them from typed coverage.
- [ ] Run focused and full nonnative tests, review and integrate framework.

## Task 2: First five named properties

Files: one per-property JSON file in `tests/property_cases/`.

Interfaces: Task 1 case format; normalized strings and native XML strings are explicit independent expectations.

Properties: `outer_wall_speed`, `inner_wall_speed`, `layer_height`, `wall_loops`, `sparse_infill_density`.

- [ ] One isolated worker/branch per property; add valid and invalid boundary cases.
- [ ] Speeds use explicit two-element vectors; layer height rejects negative/percent; wall loops reject fraction and >1000; infill rejects outside 0..100.
- [ ] Run each focused property plus full nonnative suite.
- [ ] Review all five and merge the batch into the parent; run combined tests; remove merged clean worktrees.

## Task 3: Second five named properties

Files: one per-property JSON file in `tests/property_cases/`.

Properties: `enable_support`, `support_type`, `nozzle_temperature`, `cool_plate_temp`, `additional_cooling_fan_speed`.

- [ ] One worker/branch per property, Task 1 format, explicit normalization/XML expectations and negative cases.
- [ ] Temperature/fan vectors preserve slots. Auxiliary fan test values remain zero; out-of-range values fail.
- [ ] Run focused and full nonnative suites; review, merge five into parent, run combined tests and remove worktrees.

## Task 4: add_modifier

Files: project.py, editor.py, server.py; `tests/test_modifiers.py`; update wrong modifier fixture in test_native_ops.py if confirmed against pinned source.

Produces: spec API; allowlisted native regional overrides; atomic editor/MCP operation. Exposes part subtype for inspection.

- [ ] Write tests for direct root and external assembly, millimeter local center/dimensions, native subtype, IDs, source/payload preservation.
- [ ] Observe missing-operation failures before implementing.
- [ ] Implement box mesh and safe direct-root promotion; reject invalid units, IDs, shape, finite-number, dimension and global override cases.
- [ ] Add atomic failure, stale SHA and MCP discovery/protocol tests.
- [ ] Focused and whole-suite test, fresh review, merge function branch and remove worktree.

## Task 5: Real-engine fixture and integration

Files: `tests/native/`, `tests/conftest.py` if needed, `scripts/run_native_regressions.py`.

Produces: opt-in native tests, self-contained generated public geometry, semantic G-code helpers, engine identity report. Enabling/requiring native execution must be explicit; ordinary test runs stay engine-independent.

- [ ] Generate a closed rectangular model using official resolved U1/PLA settings, Cool Plate 50 C, auxiliary fan zero; preserve app/resources.
- [ ] Record selected installed engine version/hash and operator-declared pinned source identity.
- [ ] Test layer height, wall loops, infill density and feature speed differences using native moves; temperature/fan commands and support fixtures where supported.
- [ ] Validate modifier interior feedrate changed while exterior baseline remains; fail if XML merely stores an ignored override.
- [ ] Required native command fails on absent/incompatible engine; no silent skip as pass.

## Task 6: Documentation and final branch acceptance

Files: README.md, TEST.md, MACOS.md, docs/property-regressions.md, CI/config as required.

- [ ] README explicitly states version, tested engine and prerequisite/install options.
- [ ] Document adding cases, retesting after an explicit engine/schema update, native-only limits, modifier XYZ semantics and all results.
- [ ] Run final complete pytest suite on parent, required native integration, Bash syntax, package build/distribution checks and coverage report.
- [ ] Fresh final review; resolve findings, rerun affected checks, confirm clean status, no remaining worker worktrees and main unchanged.

## Execution state

Initial tests: 309 passed, 1 skipped in 22.82 s. See isolated SDD ledger for worker branches, commits, review results and batch checkpoints.
