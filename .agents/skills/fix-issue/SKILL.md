---
name: fix-issue
description: >
  End-to-end workflow for fixing a GitLab issue: fetch issue details, create an
  isolated worktree, plan the fix in plan mode (iterating with the user until
  approved), implement the fix with tests, commit, open a merge request, then
  monitor CI until all jobs pass — fixing failures automatically or flagging
  blockers to the user. Use when the user says "fix issue N", "work on issue N",
  "solve issue N", or invokes /fix-issue.
---

# Fix Issue

## Workflow

### 1. Fetch the issue

```bash
glab issue view <N>
```

Read the title, problem description, and any linked files/lines to understand scope.

### 2. Create a worktree

Infer a concise branch slug from the issue title (e.g. `crucible-backoff` from "no exponential backoff in crucible driver").

Use `EnterWorktree` with name `<developer>/fix/<slug>`. Developer name: `git config user.name`, first word lowercased.

### 3. Enter plan mode

Call `EnterPlanMode`. Propose a thorough plan covering:

- Root cause and files to change
- What the code change looks like (pseudocode or key lines)
- Test strategy: what tests to write, where they live, what they cover

Let the user iterate freely. **Do not write any code until the user explicitly approves the plan.**

### 4. Implement

Exit plan mode and implement exactly what was approved:

- Make the code change
- Write tests (follow CLAUDE.md test-driven guidance)
- Run tests: `cd /mnt/data/shli/sds && uv run python -m pytest <test_file> -v`
- Lint: `uv run ruff check <changed_files>`

Fix any failures before proceeding. Always run from `/mnt/data/shli/sds/` — the worktree venv is not set up.

### 5. Commit

Use the `git-commit` skill.

### 6. Open a merge request

```bash
git push -u origin HEAD
glab mr create \
  --title "<type>: <brief title>" \
  --description "..." \
  --source-branch <branch> \
  --target-branch main
```

MR description should include: Summary bullets, file-level change list, test plan checklist, `Closes #<N>`, and the Claude Code footer.

### 7. Monitor CI in the background

Launch a background agent (run_in_background=true) with this task:

> Monitor GitLab MR !<N> CI. Poll with `glab pipeline list`. On failure: get job
> logs, identify root cause, fix in worktree at `<worktree_path>`, commit and push,
> continue monitoring. On transient infra failure (OOM, exit 137): push an empty
> retry commit. If a failure requires design input or is otherwise not fixable,
> stop and report clearly with the job log excerpt and what was tried. Report back
> when all jobs pass.
> Local validation before any fix push: `uv run python -m pytest <test_file> -v`
> and `uv run ruff check <changed_files>` from `/mnt/data/shli/sds/`.

Report the MR URL to the user immediately after creating it, and note that CI is being monitored in the background.

## Common CI failure patterns

| Symptom | Fix |
|---|---|
| `ruff format` fails | `uv run ruff format <file>` then commit |
| `ruff check` fails | `uv run ruff check --fix <file>` then commit |
| OOM / exit 137 on setup job | Push an empty retry commit |
| Import error in tests | Check module is importable; fix `__init__.py` if needed |
| `tach` graph freshness | `uv run tach sync` then commit |

## Key paths

- Tests: `tests/unit/` and `tests/integration/`
- Format: `scripts/format_code.sh`
- Lint: `scripts/check_errors.sh`
- Python runner: always `uv run python -m pytest ...` from `/mnt/data/shli/sds/`
