# Linux and Docker deployment

Run the official Snapmaker Orca native CLI behind Snapmaker Orca MCP, without starting a desktop GUI, display server, Xvfb or VNC. This repository builds the Python service image from a separately supplied native bundle.

The official executable still links GTK/WebKit/OpenGL libraries. The Dockerfile includes its runtime dependencies; those libraries do not mean a desktop session is running.

## 1. Obtain the official engine

Use the complete Ubuntu 24.04 **amd64** package from official `Snapmaker/OrcaSlicer` main, preserving its matching libraries/resources. The validated source commit is:

```text
9bf2f18eaff7cc0655cbdeef6a215660085ecb0f
```

The recorded official package came from [build run 38288173465](https://github.com/Snapmaker/OrcaSlicer/actions/runs/38288173465), artifact `11766257337`. GitHub retention/download access may make that artifact unavailable; do not assume a permanent download. Obtaining or building the official engine is separate from installing this MCP.

| Recorded artifact | SHA-256 |
| --- | --- |
| Artifact ZIP | `53d6aba539ca5363b246fe2a6500cd03c6a13898316711235b29e45af60f9dab` |
| `Snapmaker_Orca_Linux_AppImage_Ubuntu2404_V2.4.1.AppImage` | `bc03ec0d3add911b3e90afa1a9373f2c53e4d11692b58bfc71031657f42e8617` |
| Extracted `bin/snapmaker-orca` | `b9f6c2a9d59e0fb43761c7ce3c5c5c908f68f68991e27bd314d40a88ebb66cf9` |

Verify your download against the official build metadata. Binding hashes the local executable/resources; its commit is an operator declaration, not a source identity inferred from executable bytes.

From this standalone repository root, place the AppImage under ignored `runtime/` and extract it:

```sh
mkdir -p runtime
chmod +x runtime/Snapmaker_Orca_Linux_AppImage_Ubuntu2404_V2.4.1.AppImage
docker run --rm --platform linux/amd64 --network none \
  --mount "type=bind,source=$PWD/runtime,target=/work" --workdir /work \
  ubuntu:24.04 \
  ./Snapmaker_Orca_Linux_AppImage_Ubuntu2404_V2.4.1.AppImage --appimage-extract
```

The Ubuntu extraction image must already be available locally when using `--network none`. On an ARM64 host the amd64 command requires container emulation. The tested layout is:

```text
runtime/squashfs-root/bin/snapmaker-orca
runtime/squashfs-root/resources/
runtime/squashfs-root/lib/
```

Keep the whole extracted tree. Custom Linux FHS resource layouts are not supported by the binder.

## 2. Build the local image

Python 3.11+, [uv](https://docs.astral.sh/uv/) and Docker are required. The engine and wheel are local build inputs and are ignored by Git.

```sh
uv sync --frozen
uv build
docker build --platform linux/amd64 \
  --build-arg ENGINE_COMMIT=9bf2f18eaff7cc0655cbdeef6a215660085ecb0f \
  --build-arg ENGINE_EXECUTABLE_SHA256=b9f6c2a9d59e0fb43761c7ce3c5c5c908f68f68991e27bd314d40a88ebb66cf9 \
  --tag snapmaker-orca-mcp:0.3.0-main-9bf2f18 .
```

This tag names **your locally built image**. It is not a claim that an image has been published to a container registry. The recorded digest applies to the exact bundle above; do not reuse it to label another binary.

The Dockerfile verifies the executable digest and binds `/opt/slicer-mcp/engine.json`. Rebuilding only the Python wheel does not upgrade the C++ engine.

## 3. Choose the transport

Create the host model, preset and artifact directories first. Use separate directories: models and presets are mounted **read-only**; artifacts are writable and retain revisions, drafts, output and cache.

| Container path | Access | Contents |
| --- | --- | --- |
| `/input` | Read-only | Saved source 3MFs. |
| `/presets` | Read-only | Optional installed/canonical preset roots. |
| `/artifacts` | Read/write | Private working revisions, preset drafts and slice jobs. |
| `/run/secrets/mcp-token` | Read-only | HTTP bearer token, supplied by the operator. |

### Shared HTTP: several agents, one service

Generate a token in your private secrets directory, outside tracked files:

```sh
mkdir -p /absolute/secrets
umask 077
openssl rand -hex 32 > /absolute/secrets/mcp-token
```

Start one backend. This example publishes its HTTP port only on the host's loopback interface:

```sh
docker run --detach --name snapmaker-orca-mcp --restart unless-stopped \
  --platform linux/amd64 --cpus 4 --memory 8g \
  --publish 127.0.0.1:8080:8080 \
  --mount type=bind,source=/absolute/models,target=/input,readonly \
  --mount type=bind,source=/absolute/presets,target=/presets,readonly \
  --mount type=bind,source=/absolute/artifacts,target=/artifacts \
  --mount type=bind,source=/absolute/secrets/mcp-token,target=/run/secrets/mcp-token,readonly \
  snapmaker-orca-mcp:0.3.0-main-9bf2f18 \
  --engine /opt/slicer-mcp/engine.json --artifacts /artifacts --presets /presets \
  serve --transport streamable-http --host 0.0.0.0 --port 8080 \
  --auth-token-file /run/secrets/mcp-token \
  --allowed-host localhost:8080 --allowed-host 127.0.0.1:8080 \
  --allowed-origin http://localhost:8080 --no-update-check
```

Connect clients to `http://localhost:8080/mcp` with `Authorization: Bearer <your-token>`, using each client's secret storage. Connect/reconnect to this URL without starting another container. `/health` is a minimal liveness endpoint; native slice readiness still requires an actual request.

For a private reverse proxy, repeat `--allowed-host` and `--allowed-origin` with the actual public-facing host and browser origin. These are separate from bearer authentication. A shared service exposes one backend and artifact store to its authenticated clients; it does not provide per-user filesystem isolation.

The public Compose configuration uses the same mount contract via `SNAPMAKER_MCP_MODELS_DIR`, `SNAPMAKER_MCP_PRESETS_DIR`, `SNAPMAKER_MCP_ARTIFACTS_DIR` and `SNAPMAKER_MCP_TOKEN_FILE`. Set `SNAPMAKER_MCP_IMAGE` to your local image tag. Compose does not publish a host port by default; choose an explicit local/private access route for your deployment.

### Stdio: an individual MCP client

```sh
docker run --rm -i --platform linux/amd64 --network none \
  --mount type=bind,source=/absolute/models,target=/input,readonly \
  --mount type=bind,source=/absolute/presets,target=/presets,readonly \
  --mount type=bind,source=/absolute/artifacts,target=/artifacts \
  snapmaker-orca-mcp:0.3.0-main-9bf2f18 \
  --engine /opt/slicer-mcp/engine.json --artifacts /artifacts --presets /presets \
  serve --no-update-check
```

Stdio is the default. Configure your MCP client with `command: "docker"` and these `run` arguments. Omit a TTY: MCP uses stdin/stdout directly. This individual launcher creates a process/container for that client; use shared HTTP when several agents should share one backend.

## 4. Use server-side paths

Call `load_model(path="/input/project.3mf")` or `slice_3mf(input_3mf="/input/project.3mf", plate=0, force=true)`. Container output paths such as `/artifacts/runs/...` map to your host artifact directory. MCP resources read those same outputs without requiring host-path translation.

Working 3MFs live under `/artifacts/projects`, draft presets under `/artifacts/preset-drafts` and slice jobs under `/artifacts/runs`. Artifacts persist until the operator removes them; deleting retained sources also removes their recovery value.

## Offline operation and updates

`serve --no-update-check` disables the startup upstream check. Fully offline stdio can also use `--network none`. HTTP needs a network path between clients and server, so restrict its deployment network rather than removing that path.

Offline or unavailable update checks are reported as such. They do not establish that the engine is current. `engine_status` reports a newer main commit as a notice; it never downloads, compiles or switches the engine.

To adopt another native build, obtain it deliberately, verify its source/artifact identity, bind it and validate new slices. Adaptive generation and literal-default backfill require their compatible source/schema binding. Keep the previous validated image for rollback.

See [TEST.md](TEST.md) for platform and evidence limits and [usage examples](docs/usage.md) for editing, resources and recovery.
