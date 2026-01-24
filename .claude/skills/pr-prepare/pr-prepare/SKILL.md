---
name: pr-prepare
description: Generate pull request titles and descriptions by analyzing git diffs and commit history. Use when the user asks to prepare a PR, write PR content, generate PR text, or needs help creating a pull request. Analyzes branch changes compared to main, extracts context from commit messages, and outputs formatted text for the user to copy.
---

# PR Prepare

Generate well-formatted pull request titles and descriptions based on branch changes and commit history.

## Workflow

When this skill is invoked, follow these steps:

### 1. Validate Current Branch

First, check that the user is not on the main branch:

```bash
git branch --show-current
```

If the output is `main`, error immediately with: "Cannot prepare PR from main branch. Please switch to a feature branch first."

### 2. Gather Context

Collect information about the changes:

**Get the diff from main:**
```bash
git diff main...HEAD
```

**Get commit history for this branch:**
```bash
git log main..HEAD --oneline
```

Analyze both outputs to understand:
- What files changed
- What functionality was added, modified, or removed
- The scope and type of changes

### 3. Determine PR Type

Based on the changes, select the appropriate type prefix:

- `feat`: New feature or capability
- `fix`: Bug fix
- `refactor`: Code restructuring without behavior change
- `test`: Adding or updating tests
- `docs`: Documentation changes
- `chore`: Maintenance tasks (dependencies, config, build)
- `perf`: Performance improvements
- `style`: Code style/formatting changes

### 4. Generate PR Title

Format: `<type>: <brief-desc>`

Requirements:
- Use lowercase for the entire title
- Keep brief-desc concise (5-10 words max)
- Focus on what changed, not how or why
- Use imperative mood ("add feature" not "adds feature" or "added feature")

Examples:
- `feat: add user authentication with jwt tokens`
- `fix: resolve memory leak in image processor`
- `refactor: extract validation logic to separate module`

### 5. Generate PR Body

Write a clear description that includes:

**Summary section:**
- 2-3 sentences explaining what the PR does
- Focus on the user-facing or functional impact
- Mention key technical decisions if relevant

**Changes section (optional):**
- Bulleted list of major changes if there are multiple distinct changes
- Only include if the PR touches multiple areas

**Test plan section (optional):**
- How to verify the changes work
- Only include if not obvious or if there are specific testing steps

### 6. Output Format

Present the title and body as plaintext for the user to copy:

```
Title:
<generated-title>

Body:
<generated-body>
```

## Example Usage

**User request:** "Prepare a PR for this branch"

**Your analysis:**
- Diff shows new authentication middleware and JWT handling
- Commits mention "add auth", "integrate jwt", "add tests"
- Multiple files in auth/ directory created

**Your output:**
```
Title:
feat: add jwt authentication middleware

Body:
Implements JWT-based authentication for API endpoints. Adds middleware to validate tokens, extract user claims, and protect routes requiring authentication.

Changes:
- Add JWTAuthMiddleware for token validation
- Integrate with existing user service
- Add authentication tests

Test plan:
- Start the server
- Make requests to /api/protected with and without valid JWT tokens
- Verify 401 responses for invalid/missing tokens
```

## Important Notes

- Do NOT create the actual PR - only generate the text
- Do NOT use markdown in the title (no backticks, bold, etc.)
- Keep the title under 72 characters if possible
- Consult multiple commits to understand the full context, not just the latest one
- If changes span multiple areas, consider if they should be separate PRs instead
