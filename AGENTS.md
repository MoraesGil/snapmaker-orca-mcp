# Agent instructions

Read [contribution-workflow.md](contribution-workflow.md) before changing this repository. It is the single source for the contribution, validation and review workflow.

- Use Conventional Commits for every new commit and PR title.
- Keep implementation plans, design drafts and review artifacts out of Git and PRs; use gitignored `.temp/` for temporary work. Version maintained product documentation with the code.
- Always work in an isolated Git worktree named for the feature or fix, with a matching descriptive branch. Use `codex/feat/<slug>` or `codex/fix/<slug>` for Codex branches; keep worktrees beside the primary checkout in `<repo>_worktrees/`.
- Deliver changes through a PR against the agreed base, normally `main`. Keep the worktree while the PR is open. Merge only when the owner authorizes it and required checks pass; then clean up safely.
- Preserve source 3MFs, installed presets, geometry, painting, stable IDs and revision provenance. Use synthetic fixtures and report native-engine coverage separately from contract tests.
- Validate the affected behavior, run the required checks and describe remaining gaps accurately. Never publish credentials, private models, generated G-code or local acceptance logs.
