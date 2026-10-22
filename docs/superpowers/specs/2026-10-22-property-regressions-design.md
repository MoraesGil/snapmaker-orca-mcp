# Repeatable property and modifier regression design

The user authorized implementation, Sol 6.1 low workers, property/function branches based on `props_test_branch`, integration in batches of five, and removal of merged worker worktrees. The integration target is `props_test_branch`; merging to `main`, pushing, publishing and printer operations are outside this task.

## Outcome

Make setting edits independently reproducible across engine upgrades. Distinguish validation and saved-3MF persistence from actual native slicing and semantic G-code observations. A successful settings export or different file hash does not prove a toolpath change.

## Existing evidence

- Initial checkout: main at `0b29df3`; local native-macOS changes were already present.
- Baseline: 309 passed, 1 skipped; native boundaries in this public suite are doubles.
- MCP package 0.4.0; schema Snapmaker Orca 2.4.1 / `9bf2f18eaff7cc0655cbdeef6a215660085ecb0f`.
- 699 extracted settings, including 91 missing extracted label/tooltip text (`unparsed_keys` is the legacy field name); do not claim every engine setting is covered without a source comparison.
- No current add-modifier operation. Existing object/part settings can be changed.

## Regression layers

1. A schema-wide contract matrix must account for every catalog key: valid value, invalid value, normalization, saved configuration and immutable source. Intentionally prohibited settings get rejection tests. Missing documentation metadata is reported separately; these keys still receive normal type/3MF contract coverage.
2. Named property cases live independently in `tests/property_cases/<key>.json`. The first two batches contain five properties each. Each worker owns one case file. A generic pytest runner checks validation, 3MF JSON and XML persistence, source preservation, stale-G-code invalidation, and atomic failure.
3. Opt-in real-engine tests generate their own synthetic geometry and resolve only official installed presets. They must not depend on private models, a running server or credentials. Native absence is an explicit skip in the normal suite; the release integration command requires a real engine and cannot report success from skips.
4. Native assertions examine relevant commands/movements (layer Z, feature feedrates, infill paths, temperature/fan commands, modifier inside/outside geometry), not whole-file equality or comments alone.
5. A checked-in coverage manifest and report command name passed contract cases, native cases, blocked settings, undocumented catalog keys and missing semantic native coverage. Adding schema keys requires a deliberate coverage update.

## Modifier operation

`add_modifier(project_id, object_id, center, dimensions, changes, name="Modifier", expected_sha256=None)` adds a box with finite XYZ values and strictly positive dimensions, in millimeters. `center` is the absolute center in the object's local coordinate system, before instance transforms; all instances share this volume. Shape and coordinate space are explicit in documentation.

Store a closed box mesh as a new root-resource object and a sibling component under the existing root. Use native `subtype="modifier_part"`. Convert a direct root mesh to components while preserving its root ID, build items, original mesh payload and metadata. Preserve pre-existing external meshes, painting, hierarchy and transforms. Validate all input before changing state. Reject unsupported units, invalid numbers, unknown root IDs, invalid dimensions, unsafe/global/non-regional overrides, stale revisions and unknown arguments atomically.

The initial modifier overrides are the regional speed keys independently verified against the pinned importer and G-code; do not silently accept every schema key. Expose the operation both as a direct MCP tool and in `edit_project`.

## Branch and test isolation

Parent branch: `props_test_branch`. Children use real names (`codex/props-test/layer_height`, `codex/props-test/add_modifier`, etc.). Git cannot store both a `props_test_branch` ref and a `props_test_branch/...` ref, so the graph is expressed by ancestry rather than ref path prefixes. Each worker gets an isolated worktree under ignored `.temp/props-worktrees/` and a private pytest temp root. Worker implementation/tests are disjoint. Only the controller merges children. Native execution on a worker must use the worker's source, not an editable installation pointing to the primary checkout.

Preserve the initial local changes on the parent before creating child worktrees. Merge each property batch of five only after focused checks, then run the combined suite. Remove a worker worktree only after its commit is an ancestor of the integration branch and its working directory is clean.

## Deliverable and limits

README states MCP version, tested engine version and commit, the required matching Snapmaker Orca install, and extra Python/MCP setup versus direct Bash. TEST.md records commands, actual counts and native evidence without changing historical claims. The final integration result belongs to the final parent HEAD. No GUI equivalence, universal setting-interaction coverage or physical print certification is implied.
