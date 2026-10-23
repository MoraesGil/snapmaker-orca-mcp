# Optional custom tower plugin

Enable `custom-tower` to build an ordinary sacrificial priming object from the
actual native G-code schedule. The official engine generates all toolpaths and
infill. This plugin never rewrites G-code or controls a printer.

```sh
uv run --frozen snapmaker-slicer-mcp --engine /absolute/engine.json \
  --artifacts /absolute/artifacts serve --no-update-check --plugin custom-tower
```

The option also works with authenticated `--transport streamable-http`; the
existing Host, Origin and bearer policy applies. It adds two MCP tools to the
same editor, native admission queue and artifact store. Default tool discovery,
REST routes and the direct Bash launcher are unchanged. An already running service
must be explicitly restarted with the option; installing code alone does not
enable the plugin. This branch feature is not a new published package release.

## Add or recalculate

Load a saved project with `load_model`, inspect `list_objects` / `list_plates`,
then call:

```json
{
  "name": "add_custom_tower",
  "arguments": {
    "project_id": "<loaded-project-id>",
    "plate": 2,
    "expected_sha256": "<latest-working-revision-sha256>",
    "options": {
      "model_tools": [0, 2],
      "interface_tool": 1,
      "primary_object_id": 35,
      "minimum_width_mm": 28,
      "maximum_width_mm": 56,
      "brim_mm": 6,
      "purge_volume_mm3": {"0": 12, "1": 12, "2": 12},
      "structural_infill_percent": 10,
      "speed_mm_s": 40
    }
  }
}
```

IDs above are illustrative; obtain real IDs from the loaded project. Tools are
zero-based logical filament slots. The supported mode requires a verified
one-to-one physical head assignment, at most three model colours in one material
family and one different interface material. The mapping does not assume T0 or
PETG: a PETG model with PLA interface uses the same role rules. Declaring different
families is not a physical certification of their adhesion or release behaviour.

The result is either `digitally_validated`, with a new private revision, native
job, input/engine hashes and audit, or `rejected`, with the working revision
unchanged. Invalid/unsupported inputs and native process failures raise an error.
Run artifacts remain private for diagnosis; source files and installed presets
are read-only. `expected_sha256` rejects a stale edit, including a model changed
while the native process was running. Only the exact checked native source
snapshot is published as the working revision.

`custom_tower_status(project_id)` reports whether recalculation is required.
Geometry, painting, support, orientation, scale and configuration edits can
invalidate a receipt. Call `add_custom_tower` again with the latest SHA. The
plugin removes its old signed auxiliary before the fresh prepass and replaces
it with exactly one tower on the selected plate. Editor `slice_project` and
`apply_and_slice` reject stale or imported unvalidated signed towers. Raw
`slice_3mf` is still a generic native operation; its successful result alone is
not a custom-tower approval.

## Material and geometry rules

1. The prepass disables the native tower and excludes only owned auxiliaries.
   A real architectural model called “tower” remains part of the calculation.
   The exact name `__SYNTH_TOWER_V1__<token>` must match producer/role/schema
   metadata and a geometry digest. This is an integrity/ownership convention,
   not a cryptographic authentication boundary. Damaged claimed outputs fail
   explicitly instead of being silently deleted.
   Older unsigned auxiliaries require an explicit `legacy_tower_ids` list with
   the current `expected_sha256`. Without that declaration, names/shapes do not
   authorize deletion. This migration changes only the private working revision.
2. The three lobes rise together. Model-colour regions nest from the exterior
   towards a single filled central interface region. Missing colours are
   reassigned only to materials already used externally at that height. No
   interface material touches the first layer or forms an entire weak layer
   across the exterior. The solid region volumes cover the section; native
   infill can be sparse, so “filled” does not mean 100% solid throughout.
3. The foundation is twice the final XY width; brim is additional. The primary
   model's measured envelope drives a causal taper. A constant section needs
   an explicitly requested `foundation_height_mm` flare. Impossible dimensions,
   unsupported narrowing and insufficient deposition capacity are rejected.
4. The selected Cool Plate remains selected. Its initial/normal bed targets come
   from the main model material. Only the interface slot's project-local bed
   entries are aligned to those targets; no global preset is changed. Nozzle
   temperatures remain the supplied material values and are checked during
   actual deposition by height/tool. Auxiliary fan activation is rejected.
   Bed vectors belong to the whole project: if an adjustment could affect
   another production plate, split the material roles into separate projects
   or align the existing project explicitly. The plugin refuses that implicit
   cross-plate change.

