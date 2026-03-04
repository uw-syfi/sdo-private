---
name: review-codebase
description: >
  Review the SDS codebase for code quality, architecture, and convention adherence.
  Use when the user asks to review the codebase, audit code quality, check for issues,
  or invokes /review-codebase. Triggers on requests like "review the codebase",
  "audit code quality", "check code for issues", "what needs improving",
  "find code smells", "review app_operator", "review lego_agent".
  This is for reviewing the overall codebase — not for reviewing a specific MR/PR diff
  (use review-mr for that).
---

# SDS Codebase Review

Review the SDS codebase, produce numbered issue proposals, and optionally file them as GitLab issues.

## Scope

Review these directories (ignore `apps/`):
- `app_operator/` — core operator logic
- `lego_agent/` — autonomous script generation
- `libs/agent_cli/` — shared AI provider integrations
- `tests/` — test suite

If the user specifies a narrower scope (e.g., "review lego_agent") or a specific type of issue
(e.g., "find security issues", "check test coverage"), limit analysis accordingly.

## Review Process

1. Read `references/review-checklist.md` for the full checklist of what to examine
2. Explore the target directories using Glob and Grep to find relevant files
3. Read key files to understand patterns and identify issues
4. Run `bash scripts/check_errors.sh` to check for linting issues
5. Produce the review report as numbered issue proposals (see Output Format)

## Output Format

Present findings as numbered issue proposals. Each issue must be self-contained — its body
should contain everything needed to resolve it without additional context.

```
# Code Review: [Scope]

## Summary
[1-2 sentence overview of codebase health]

## Proposed Issues

### #1: [Short descriptive title]
- **Severity**: critical | high | medium | low
- **Labels**: bug, security, code-quality, architecture, testing, config, error-handling, docs
- **Location(s)**: `file_path:line_number` (list all relevant locations)
- **Problem**: [Clear description of what is wrong or suboptimal]
- **Proposed fix**: [Concrete steps to resolve, with code snippets if helpful]
- **Acceptance criteria**: [How to verify the fix is correct]

### #2: [Short descriptive title]
...

## Statistics
- Files reviewed: N
- Issues proposed: N (critical: N, high: N, medium: N, low: N)
```

## Issue Quality Guidelines

Each proposed issue must be:
- **Well-scoped**: One logical fix per issue. Do not combine unrelated problems.
- **Self-contained**: The issue body must include all context needed to resolve it —
  file paths, line numbers, code snippets, and expected behavior.
- **Actionable**: Include concrete fix steps, not vague suggestions.
- **Verifiable**: Include acceptance criteria or a way to confirm the fix works.

## Severity Levels

- **critical** — security vulnerability, data loss risk, or correctness bug
- **high** — significant maintainability or reliability issue
- **medium** — code smell, convention violation, or moderate risk
- **low** — minor improvement opportunity

## Filing Issues on GitLab

Do NOT file issues until the user explicitly asks. When they do, they may say things like:
- "file all of them"
- "file #1, #3, #5"
- "file the critical ones"

To file an issue, use `glab`:

```bash
glab issue create --title "<title>" --description "<body>" --label "<label1>,<label2>"
```

Format the issue body in Markdown with these sections:
- **Problem** — what is wrong, with file paths and line numbers
- **Proposed Fix** — concrete steps to resolve
- **Acceptance Criteria** — how to verify

Wrap the description in a heredoc for proper formatting:

```bash
glab issue create --title "Fix X in Y" --label "bug,code-quality" --description "$(cat <<'EOF'
## Problem

[description with `file_path:line_number` references]

## Proposed Fix

[concrete steps, code snippets if needed]

## Acceptance Criteria

- [ ] [verification step]
EOF
)"
```

## Follow-up

When a user references an issue number (e.g., "fix #3", "tell me more about #5"):
1. Retrieve the issue details from the review
2. Implement the fix or provide detailed guidance
3. Run validation (`bash scripts/check_errors.sh` and relevant tests)
