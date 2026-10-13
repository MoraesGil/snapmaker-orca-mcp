# Standalone release implementation plan

> **For agentic workers:** Independent transport and community-documentation tasks use separate worktrees. Root integrates, validates, publishes and retires the previous installation.

**Goal:** Publish and install Snapmaker Orca MCP as an independent, shared local service under `MoraesGil/snapmaker-orca-mcp`.

**Architecture:** The Python package wraps the same immutable official native engine. A single authenticated Streamable HTTP server shares its editor and slicer across clients; stdio remains available. ForjaFarm remains an independent future consumer.

**Tech Stack:** Python 3.11+, MCP SDK 1.30, pytest, official Snapmaker Linux engine, Docker/OrbStack and local-mcp adoption.

**Spec:** `docs/specs/standalone-service.md`.

## Constraints

Use version 0.3.0; keep the 33 existing tools/resources and native commit `9bf2f18`. Never write canonical presets or input 3MFs. Never publish private native fixtures or tokens. No ForjaFarm modifications or physical printer operations. Preserve AGPL attribution.

## Task 1: Extract a standalone package

- [x] Archive only tracked `tools/slicer_mcp` files from the old main into the new repository directory.
- [x] Update package version/URLs and Docker wheel version; ignore `.temp`, runtimes, artifacts and secrets.
- [x] Run `uv run --extra test pytest tests -q`; record the three failing tests that relied on paths in the old source tree.
- [x] Replace obsolete schema paths with the standalone package path. Assert the recorded external native source hash, rather than requiring C++ files in this Python repository.
- [x] Include only the four official Generic PLA inheritance fixtures used by the catalog test, with a provenance manifest and unchanged payload hashes.
- [ ] Run the complete baseline suite and commit the extracted package before creating transport/documentation worktrees.

## Task 2: Shared authenticated HTTP transport

Files: `src/snapmaker_slicer_mcp/http_server.py`, `cli.py`, `editor.py`, `tests/test_http.py`, `tests/test_editor.py`.

- [ ] Add failing tests for unauthorized/invalid-origin HTTP calls, MCP initialization/tools/resources and concurrent clients on one app.
- [ ] Add CLI `serve --transport streamable-http --host 0.0.0.0 --port 8080 --auth-token-file PATH --allowed-host HOST --allowed-origin ORIGIN`; stdio remains the default.
- [ ] Serve `/mcp` with one FastMCP application; expose a minimal `/health` response. Read bearer token from the specified file and reject an empty/missing token in HTTP mode.
- [ ] Configure explicit Host/Origin restrictions, independent of bearer validation. Do not log secrets.
- [ ] Add a blocking-slice regression test; capture immutable source/selection under the editor lock and run the slice after releasing it.
- [ ] Verify stale-revision behavior and native result provenance remain unchanged; run focused and full tests, then commit.

## Task 3: Community documentation and visual identity

Files: `README.md`, `LINUX.md`, `TEST.md`, `THIRD_PARTY.md`, `CONTRIBUTING.md`, `docs/`, `.github/ISSUE_TEMPLATE/`.

- [ ] Lead with problems solved: agent-controlled native 3MF slicing, safe project edits, native plate arrangement and inspectable G-code/settings.
- [ ] Document single-client stdio and shared authenticated HTTP separately, with valid standalone paths and installation commands.
- [ ] Generate an original cover image using imagegen; copy the selected local output into `docs/assets/hero.png` and inspect it visually.
- [ ] Clearly distinguish shipped capabilities from future integration, renderer and physical-printer matching.
- [ ] Preserve helper/native fixture attribution and explain how to regenerate the schema from an explicitly supplied official source checkout.
- [ ] Add concise contributor and issue guidance; verify local links and examples, then commit.

## Task 4: Install, publish and retire

Files: `.github/workflows/ci.yml`, `compose.yml`, `scripts/`, operational installation outside the repo.

- [ ] Run unit tests/build/wheel inspection in the integrated source. Configure CI to reproduce public tests on Linux and macOS.
- [ ] Build the Python image with the retained official runtime; compare native executable/resource hashes to 0.2.0.
- [ ] Start one persistent Compose service with read-only inputs/presets/token and a writable artifact directory; adopt it through local-mcp with no Tail publication.
- [ ] Exercise three concurrent HTTP clients, fresh slice, resources, recovery, editing and reconnects. Prove source/preset hashes and a stable single-container count.
- [ ] Publish the independent public repository on main under `MoraesGil`; verify remote revision and CI.
- [ ] Back up and switch the three client registrations to the shared HTTP endpoint. Retire the old launcher only after read-back and new transport validation.
- [ ] Audit/remove any actual MaxEllis registrations/packages; preserve attribution in the new source.
- [ ] Preserve the old fork's Git history and required ignored evidence/runtime. Confirm no outstanding dependency, then delete the owner's old fork and retire its local checkout.
- [ ] Update the global TOOLING map and operational manifest with new canonical source, URL, version, tests and limits.
