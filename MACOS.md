# Native macOS CLI and optional MCP

Snapmaker Orca's macOS executable supports headless slicing. The GUI and CLI can
use the same app bundle and slicing code. A Linux VM, Docker, Xvfb and GPU/MLX
runtime are not required to execute the macOS CLI.

Use an official build whose exact source commit is known. The adapter and schema
in this release are pinned to `9bf2f18eaff7cc0655cbdeef6a215660085ecb0f` (2.4.1).
Do not label a different installed build with that commit or update it silently.
Native libraries and `Contents/Resources` must remain beside the executable.

## Slice once and exit

Install Snapmaker Orca, then run the Bash launcher:

```sh
bash bin/snapmaker-slice.sh /absolute/project.3mf 1 /absolute/plate1.gcode
```

No Python, server or VM is required. The destination must be new and its directory
must exist. `ORCA_APP` explicitly selects another installed app. Native errors
and warnings pass through; private working files are removed on exit. The saved
source and installed presets are preserved. The optional fourth argument
`--allow-newer-file` explicitly bypasses the native file-version gate; check
compatibility before using it.

Publication uses an exclusive hard link on the output filesystem. Use APFS/HFS+
or another filesystem with hard-link support; slice there before copying to an
exFAT/FAT drive. This preserves atomic publication without overwriting a file or
moving the result inside a directory created concurrently at the destination.

The Bash launcher does not retain an artifact store or analysis reports. See the
README for the complete invocation contract and determinism limits.

## Optional Python adapter

For MCP, REST or retained artifacts, install Python 3.11+ and uv and bind the
matching official macOS build:

```sh
git clone https://github.com/MoraesGil/snapmaker-orca-mcp.git
cd snapmaker-orca-mcp
uv sync --frozen

uv run snapmaker-slicer-mcp --engine /absolute/runtime/engine-macos.json bind-engine \
  --binary "/Applications/Snapmaker Orca.app/Contents/MacOS/Snapmaker_Orca" \
  --resources "/Applications/Snapmaker Orca.app/Contents/Resources" \
  --commit 9bf2f18eaff7cc0655cbdeef6a215660085ecb0f

uv run snapmaker-slicer-mcp \
  --engine /absolute/runtime/engine-macos.json \
  --artifacts /absolute/artifacts \
  slice /absolute/models/project.3mf --plate 1 --force
```

This starts a native slicer process, waits for its G-code/error result and exits.
`--force` bypasses the verified result cache. Each invocation uses private native
profile and temporary directories; worker temporary files are removed when it
finishes. Output, source snapshots and diagnostics remain in the artifact store
for download and provenance. Deleting retained artifacts is separate from worker
cleanup.

Use a complete saved 3MF. The CLI reads its geometry, material/process settings,
painting and object overrides; it does not open the project in the desktop UI or
change installed presets. Explicit version checking is available with
`slice --strict-file-version`.

## Optional MCP

For one agent, configure the existing `serve --transport stdio` entry shown in
the README, using the macOS engine manifest. For several agents or REST clients,
run one native HTTP server:

```sh
.venv/bin/snapmaker-slicer-mcp \
  --engine /absolute/runtime/engine-macos.json \
  --artifacts /absolute/artifacts \
  serve --transport streamable-http \
  --host 127.0.0.1 --port 49383 \
  --auth-token-file /absolute/secrets/mcp-token \
  --allowed-host 127.0.0.1:49383 \
  --allowed-origin http://127.0.0.1:49383 \
  --no-update-check
```

Register it as `snapmaker-orca-mcp`, URL `http://127.0.0.1:49383/mcp`, and preserve
the client's private Bearer header. REST uses the same listener at `/api/v1`.
The server stays available; each slice starts and ends its own native worker.
`engine_status` exposes the bound executable/resource hashes. It never installs
or switches the engine.

For a persistent local installation, use a user LaunchAgent with the venv's
absolute executable path and these arguments. Keep its manifest, token and logs
outside Git. Do not depend on an interactive shell's current directory or uv
resolving dependencies each time launchd starts the service. A loopback listener
is local to the Mac; remote access needs its own explicitly configured private
transport.

## Moving from Docker

Create a new macOS engine manifest; do not reuse or edit Linux executable hashes.
Drain and stop the old slicing backend before sharing its artifact store with a
native server. Preserve artifacts and private client tokens, then change the MCP
client URL and reload that connection.

Use host paths such as `/Users/name/models/project.3mf` or `/Volumes/drive/...`.
Container mount aliases such as `/input/files`, `/input/user` and `/artifacts`
are not native macOS filesystem paths. Retained editing-state files that contain
absolute container paths need a hash-checked migration before reopening those
projects. The job download reader already reconstructs paths from job IDs.

Independent GUI and CLI runs can differ in headers, images and toolpaths. Engine
identity and saved 3MF settings alone do not prove byte identity or exhaustive
effective per-object/per-region configuration equality. Do not normalize or
rewrite returned G-code to conceal a mismatch.
