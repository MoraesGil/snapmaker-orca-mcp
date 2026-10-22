![Give your AI a Snapmaker slicer. 3MF → G-code. No GUI. Conceptual illustration.](docs/assets/hero.png)

# Snapmaker Orca · CLI first

**Give your agent a saved 3MF. Get G-code without opening the slicer GUI.**

A small Bash launcher calls the **Snapmaker Orca already installed on your Mac**.
The official native engine does the slicing. The launcher starts a worker,
returns its output and exits.

**macOS · Bash · your installed Snapmaker Orca · optional MCP / REST · AGPLv3**

This is an independent community project, not an official Snapmaker product.

## Requirements and tested versions

| Component | Requirement / recorded version |
|---|---|
| This MCP package | **0.4.0**; branch improvements are unreleased until published |
| Native slicer | **Snapmaker Orca is required**, with its matching libraries and resources; the engine is not bundled with this Python package |
| Pinned adapter and setting catalog | **Snapmaker Orca 2.4.1**, official source commit `9bf2f18eaff7cc0655cbdeef6a215660085ecb0f` |
| Direct macOS slicing | Installed Snapmaker Orca + this repository's Bash script; Bash and system utilities are supplied by macOS |
| MCP / REST | The same installed slicer + **Python 3.11+**, the project dependencies and MCP client configuration; the commands below use **uv** |

The desktop app contains the native CLI executable. The GUI can remain closed.
On macOS, neither Docker nor a separate Linux engine is required. Do not move
the executable out of its app bundle or substitute another build silently.
The declared source commit and measured binary/resource hashes identify the
test target; a matching version string alone is insufficient. See [the actual
validation record and platform limits](TEST.md).

## Slice once

Install Snapmaker Orca, then:

```sh
git clone https://github.com/MoraesGil/snapmaker-orca-mcp.git
cd snapmaker-orca-mcp

bash bin/snapmaker-slice.sh "/path/project.3mf" 1 "/path/plate1.gcode"
```

The output directory must exist, and the destination file must be new.
Plate numbers start at **1**. The launcher returns one plain `.gcode` file.
The output filesystem must support hard links (for example APFS or HFS+);
for an exFAT/FAT removable drive, slice to a supported volume and copy the result.

The basic path needs **no Python installation, Docker, Linux VM or MCP server**.
It uses `/Applications/Snapmaker Orca.app` by default. Select another installed
bundle explicitly with `ORCA_APP="/path/Snapmaker Orca.app"`.

If the native CLI rejects the file's version after compatibility has been
checked, pass its explicit compatibility flag:

```sh
bash bin/snapmaker-slice.sh "/path/project.3mf" 1 "/path/plate1.gcode" --allow-newer-file
```

This bypasses the engine's version gate. It does not upgrade the file or restore
features unsupported by the selected build. The default remains strict.

## What it does

| Input | Result |
|---|---|
| A complete saved 3MF and one plate number | Plain G-code produced by the selected native engine |
| Native warnings and errors | Original stdout/stderr and native failure exit code |
| Temporary source copy, profile and working files | Private per-run directories, removed on exit |
| Existing output filename | Refused, including a concurrent destination writer |

The source 3MF and installed presets are preserved. Each call starts a new native
process and profile; the launcher keeps no result cache or job database.
There are no added slicing rules, preset overrides, G-code rewrites, analysis
steps or printer commands in this path. Geometry, painting, materials and
settings are interpreted by the native slicer.

Use a saved project with its required settings. Comparing results, checking
print suitability and finding improvements are separate agent tasks.
A predictable launcher does **not** establish byte-identical native toolpaths:
verify actual files before claiming matching SHA1s or movement commands.

## Optional MCP and REST

Use the Bash launcher directly when the agent can run local commands.
Add MCP when clients need tool discovery or a shared endpoint. The server can
also run **natively on macOS**, calling the same installed slicer executable.

The optional Python service is version **0.4.0**. Its configuration schema and
advanced adapter are pinned to official source commit
`9bf2f18eaff7cc0655cbdeef6a215660085ecb0f` (2.4.1). Binding records executable
and resource hashes; updates require an explicit new binding and validation.
The Bash launcher follows the app selected by the operator.

```sh
uv sync --frozen

uv run snapmaker-slicer-mcp --engine /absolute/runtime/engine.json bind-engine \
  --binary "/Applications/Snapmaker Orca.app/Contents/MacOS/Snapmaker_Orca" \
  --resources "/Applications/Snapmaker Orca.app/Contents/Resources" \
  --commit 9bf2f18eaff7cc0655cbdeef6a215660085ecb0f

uv run snapmaker-slicer-mcp --engine /absolute/runtime/engine.json \
  --artifacts /absolute/artifacts serve --no-update-check
```

The last command serves MCP over stdio; configure it as `snapmaker-orca-mcp` in
your client. A shared authenticated HTTP listener also exposes REST under
`/api/v1`. MCP/REST retain artifacts and private editing revisions for later
retrieval; the direct Bash path is ephemeral.

The optional service has additional project editing and output-reading tools.
They are separate from the Bash slicing command. No GUI automation or automatic
engine download/update is included.

Use `load_model` → `set_config` / `edit_project` → `slice_project` to edit a
private 3MF revision and regenerate G-code. Re-slice after every change.
The catalog contains **699 extracted settings**; **91 additional native keys
remain unparsed**. Type validation, saved-3MF persistence and native G-code
effects are separate checks. See [property regressions](docs/property-regressions.md)
for named cases, upgrade checks and the coverage gaps.

- [Native macOS and MCP setup](MACOS.md)
- [MCP tools and usage](docs/usage.md)
- [REST, OpenAPI and downloads](docs/rest.md)
- [Linux / Docker for a Linux host or cloud deployment](LINUX.md)

## Development

```sh
uv sync --frozen --extra test
uv run pytest -q
bash -n bin/snapmaker-slice.sh
```

The service tests use native-boundary fixtures; optional tests can exercise a
separately installed official engine. Local benchmarks and customer models do
not belong in the public repository.

[AGPLv3 license](LICENSE)
