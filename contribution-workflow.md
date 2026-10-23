# Contribution workflow

This is the contribution guide for Snapmaker Orca MCP: isolated changes, Conventional Commits, reproducible validation and review through a pull request. [AGENTS.md](AGENTS.md) applies this workflow to agents.

## 1. Create a named worktree

Start from the agreed base, normally `origin/main`. Keep the primary checkout available; do feature and fix work in sibling worktrees under `<repo>_worktrees/`.

```sh
git fetch origin
project_root="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
worktrees_dir="${project_root}_worktrees"
mkdir -p "$worktrees_dir"
git worktree add -b codex/feat/box-modifier \
  "$worktrees_dir/feat-box-modifier" origin/main
cd "$worktrees_dir/feat-box-modifier"
uv sync --frozen --extra test
```

Replace the example with the actual feature or fix name. Use `codex/fix/<slug>` and `fix-<slug>` for fixes; documentation and test work also need a descriptive worktree. Each worktree owns its Python environment. Inspect existing changes before editing and never reset another contributor's work.

For an agreed integration branch, branch each independent property or function from that parent and target its PR there. Name children after the real property or function and keep their fixtures separate. Follow the agreed batch plan, then run the complete suite on the parent. A passing child test does not replace parent integration; see the [property regression workflow](docs/property-regressions.md).

## 2. Make focused changes and commits

Use the Conventional Commits form for every new commit and PR title:

```text
<type>(<optional-scope>): <subject>

<optional body explaining why>

<optional footer>
```

Types include `feat`, `fix`, `test`, `docs`, `refactor`, `perf`, `build`, `ci` and `chore`. Write an imperative subject starting lowercase, under 50 characters, without a final period. Keep commits coherent; a behavior change and its regression test belong together. Use English consistently in new repository documentation, commit messages and PRs.

Examples: `feat(modifier): add regional speed overrides`, `fix(slice): reject stale revision hashes`, `docs(workflow): explain worktree cleanup`. Explain non-obvious reasons in the body, wrap it around 72 characters and reference only real issues. Mark breaking changes with `!` and a `BREAKING CHANGE:` footer describing migration.

Keep implementation plans, design drafts, review copies and agent working notes out of version control. Use gitignored `.temp/` for temporary planning, output comparisons and validation reports; do not open PRs solely to publish planning artifacts. Maintain product documentation such as setup, usage, API contracts and testing instructions with the code. Remove accidentally tracked planning files with a normal commit through the contribution workflow; routine cleanup does not require rewriting Git history.

Preserve these project contracts:

- Keep source 3MFs, installed presets and canonical `_GENERIC` sources read-only. Edits belong in private revisions or artifact-local drafts; failed batches must be atomic.
- Preserve component payloads, painting, IDs, transforms, native array shapes and source/revision hashes. Reject stale edits and invalidate outdated slicing output.
- Keep existing tool/resource contracts, default stdio behavior and authenticated HTTP boundaries. Update discovery tests deliberately when adding an API.
- Keep native-engine selection and upgrades explicit. Do not silently download or replace the engine. Do not introduce printer control or GUI automation as part of an unrelated change.
- Retain [third-party provenance and license notices](THIRD_PARTY.md). For schema changes, follow its regeneration procedure using an explicitly selected official source checkout.

## 3. Validate the changed behavior

For a new behavior or bug fix, reproduce the failure with a focused regression before implementing it. Test observable results and edge cases; use doubles at the native-process/transport boundary. Then run the public suite and release checks from the worktree:

```sh
uv run --frozen --extra test pytest tests -q
bash -n bin/snapmaker-slice.sh
uv build
uv run --frozen python scripts/check_distribution.py
git diff --check
```

Python 3.11+ is required for the service. The regular suite can run without a slicer or printer; it does not prove native G-code semantics. CI checks Linux and macOS across Python 3.11–3.13. For documentation-only changes, verify links and commands and explain any checks omitted.

For slicing/property/modifier behavior, run the required native integration with an explicitly selected official Snapmaker Orca installation and its matching resources. On macOS:

```sh
uv run --frozen --extra test python scripts/run_native_regressions.py \
  --all-tests --app "/Applications/Snapmaker Orca.app" \
  --source-commit <verified-official-engine-commit> \
  --report .temp/native-regressions.json
```

