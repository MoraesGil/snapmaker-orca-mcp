# Linux container: official native engine + MCP stdio

This recipe packages an already compiled official engine alongside the Python MCP layer. It does not start the slicer's GUI, a display server, Xvfb or VNC. GTK/WebKit/OpenGL shared libraries are runtime dependencies of the official binary even on its native CLI path.

Use a standard Ubuntu 24.04 AppImage from a recorded **Snapmaker/OrcaSlicer main** commit. Keep the complete extracted package. The source commit is an operator declaration; verify the official build's metadata and artifact digest before binding. Neither the Dockerfile nor the MCP downloads or updates the engine.

## Prepare the engine and MCP wheel

Run from the repository root. This validated example is the official main `9bf2f18eaff7cc0655cbdeef6a215660085ecb0f`, [build run 38288173465](https://github.com/Snapmaker/OrcaSlicer/actions/runs/38288173465), artifact `11766257337`:

```sh
mkdir -p tools/slicer_mcp/runtime
gh api repos/Snapmaker/OrcaSlicer/actions/artifacts/11766257337/zip \
  > tools/slicer_mcp/runtime/engine.zip
```

Verify ZIP SHA-256 against the official metadata: `53d6aba539ca5363b246fe2a6500cd03c6a13898316711235b29e45af60f9dab`. Then extract the ZIP into `tools/slicer_mcp/runtime`. It contains `Snapmaker_Orca_Linux_AppImage_Ubuntu2404_V2.4.1.AppImage` (SHA-256 `bc03ec0d3add911b3e90afa1a9373f2c53e4d11692b58bfc71031657f42e8617`). Artifact availability depends on GitHub retention and download access; if it has expired, supply a matching main build instead.

Extract the AppImage once. It contains an amd64 executable; on macOS ARM64 the following command runs it under emulation:

```sh
chmod +x tools/slicer_mcp/runtime/*.AppImage
docker run --rm --platform linux/amd64 --network none \
  --mount "type=bind,source=$PWD/tools/slicer_mcp/runtime,target=/work" \
  --workdir /work ubuntu:24.04 \
  ./Snapmaker_Orca_Linux_AppImage_Ubuntu2404_V2.4.1.AppImage --appimage-extract

uv build --project tools/slicer_mcp
```

The resulting layout must include `runtime/squashfs-root/bin/snapmaker-orca` and `runtime/squashfs-root/resources`. Engine files and wheel output are ignored by Git.

## Build and run

```sh
docker build --platform linux/amd64 \
  --build-arg ENGINE_COMMIT=9bf2f18eaff7cc0655cbdeef6a215660085ecb0f \
  --build-arg ENGINE_EXECUTABLE_SHA256=b9f6c2a9d59e0fb43761c7ce3c5c5c908f68f68991e27bd314d40a88ebb66cf9 \
  --tag snapmaker-slicer-mcp:0.2.0-main-9bf2f18 tools/slicer_mcp

docker run --rm -i --platform linux/amd64 \
  --mount type=bind,source=/absolute/3mf-inputs,target=/input,readonly \
  --mount type=bind,source=/absolute/slicer-artifacts,target=/artifacts \
  snapmaker-slicer-mcp:0.2.0-main-9bf2f18
```

Create the input/output directories first. The output bind mount preserves the generated source copies, G-codes, settings and cache after the stdio server exits. Do not add `-t`: MCP uses stdin/stdout directly. There is no HTTP port to expose. Pin the validated image ID in a persistent client launcher, and retain the previous versioned image for rollback. A tag alone does not make a running client adopt an upgrade.

Configure an MCP client with `command: "docker"` and the arguments from the `docker run` command. Then call `slice_3mf` with `input_3mf: "/input/project.3mf"` and the desired plate number. Paths returned by the MCP are container paths; `/artifacts/...` corresponds to the mounted host output directory, and MCP resources read the same files.

To expose existing presets for inspection and explicit working-copy selection, add a read-only mount and the `--presets` argument:

```sh
docker run --rm -i --platform linux/amd64 --network none \
  --mount type=bind,source=/absolute/3mf-inputs,target=/input,readonly \
  --mount type=bind,source=/absolute/canonical-presets,target=/presets,readonly \
  --mount type=bind,source=/absolute/slicer-artifacts,target=/artifacts \
  snapmaker-slicer-mcp:0.2.0-main-9bf2f18 \
  --engine /opt/slicer-mcp/engine.json --artifacts /artifacts \
  --presets /presets serve --no-update-check
```

`load_model` and editing tools produce private revisions under `/artifacts/projects`; preset save/edit/rename/delete only affect `/artifacts/preset-drafts`. The mounted originals remain read-only. Rebuild the wheel and image to install Python editing changes; the native engine's recorded identity stays the same.

To run offline, add `--network none` before the image name and override its command with:

```text
--engine /opt/slicer-mcp/engine.json --artifacts /artifacts serve --no-update-check
```

Offline mode leaves upstream status `not_checked`; it does not imply no update exists. Normal startup checks upstream once and only reports notices. To update deliberately, supply the newer official compiled package, change the two binding arguments, build a new image and run it. Rebuilding the Python layer alone does not update C++ slicing behavior.

The bound main `9bf2f18` fixes tree/manual support leaking into unpainted overhangs. Auto support is unchanged by that commit. The retained native macOS `441f73c` build is a separate older binding; newer macOS acceptance is not part of this record. Acceptance evidence and platform limits are in [TEST.md](TEST.md).
