# REST for applications

The authenticated HTTP deployment serves REST at `/api/v1` and MCP at `/mcp` on one listener. Both adapters call the same Python `Slicer`, sharing its native-execution lock, engine binding, artifact store and verified cache. MCP calls do not loop through REST. No second container or native-engine installation is needed for REST clients.

Use the shared HTTP setup in [README.md](../README.md) and [LINUX.md](../LINUX.md). Stdio is an alternative transport for individual MCP clients; it does not expose HTTP routes.

## Authentication and schema

Send `Authorization: Bearer <token>` on all REST calls. The token comes from the operator's private token file. Host/Origin restrictions apply to both REST and MCP. `GET /health` remains a minimal public liveness check under those restrictions.

`GET /api/v1/openapi.json` returns the authenticated OpenAPI 3.1 description. The service is intended for trusted clients sharing server files and artifacts; it is not a multitenant upload service. Prefer server-to-server calls for application integration rather than placing the shared bearer token in browser code.

## Slice a saved project

```http
POST /api/v1/slices
Authorization: Bearer <token>
Content-Type: application/json

{
  "input_3mf": "/input/project.3mf",
  "plate": 0,
  "force": false,
  "allow_newer_file": true
}
```

`input_3mf` is a nonempty server-side path to a complete saved 3MF. In Docker it must use the mounted container path. `plate` is a strict integer: 0 means all applicable plates; 1 and above select one. The flags are strict JSON booleans. Unknown fields are rejected. Installed/canonical profiles and input projects remain read-only.

The response is **200 after native completion or verified cache reuse**. It contains the existing manifest fields (`job_id`, source/engine identity, plate files/hashes, timing, cache state and MCP resource URIs) plus links such as:

```json
{
  "links": {
    "self": "/api/v1/jobs/<job_id>",
    "props": "/api/v1/jobs/<job_id>/props",
    "source_3mf": "/api/v1/jobs/<job_id>/source",
    "plates": {
      "1": {
        "gcode": "/api/v1/jobs/<job_id>/plates/1/gcode",
        "lines": "/api/v1/jobs/<job_id>/plates/1/lines"
      }
    }
  }
}
```

Resolve these relative links against the server base URL and send the same bearer header. Job IDs are32 lowercase hexadecimal characters. Server filesystem paths in the native manifest are provenance; use the HTTP links to retrieve bytes from another application.

This release has **synchronous submission**, not a 202 async queue or progress subscription. Native execution is serialized. Set HTTP client/proxy timeouts for the configured native timeout (default 1800 s) plus possible queue wait and file verification. A disconnect or client timeout does not cancel native work. Before retrying an uncertain call, inspect retained results; `force=true` requests another execution. Cache hits revalidate retained input/output/settings hashes.

## Read results and download artifacts

| Request | Result |
| --- | --- |
| `GET /api/v1/engine` | Local bound engine identity/status; no new upstream query. |
| `GET /api/v1/jobs/{job_id}` | A retained completed manifest and relative artifact links. |
| `GET /api/v1/jobs/{job_id}/props` | Hash-verified native global/per-plate configuration JSON. |
| `GET /api/v1/jobs/{job_id}/source` | Byte-exact preserved source 3MF download. |
| `GET /api/v1/jobs/{job_id}/plates/{plate}/gcode` | Hash-verified G-code attachment for that plate. |
| `GET /api/v1/jobs/{job_id}/plates/{plate}/lines?start=1&count=200` | Bounded JSON lines, 1-based; `count` 1..1000. |

Artifact responses carry a SHA-256 ETag and `Cache-Control: private, no-store`. Missing/changed artifacts or symlinked artifact paths fail explicitly. The service reconstructs canonical artifact locations; stored absolute paths and arbitrary client filenames are not download authority. A job manifest is metadata; its200 response alone does not attest the current integrity of every artifact. Artifact reads verify the requested bytes.

Source download is recovery of a retained input, not reconstruction of a mesh from arbitrary G-code. Global/per-plate property exports do not exhaustively describe every final per-region override. Keep the returned engine/source identities when attaching downloaded output to a print job.

## Errors

| Status | Meaning |
| --- | --- |
| 400 | Malformed JSON, invalid ID/range, unsupported 3MF or external post-processing. |
| 401 / 403 / 421 | Missing/wrong bearer, disallowed supplied Origin, or disallowed Host. |
| 404 | Missing source, completed job or requested plate. |
| 409 | Modified/missing retained artifact, invalid stored manifest, or changed engine assets. |
| 422 | Invalid slice request schema; fields are strict and extra fields are forbidden. |
| 502 / 504 | Native process failure or native execution timeout; diagnostic paths remain in the error. |
| 503 | Service filesystem I/O unavailable. |

The service retains native diagnostics and never silently changes the engine, presets or GUI path. It does not launch computer use. An operator or separate agent can inspect the saved revision in the official slicer; that workflow is outside REST/MCP and its outputs require their own provenance. Failed jobs do not have completed manifests, so completed-job routes return404 for them; diagnostic files remain in the server's artifact directory.

## Farm manager integration

A farm manager needs slicing only when it prepares or changes jobs from 3MF. Scheduling, monitoring and printing already prepared G-code do not require embedding a slicer. Its backend can optionally call this service, download G-code and retain the manifest/hash with its own job record. This repository does not integrate or modify ForjaFarm.