Replace the placeholder with the engine identity you verified; it is not this wrapper's Git SHA. Clear inherited `PYTEST_ADDOPTS` before a complete run. Keep the paired JSON/JUnit reports locally and record the wrapper SHA/dirty state, engine version/source identity/hashes, counts, skips and remaining gaps. Do not reuse reports after a code or engine change. See [property regressions](docs/property-regressions.md) and [validation records](TEST.md); Linux runtime setup is in [LINUX.md](LINUX.md).

Distinguish contract validation, saved-3MF persistence and observed native toolpaths. State which properties, values and geometries were exercised; catalog coverage does not imply native coverage. A digital slice does not certify GUI equivalence or physical printing.

Use synthetic, publishable geometry wherever possible. Keep runtime bundles, generated G-code, private models and local acceptance logs outside Git. Review logs before sharing; remove tokens, credentials, personal paths and customer details. A reproducible issue should identify versions/platform, transport, minimal calls, expected/actual behavior and the type of evidence.

## 4. Open a reviewable PR

Read the full diff against the intended base and the commit history before writing the description. Use a Conventional Commits title. Lead with the concrete problem and resulting behavior in one to three sentences; avoid opening with “This PR”. Describe the final implementation without tool/model attribution or unrelated project references.

Use this lean structure for CLI/MCP changes, adapting the content to the actual diff:

```markdown
## Overview
Concrete problem, resulting behavior and why it matters.

## Changes Made

- One actual change per bullet.

| Before | After |
| --- | --- |
| Previous observable behavior | New observable behavior |

## Testing

1. Prepare a small synthetic project with the required printer/material.
2. Exercise the affected edit or slicing operation.
3. Inspect the saved project/output and verify the relevant rejection case.

## Validation
Actual commands, results, platform/engine identity and coverage limits.

## Checklist

Author:

- [ ] Affected behavior and required checks verified; gaps stated.
- [ ] Source/preset preservation and revision handling checked where affected.
- [ ] Documentation updated; published content reviewed for private data.

Reviewer:

- [ ] Behavior, diff and evidence agree; required CI checks pass.
- [ ] Blocking findings resolved and final changes reviewed.

Author: @your-github-handle
```

Replace the template prose with evidence and use the actual author's public GitHub handle once in the footer. `Testing` contains numbered human steps, suitable for repeating the product workflow; put shell commands, runner output and technical provenance in `Validation`. Mark only checks actually completed and leave reviewer checks for the reviewer. Remove unused sections; add outstanding questions or limitations only when real. Link an issue only when one exists.

For visible UI changes, add real Before | After captures under `Evidence` and explain the observed states; CLI/MCP changes can use the behavior table and test results. Do not fabricate screenshots, approvals, results or placeholder links. Use a draft PR while required work remains.

## 5. Review, merge and clean up

Review the complete diff and evidence independently. Prioritize correctness, source preservation, authentication, failure handling and coverage; distinguish blocking defects from optional follow-ups. Re-run affected checks after fixes and review the final revision. The author checklist is not reviewer approval.

Opening a PR does not authorize merging it. Merge into the agreed base only after owner authorization, required CI checks and applicable review approval. Prefer a squash merge with the Conventional Commits PR title; do not rewrite shared history to tidy older commits.

Keep the worktree while its PR is open. After the approved merge, verify the remote PR is merged and inspect the worktree for changes, untracked files and needed ignored evidence. Preserve needed evidence outside the worktree before removing it. From the primary checkout:

```sh
git fetch origin
git worktree list
git -C "$worktrees_dir/feat-box-modifier" status --short --untracked-files=all
git worktree remove "$worktrees_dir/feat-box-modifier"
git branch -d codex/feat/box-modifier
```

Resolve `project_root` and `worktrees_dir` as in step 1 in a new shell. Do not force removal of a dirty worktree. After a squash merge, local branch deletion may refuse because ancestry differs; verify the merged PR and preserve any remaining work before deciding how to remove the branch.

Contributions use this repository's [GNU AGPLv3 license](LICENSE.txt). Include the origin and compatible license of third-party code or fixtures.
