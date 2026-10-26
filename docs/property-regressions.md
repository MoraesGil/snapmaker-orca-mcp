# Property and function regressions

Start each integration branch from the agreed current `main`, for example
`codex/feat/native-property-regressions`. Its children use real setting or
function names, such as `codex/props-test/sparse_infill_speed` and
`codex/props-test/native_http`. Merge five reviewed property children into that
parent, verify the combined batch, then run the complete parent suite.
The first two batches used `props_test_branch`; preserve historical branches
when they diverge from a later refactor. Child ancestry expresses the branch
tree: Git cannot simultaneously store a parent ref and a slash-prefixed child
of exactly the same ref name.

## What the checks establish

| Layer | Evidence | Limit |
|---|---|---|
| Catalog contract | Every extracted key is accounted for, with intentional policy rejection where applicable | Not every native engine setting is extracted |
| Named setting case | Accepted/rejected values, normalization, saved 3MF JSON/XML, source preservation and atomic failures | XML can store an override which the slicer ignores in that scope |
| Native integration | A generated model is sliced with the selected installed engine, then relevant G-code commands/movements are inspected | A finite fixture set cannot cover every geometry, setting combination or physical material |
| MCP/function tests | Tool discovery, validated parameters, atomic edits and revision/source association | A process double cannot establish native algorithm behavior |

Do not count an unexecuted native case as passed. A setting present in the
configuration block, a successful export, or a changed file hash is insufficient
evidence of a changed toolpath. Native path ordering can vary between fresh
runs, so comparisons target relevant commands and movement roles.

## Independent property cases

Case files are named `tests/property_cases/<native_key>.json`. Their contract is:

```json
{
  "key": "wall_loops",
  "batch": 1,
  "valid": [{"value": 4, "normalized": "4", "xml": "4"}],
  "invalid": [-1, 4.5, 1001]
}
```

Use actual native key spelling. Vectors need explicit arrays and preserve
existing slots; a speed setting may be a vector even when the UI shows a
single input. Expected normalized and XML values are written explicitly in the
case file, so a broken codec cannot supply its own expected answer.

The first batch is `outer_wall_speed`, `inner_wall_speed`, `layer_height`,
`wall_loops`, `sparse_infill_density`. The second is `enable_support`,
`support_type`, `nozzle_temperature`, `cool_plate_temp`,
`additional_cooling_fan_speed`. The third batch is `sparse_infill_speed`,
`top_surface_speed`, `internal_solid_infill_speed`, `initial_layer_speed` and
`filament_flow_ratio`.

Each property worker uses its own worktree and test temporary directory.
Review and merge five property branches into the integration branch, run their
combined tests, then remove only clean worktrees whose commits are merged.
The final integration runs the entire suite on the integration branch.

## Reproduce the integration

```sh
uv sync --frozen --extra test
mkdir -p .temp
uv run --frozen --extra test pytest tests -m "not native" -q --strict-markers \
  --junitxml=.temp/regression-suite.xml

uv run --frozen --extra test python scripts/run_native_regressions.py \
  --all-tests \
  --app "/Applications/Snapmaker Orca.app" \
  --source-commit 9bf2f18eaff7cc0655cbdeef6a215660085ecb0f \
  --report .temp/native-regressions.json

uv run --frozen python scripts/property_coverage.py \
  --junit .temp/native-regressions.xml \
  --native-report .temp/native-regressions.json > .temp/property-coverage.json
```

The first command explicitly selects only the engine-independent portable
profile. Its deselected native cases are not counted as executed or passed.
It is not the complete integration. The required native runner enables the
official engine and `--all-tests` executes every test on the integration branch;
any deselection, skip, xfail or xpass fails the complete gate. A missing engine
is an error. Omitting `--all-tests` runs only `tests/native` and is a focused run.
The runner replaces old JSON/JUnit evidence and requires actual executed cases
with zero skipped, failed or errored cases, even when native case records claim
success. It saves JUnit and test totals beside the JSON report.
It records the Git revision,
measured executable/resource hashes, declared native source, app version and
per-case scope. It does not install or switch the slicer, call a running MCP
service, use private models or send jobs to a printer.

Keep both reports. `property_coverage.py` attributes native property results
only from the explicit successful runner report for the matching schema source
commit; JUnit test names alone are not semantic attribution. Failed/unexecuted
cases remain missing. Auxiliary-fan evidence is specifically **OFF-only**;
other geometries, values and combinations require additional fixtures.

## Maintained real HTTP/native regression

`tests/native/test_native_http.py` starts a separate CLI server on an ephemeral
loopback port with a fresh token, temporary artifacts and synthetic 3MFs. It
does not read client registrations, installed credentials or the persistent
MCP service. It uses the explicitly bound official engine without a process
double. The same required runner above executes this test.

