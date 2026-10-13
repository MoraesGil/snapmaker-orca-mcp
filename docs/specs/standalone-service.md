# Standalone Snapmaker Orca MCP

The owner requests a standalone public repository under `MoraesGil`, independent of ForjaFarm. ForjaFarm may consume this MCP later. Keep the official Snapmaker native engine and the 33 existing project/preset/slicing tools. MaxEllis remains an attributed source of helper code and feature ideas, not an alternate installed slicer.

## Release boundary

- Extract only the tracked Python MCP package from `MoraesGil/OrcaSlicer` commit `ee087a5bd7ebbb5daf445211cea1a3a9e3c931ad`.
- Maintain one shared local HTTP MCP service. Connecting clients must not start another container or editor instance.
- Keep stdio and direct CLI support for community users. HTTP uses Streamable HTTP, a single application instance, explicit Host/Origin allowlists and bearer authentication from a token file. No token is committed or baked into images.
- Preserve native engine commit `9bf2f18eaff7cc0655cbdeef6a215660085ecb0f`, executable/resources identity, read-only inputs and canonical presets. Python packaging changes must not change the native motor.
- Keep the package import/CLI name `snapmaker_slicer_mcp` / `snapmaker-slicer-mcp`; repository and product name are `snapmaker-orca-mcp` / Snapmaker Orca MCP. Version the standalone release as 0.3.0.
- Preserve all 33 tools and three resource templates. A slice captures an immutable revision before releasing the editor lock; it must not block unrelated project reads or accidentally slice a later revision.
- Do not add GUI, rendering, printer access, speculative color-head matching, full MaxEllis parity, or ForjaFarm coupling.
- Publish clear English documentation, an original cover image, examples, truthful validation limits, CI and contribution/issue guidance. Do not promise GitHub stars or universal GUI equivalence.
- Retire the old global per-session launcher only after installed HTTP acceptance, concurrent clients, fresh native slice, resource reads, recovery and source/preset hash checks.
- Delete the owner's old fork only after its code and required private evidence/runtime are preserved, the new main is published, and the installed service no longer depends on the fork. Preserve third-party authorship and license.

## Acceptance

1. The standalone unit suite passes without the old repository or its C++ source tree. Bundled native preset test fixtures have source hashes and attribution.
2. Installed wheel contains the schema/license/attribution and no private models, G-code, runtime or secrets.
3. HTTP authentication and Host/Origin restrictions reject invalid clients before file operations.
4. Three concurrent MCP clients initialize, list 33 tools and use the same backend. Container count remains one after reconnects.
5. Native `force=true` generates new G-code, properties and logs using the bound official engine; cached results verify their hashes.
6. Resources and original-source recovery work through HTTP. Editing still returns private immutable revisions and preserves original/preset hashes.
7. A slow slice does not hold the editor lock while another caller reads project state; output is linked to the captured source revision.
8. GitHub main matches the tested source and CI is green. The fork is not removed while any required dependency remains.
