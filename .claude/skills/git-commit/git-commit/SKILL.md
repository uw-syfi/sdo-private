---
name: git-commit
description: "Create well-formatted git commits with conventional commit messages. Use when the user asks to commit changes, create a commit, or save their work to git. Automatically analyzes uncommitted changes, generates structured commit messages with type and brief description format (e.g., 'feat: add login') and bullet-point descriptions, intelligently stages relevant files (excluding sensitive files), and commits the changes."
---

# Git Commit

## Overview

Create conventional commits with well-structured messages by analyzing uncommitted changes, understanding context from the current branch, and following git commit hygiene best practices.

## Workflow

Follow these steps to create a commit:

### 1. Gather Context

Run these commands in parallel to understand the current state:

```bash
# See all untracked and modified files (never use -uall flag)
git status

# See staged changes
git diff --cached

# See unstaged changes
git diff

# Get recent commit messages to understand style
git log -5 --oneline

# Get current branch name for context
git branch --show-current
```

### 2. Analyze Changes

Examine the diff output to understand:
- **Nature of changes**: New feature, bug fix, refactor, documentation, tests, etc.
- **Scope**: Which components/modules are affected
- **Intent**: Use branch name as hint (e.g., `feat/add-login` suggests a feature), but prioritize actual changes over branch name

### 3. Determine Files to Stage

Stage relevant files while excluding:
- Sensitive files: `.env`, `credentials.json`, `*.key`, `*.pem`, secrets
- Build artifacts: `dist/`, `build/`, `*.pyc`, `node_modules/`
- Large binaries unless explicitly requested
- Submodule changes (unless user explicitly requests)

If files are already staged, include them in the commit.

### 4. Generate Commit Message

Format: First line + optional detailed description

**First line** (required):
```
<type>: <brief-desc>
```

Types (choose most appropriate):
- `feat`: New feature or capability
- `fix`: Bug fix
- `refactor`: Code restructuring without behavior change
- `docs`: Documentation changes
- `test`: Adding or updating tests
- `chore`: Maintenance, dependencies, configuration
- `style`: Formatting, whitespace, cosmetic changes
- `perf`: Performance improvements

Keep first line under 72 characters, imperative mood (e.g., "add" not "added" or "adds").

**Detailed description** (for non-trivial changes):
- Leave blank line after first line
- Use bullet points to describe what changed
- Focus on what and why, not how
- Skip for simple changes that are self-explanatory from first line

Example for trivial change:
```
fix: correct typo in README
```

Example for non-trivial change:
```
feat: add user authentication system

- Implement JWT-based authentication middleware
- Add login/logout endpoints with session management
- Create user model with password hashing
- Add auth guards for protected routes
```

### 5. Create Commit

Write commit message to temporary file, then use the script:

```bash
# Create message file
cat > /tmp/commit_msg.txt << 'EOF'
[your commit message here]
EOF

# Stage and commit
scripts/create_commit.sh /tmp/commit_msg.txt [files to stage...]
```

If files are already staged, omit the file arguments:
```bash
scripts/create_commit.sh /tmp/commit_msg.txt
```

### 6. Verify

After committing, run `git log -1` to verify the commit was created successfully and the message looks correct.

## Decision Logic

**Auto-commit vs. Review:**
- Default: Auto-commit immediately after creating the message
- Only ask for review if user explicitly says "review first", "show me", "draft", or similar

**Staging strategy:**
- If user staged files already: Include them in commit
- If no files staged: Intelligently stage relevant files based on changes
- Always exclude sensitive files and build artifacts
- Skip submodule changes unless explicitly requested

**Message detail level:**
- Single trivial change (typo, formatting): First line only
- Multiple changes or non-obvious changes: First line + bullet points
- Complex changes affecting multiple areas: Group bullets by component/area

## Examples

**Simple fix:**
```
User: "Commit this fix"
Changes: Fixed null pointer in auth.py line 42

Message:
fix: prevent null pointer in auth validation
```

**Feature with multiple changes:**
```
User: "Commit my changes"
Changes: Added 3 new API endpoints, tests, updated docs
Branch: feat/api-endpoints

Message:
feat: add user management API endpoints

- Add GET/POST/DELETE endpoints for user CRUD operations
- Implement request validation and error handling
- Add comprehensive test coverage for all endpoints
- Update API documentation with endpoint specifications
```

**Refactor:**
```
User: "Save this refactor"
Changes: Renamed variables, reorganized imports in 5 files
Branch: refactor/cleanup-imports

Message:
refactor: reorganize imports and improve naming

- Consolidate imports to top of files
- Rename ambiguous variable names for clarity
- Remove unused imports across modules
```

## Resources

### scripts/create_commit.sh

Shell script that handles staging and committing with proper error handling.

Usage:
```bash
scripts/create_commit.sh <message_file> [files_to_stage...]
```

If files are provided, stages them before committing. Otherwise uses already-staged files.
