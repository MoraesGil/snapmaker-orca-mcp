# Contributing

Thanks for helping improve Snapmaker Orca MCP. Clear documentation, small publishable regression fixtures and focused fixes are useful contributions.

## Set up the standalone project

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required:

```sh
git clone https://github.com/MoraesGil/snapmaker-orca-mcp.git
cd snapmaker-orca-mcp
uv sync --frozen --extra test
uv run pytest tests -q
uv build
```

Public engine-independent tests use native-boundary process doubles and the four attributed public preset fixtures. The optional private geometry fixture skips when absent. Real native tests are opt-in; the required all-tests runner uses generated public geometry and an explicitly selected installed app. See [property regressions](docs/property-regressions.md). You do not need the old fork, a native source checkout or a printer to run the regular public suite.

Real-engine acceptance needs your own complete saved 3MF and a deliberately obtained/bound official runtime. Follow [LINUX.md](LINUX.md) and distinguish those results from unit/protocol tests in your report.

## Keep changes focused

- Preserve the existing tool contracts, three resource templates and default stdio behavior unless the proposed change explicitly addresses their contract. This branch exposes 34 tools including `add_modifier`; update discovery assertions deliberately for API additions.
- Keep input projects, installed presets and canonical `_GENERIC` sources read-only. Mutations belong in private project revisions or artifact-local drafts.
- Preserve component payloads, painting, stable IDs, native vector shapes and source/revision provenance. A failed edit batch must not commit a partial revision.
- Keep shared HTTP clients on one authenticated backend. Native slicing should not block unrelated project-state reads.
- Add a regression that fails for the concrete bug and passes with the fix. Run the relevant focused tests, then the public suite.
- Keep native engine updates explicit. A Python wrapper change must not silently replace, relabel or download another motor.
- Retain [third-party credits](THIRD_PARTY.md), license notices and fixture provenance when adapting code or adding test data.

This repository wraps the official native engine. Preview rendering, printer control, physical head matching and full MaxEllis feature parity are outside the current release boundary. Discuss such extensions before coupling them to existing tools.

## Report a reproducible issue

Use the bug-report form and include:

1. Package/source version, engine source commit and platform/architecture.
2. Transport (stdio, Streamable HTTP or direct CLI) and a minimal sequence of calls.
3. Expected behavior, actual result and the relevant redacted error.
4. Whether you observed a fresh slice, verified cache, protocol test or physical result.

Review logs before sharing: remove tokens, credentials, personal paths and customer details. Share a minimal 3MF only when you have permission to publish it; a small synthetic fixture is preferable when it reproduces the issue. A private project is not required to open an issue.

## Submit a pull request

Explain the concrete problem, resulting behavior and validation performed. Include platform/engine identity for native results and any unsupported cases that remain. Keep claims proportional: a passing test, a fresh native G-code and a physical print prove different things.

For a schema change, regenerate from an explicitly supplied official checkout using the command in [THIRD_PARTY.md](THIRD_PARTY.md#regenerate-the-schema-from-an-explicit-official-source-checkout). Review the metadata/source diff; do not hand-edit generated defaults or assume GUI equivalence.

For a native runtime change, record source/build identity, executable/resource hashes, a fresh slice, source/preset preservation and recovery. Runtime bundles, generated G-code and private acceptance logs remain outside Git.

Contributions are distributed under this repository's [GNU AGPLv3 license](LICENSE.txt). Please include the origin and compatible license of any third-party code or fixture you add.