The default 12 mm³ per change is a **comparison budget**, not a physically
calibrated cleaning minimum. Set `purge_volume_mm3` from material/head trials.
The geometry estimate chooses density; the final gate measures commanded
positive XY extrusion with filament diameter, M221 and retraction repayment.
The filament flow ratio is already represented in emitted E and is not applied
twice. Native density corrections are bounded to four attempts maximum.

## Immediate preparation and structural growth

After every real T change, the complete tower pass, including contour and
infill, must precede model/support deposition. The audit compares all native
phases, rejects additional tower-only changes and same-phase returns, and
checks a measured volume budget for every preparation pass. Native per-layer
sequence overrides are derived afresh, never copied from another model.

A continuous ordinary tower must also grow when a head continues onto the next
layer. These visits receive only `structural_infill_percent` and a **zero priming
budget**. They do not prefill a future colour's cleaning region. The report
lists them separately. A literal object that extrudes exclusively after T while
remaining continuously supported is not provided by this native object backend.

`speed_mm_s` sets the tower's regional speeds. Native project first-layer speed
caps still control the first layer; the audit checks those inherited caps there.
Other model speeds and support masks are preserved. An ordinary object does not
automatically acquire native flush, ramming, flow calibration or firmware nozzle
wiping semantics. `flush_into_objects` and `flush_into_support` are disabled on
the auxiliary; it provides its own measured deposition pass.

## Assembled demos

An existing authoritative assembled demo stays enabled on **plate 1**, titled
as a nonprinting preview. Never set its instances to `printable=0`. For several
sizes, reference the largest production size. Supply `demo_object_ids` and
`demo_source_plate` to verify matching leaf geometry, painting and physical
scale and enable only those plate-1 instances. Larger scaled instances of the
same mesh resources are rejected as a smaller reference. The caller identifies
the largest source plate when sizes use unrelated/baked meshes. The plugin
preserves an existing assembly placement; it cannot infer mechanical assembly
from arbitrary arranged pieces or replace a CAD assembly review. A mismatching
demo fails instead of being presented as identical. It does not create a fake
preview, disable a demo or schedule plate 1 as the production target.

## Optional hybrid advice

Add an explicitly configured `hybrid` object:

```json
{
  "ranker_url": "http://127.0.0.1:9000/v1/rerank",
  "llm_url": "http://127.0.0.1:9001/v1/chat/completions",
  "llm_model": "operator-selected-model",
  "timeout_seconds": 10
}
```

URLs are examples, not services started by this project. The ranker endpoint
accepts `query` and `documents`, returning each candidate's `index` and
`relevance_score`. The LLM endpoint accepts an OpenAI-compatible chat request
and returns JSON `{"candidate_id":"margin-0"}` (or `margin-10` / `margin-20`).
An optional `ranker_model` is forwarded. There is at most one call to each
endpoint; timeout, malformed advice or unavailable providers retain deterministic
ordering. Redirects, proxy environment inheritance, URL credentials, query
strings and responses over 64 KiB are refused.

Only anonymous finite density candidates are sent. No mesh, G-code, private
path, project name, credentials or geometry is sent. Providers can reorder
candidates but cannot change material roles, geometry limits, temperature,
sequence or approval gates. No endpoint is contacted in deterministic mode;
neither Jina nor an LLM nor Pydantic AI is required. Pydantic validates requests
and advisory choices in the existing Python service.

## Supported domain and remaining proof

This backend is pinned to official engine source `9bf2f18` and saved Snapmaker
project format v1, rectangular Cool Plate, millimetres, standard filament flow
modes and shared constant layer heights. It rejects adaptive/independent support
layers, repeated tool visits at one Z, unknown print-time priming extrusion,
unfittable brim/clearance and unsupported motion modes. Occupancy uses
conservative deposited footprint bounds, including supports; this can reject
a geometrically feasible packing rather than accept a collision. Private
source IDs, component payloads, painting, ordinal ranges, cut/brim information
and profiles are preserved when adding the first-in-order auxiliary.

Actual commanded order, volumes and targets are digital evidence. Cleaning
quality, polymer release, tower strength, travel strings, skip behaviour and
motion inside opaque firmware macros still require physical trials. No current
claim covers every arbitrary 3MF or material combination. Support-interface
unification is a separate model-preparation step; do it before the prepass so
its real material schedule is included. This plugin does not weld chain holes
or silently redesign supports.