The single complete workflow verifies all 34 tools are invoked successfully,
including edit/slice revision association, actual wall feedrates and nozzle
temperature, regional modifier speeds, exact source recovery and resource
content, object/plate edits and temporary preset CRUD. Bounded native scenarios
also check arrangement of overlapping boxes, orientation of an on-bed box,
adaptive layers/reset and a Z-band. Authentication, Host/Origin, stale revisions,
failed-batch atomicity and prohibited post-processing have rejection assertions.

The workflow records `mcp_http` as passed only after every scenario and process
cleanup finishes. The required report now needs **17 cases**: fifteen property
comparisons, the modifier and the HTTP workflow. Missing, skipped or stale
cases fail completion. The property coverage report lists HTTP as a function
case; invoking tools does not attribute additional property semantics.

The server's native workers create separate sessions. Test-only process
supervision tracks its unique scratch directory, descendant relationships and
PID start/command identity, then terminates detached workers and waits for its
server. Controlled Python child-process tests cover timeout and early-parent
exit cleanup without requiring the slicer. Full-width `ps` snapshots preserve
long commands even with `COLUMNS=80`; regression cases verify this Linux failure
mode and that changed start-time/command identities never receive signals.

The portable public CI profile runs these lifecycle and pure oracle tests with
zero skips. Native cases are selected through their `native_engine` dependency
only for the complete engine-enabled run. Invoking selected native cases without
a configured engine fails. Running the required native command locally or on an
explicitly provisioned native runner remains mandatory; portable CI cannot stand
in for this evidence. No official app is downloaded or installed by these tests.

The five new property modules have independent oracles and negative examples
that reject comments, travel-only or unchanged output. Speeds target their
specific extrusion role/layer. The flow comparison uses identical outer-wall
paths and deposited E per XY length: 0.8 versus 1.2 must scale by 1.5, within
0.2% for native E rounding. Its fixture fixes outer-wall speed at 20 mm/s and
sets the volumetric cap to 100 mm3/s; this is a synthetic isolation condition,
not a material preset recommendation. Top/internal/first-layer speed fixtures
disable cooling slowdown locally and keep the official volumetric limit.

Current native catalog coverage is **15 of 699 properties**; **684 keys** still
have no independent native semantic fixture. Finite values, one material and
small geometries do not certify all combinations or physical print quality.

## Modifier coordinates and scope

`add_modifier` adds an axis-aligned box with `center=[x,y,z]` and
`dimensions=[width,depth,height]`, in millimeters. The center is absolute in the
object's local coordinates, before the instance's rotation, scale and position;
it is not a point in plate coordinates. The modifier is shared by all instances
of that object. Its native part subtype is `modifier_part`.

Only documented regional overrides are accepted. A printer/global setting
cannot become a regional speed setting merely by writing it into part metadata.
Invalid dimensions, nonfinite coordinates, unsupported units/overrides and
stale revisions must leave the current 3MF revision unchanged. Regenerate G-code
after adding a modifier. The native test must observe the requested speed
inside the modified region and the baseline behavior outside it.

The initial supported overrides are `outer_wall_speed`, `inner_wall_speed`,
`sparse_infill_speed`, `top_surface_speed` and `internal_solid_infill_speed`.
Use explicit arrays matching the current process flow variants, even when the
UI shows a single speed. For a project whose `outer_wall_speed` has two variants:

```json
{
  "name": "add_modifier",
  "arguments": {
    "project_id": "<id returned by load_model>",
    "object_id": 1,
    "center": [6, 12, 2.5],
    "dimensions": [12, 30, 10],
    "changes": {"outer_wall_speed": [20, 20]},
    "expected_sha256": "<current revision sha256>"
  }
}
```

The same fields can be submitted in an `edit_project` operation with
`"operation": "add_modifier"`. The number of flow variants is independent of
the number of physical filament slots; preserve the current setting's array
shape. `list_objects` includes each part's native subtype for inspection.

## Engine upgrades

1. Obtain and select the new official app explicitly, preserving the previous
   binding and evidence. Do not re-label a binary with an unverified source
   commit or edit an old manifest to hide changed hashes.
2. Regenerate the catalog from the exact source and inspect added, removed,
   changed and still-unparsed keys. Update the checked-in coverage expectations
   deliberately; do not automatically bless a new schema fingerprint.
3. Run the complete engine-independent suite. Add named cases for new properties
   and protocol/atomicity tests for new functions.
4. Run required native integration with the new app and retain its identity,
   commands and per-case result report. Investigate failed semantic assertions;
   configuration equality does not override a failed G-code observation.
5. Record the actual tested versions and remaining gaps in TEST.md before
   merging a validated batch. Printer execution is a separate operation.

The baseline catalog is pinned to 2.4.1 / `9bf2f18eaff7cc0655cbdeef6a215660085ecb0f`:
699 extracted keys. The legacy `_meta.unparsed_keys` field lists 91 of those
same keys without extracted label and tooltip text; their types are cataloged
and they remain covered by the contract matrix. This field does not mean 91
extra or unsupported settings. Existing unknown historical values can remain
preserved in a project.
