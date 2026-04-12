---
name: review-mr
description: "Review a merge request (MR) or pull request (PR) by analyzing the diff against the main branch. Use when the user asks to review an MR, review a PR, review a branch, review changes, do a code review, or invokes /review-mr. Triggers on requests like 'review this MR', 'review my PR', 'code review branch feature-x', 'review the changes on this branch'. The user is expected to already be on the branch or provide a branch name."
---

# Review MR

## Workflow

### 1. Determine the branch to review

- If the user provides a branch name, use it.
- Otherwise, use the current branch (`git rev-parse --abbrev-ref HEAD`).
- Confirm the branch is not `main` or `master`—warn if so.

### 2. Gather the diff

Run: `git diff main...{branch} --stat` for an overview, then `git diff main...{branch}` for the full diff.

If the diff is very large (>3000 lines), use the Explore agent to read changed files individually rather than loading the entire diff. Focus on files with substantive logic changes first.

Also run `git log main...{branch} --oneline` to understand commit history and intent.

### 3. Understand intent before reviewing

Before writing findings, assess whether the MR's purpose is clear from commit messages, branch name, and code changes. If ambiguous, ask the user clarifying questions such as:

- "What problem does this MR solve?"
- "Is [specific change] intentional or an artifact?"
- "This changes [X behavior]—is that the goal?"

Do not guess intent when the code is ambiguous. Ask.

### 4. Review the diff

Analyze changes against the checklist below. Organize findings by severity:

**Blocking** — Must fix before merge:
- Bugs, logic errors, incorrect behavior
- Security vulnerabilities (injection, auth bypass, secrets in code, OWASP top 10)
- Data loss risks
- Breaking changes to public APIs without migration path
- Race conditions, deadlocks
- Resource leaks (unclosed handles, connections, missing cleanup)

**Should fix** — Strong recommendations:
- Missing error handling for failure-prone operations (network, I/O, parsing)
- Missing or inadequate tests for new/changed behavior
- Performance issues (N+1 queries, unnecessary allocations in hot paths, quadratic loops)
- Poor naming that obscures intent
- Code duplication that should be extracted
- Missing input validation at system boundaries
- Inconsistency with surrounding codebase patterns/conventions

**Nit** — Minor, non-blocking:
- Style inconsistencies
- Minor naming suggestions
- Comment improvements
- Import ordering

### 5. Review checklist

Apply these checks to every MR:

- **Correctness**: Does the code do what it claims? Edge cases handled?
- **Security**: Any user input unsanitized? Secrets exposed? Auth checks missing?
- **Error handling**: Are errors caught, propagated, and reported appropriately?
- **Tests**: Are new behaviors tested? Are edge cases covered? Do existing tests need updating?
- **Performance**: Any obvious bottlenecks? Unnecessary work in loops? Memory concerns?
- **Concurrency**: Thread safety? Shared mutable state? Proper locking?
- **API design**: Are interfaces clear, minimal, and consistent? Breaking changes documented?
- **Resource management**: Files/connections closed? Cleanup in finally/defer/context managers?
- **Naming & clarity**: Can you understand the code without the diff context?
- **Scope**: Does the MR do one thing well, or is it mixing concerns?
- **Rollback safety**: Can this be reverted without data migration issues?
- **Dependencies**: New dependencies justified? Version pinned? License compatible?

### 6. Present findings

Format the review as:

```
## MR Review: `{branch_name}`

**Summary**: 1-2 sentence summary of what this MR does and overall assessment.

**Scope**: {N} files changed, {additions}+/{deletions}-

### Blocking
- **[file:line]** Description of issue and suggested fix.

### Should Fix
- **[file:line]** Description and rationale.

### Nits
- **[file:line]** Minor suggestion.

### Questions
- Clarification questions about design decisions or intent.

### What looks good
- Brief callout of well-done aspects (good test coverage, clean abstractions, etc.)
```

Omit empty sections. Always include the "What looks good" section—acknowledge quality work.

## Guidelines

- Be specific: reference file paths and line numbers. Never give vague feedback.
- Be constructive: suggest fixes, not just problems.
- Respect scope: review what changed, not the entire codebase. Only flag pre-existing issues if the MR makes them materially worse.
- Read surrounding context: understand the file and module before commenting on the diff.
- Use the project's CLAUDE.md and conventions to calibrate style expectations.
- When unsure about project-specific conventions, check existing code for patterns before flagging style issues.
