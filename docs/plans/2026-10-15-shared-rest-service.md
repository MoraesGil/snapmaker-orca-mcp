# Shared REST and MCP Implementation Plan

> **For agentic workers:** Execute the approved plan in this session; use the existing test-driven workflow and request an independent read-only code review before publication.

**Goal:** Expose saved3MF slicing and artifact inspection/downloads through REST and MCP in one installed service, and clarify the Agentic Slicer positioning.

**Architecture:** Add Starlette REST routes to the existing authenticated MCP application. Both adapters receive the same `Slicer`; its native process, lock, artifacts and cache remain unchanged. HTTP slicing returns a completed200 response, with artifact links for app consumers.

**Tech Stack:** Python 3.11+, existing MCP 1.30/Starlette/Pydantic/httpx/pytest, official Snapmaker Linux bundle and Docker/OrbStack.

**Spec:** `docs/specs/shared-rest-service.md`.

## Constraints

- Package 0.4.0, unchanged native 9bf2f18 and 33 tools / 3 resource templates.
- No GUI, automatic computer-use fallback, bundled skill, new dependency, preset mutation, ForjaFarm edit or extra container.
- Reuse the HTTP bearer/Host/Origin middleware; read secrets only from the current private deployment.
- Keep source/preset hashes and existing artifacts/cache valid.

## Task 1: Prove the REST boundary and shared backend

Files: create `tests/test_rest.py`; modify `src/snapmaker_slicer_mcp/http_server.py`; create `src/snapmaker_slicer_mcp/rest.py`.

- [ ] Add ASGI tests expecting `POST /api/v1/slices` to return200, preserve source, provide retrievable G-code/source/props and bounded lines.
- [ ] Run `uv run --locked --extra test pytest tests/test_rest.py -q`; observe missing-route404 failures before adding production REST.
- [ ] Test authorization/Host/Origin rejection before any job creation; strict requests, missing inputs/jobs/plates, corrupt artifacts/manifests, native failures/timeouts and OpenAPI path/request-schema agreement.
- [ ] Test a REST slice followed by an MCP cache hit through the same application, then a slow REST slice with concurrent MCP reads. Native subprocess calls remain the existing process-double fixture.
- [ ] Implement `rest_routes(slicer)` and attach its routes to `build_http_app` after `streamable_http_app()`. Use `asyncio.to_thread` for native and file/hash work; JSON and verified-snapshot responses remain inside the same middleware. Share async admission between both adapters so queued slices do not occupy read threads.
- [ ] Run focused REST+HTTP tests and the public suite; inspect source preservation, failure diagnostics and event-loop availability.

## Task 2: Package and document the actual product

Files: `pyproject.toml`, `uv.lock`, `__init__.py` if versioned, `Dockerfile`, `.dockerignore`, `compose.yml`, `README.md`, `LINUX.md`, `docs/rest.md`, `docs/usage.md`, `TEST.md`.

- [ ] Bump wrapper0.4.0 without updating dependencies/native bundle. Update wheel/image references and validate `uv sync --locked`.
- [ ] Document authenticated REST requests/downloads, synchronous behavior/timeouts and error codes. Add “Agentic Slicer” positioning with selective settings, bounded G-code reads and atomic edits; state that no comparative token benchmark exists.
- [ ] Explain one standalone source repository, deployment-data directory, GUI-opening compatibility, separate computer-use fallback and no bundled skill. Explain ForjaFarm's optional preparation role.
- [ ] Validate local Markdown links, build distributions and run `scripts/check_distribution.py`; retain AGPL/Max/Snapmaker attribution and original artwork.
- [ ] Request independent read-only review, fix material findings and commit tested source/docs on the owner's main.

## Task 3: Validate and promote the installed shared service

Files: operational Compose/manifest/runbook/evidence, global TOOLING inventory.

- [ ] Build0.4.0 with the retained native runtime and compare official executable/resource hashes.
- [ ] Check the current container for active native jobs; deploy only when idle. Preserve the current URL, token, mounts/data and one backend; refresh the local-mcp adoption record.
- [ ] Authenticate through REST and MCP, force a fresh native source slice, verify cross-protocol cache/resources/download hashes and source/preset preservation. Confirm three MCP clients and container count remain one.
- [ ] Publish main, inspect CI, create0.4.0 release assets/checksums, update repository description/topics and local documentation. Keep0.3.0 image/package for rollback.
- [ ] Record the exact source/image/native identities, test counts, limitations and final runtime status. Do not claim GUI fallback, REST async queue, physical validation or measured token savings.
