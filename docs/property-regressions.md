# Property and function regressions

The integration branch is `props_test_branch`. Its children use real setting or
function names, for example `codex/props-test/outer_wall_speed` and
`codex/props-test/add_modifier`. Child ancestry expresses the branch tree;
Git cannot simultaneously store `props_test_branch` and a child ref named
`props_test_branch/outer_wall_speed`.

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
`additional_cooling_fan_speed`.

Each property worker uses its own worktree and test temporary directory.
Review and merge five property branches into the integration branch, run their
combined tests, then remove only clean worktrees whose commits are merged.
The final integration runs the entire suite on the integration branch.

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
