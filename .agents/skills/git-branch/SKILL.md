---
name: git-branch
description: Create git branches following the repository's naming convention (name/type/brief-desc format). Use this skill when the user wants to create a new git branch, start working on a feature/fix/refactor, or mentions needing a new branch for their work. Automatically infers developer name from git config and branch type from the task description.
---

# Git Branch Creation

Create branches following the format: `<name>/<type>/<brief-desc>`

## Examples from This Repository

Past branches in this repository:
- `vic/feat/code-analyzer` - New feature for code analysis
- `vic/fix/log-capture` - Bug fix for log capturing
- `vic/refactor/jinja-prompt` - Refactoring prompt system
- `vic/chore/update-bash-file` - Maintenance task

## Workflow

### 1. Gather Information

Ask the user for a brief description of what the branch is for. This should be a short summary of the work to be done.

**Example questions:**
- "What will you be working on in this branch?"
- "What's the purpose of this new branch?"

### 2. Infer Developer Name

Extract the developer name from `git config user.name`:
- Take the first part before any hyphen or space
- Convert to lowercase
- Example: `vic-lsh` → `vic`, `John Doe` → `john`

**If uncertain about the mapping** (e.g., unclear git username, multiple possible interpretations), ask the user:
- "I see your git username is '{username}'. Should I use '{inferred}' for the branch name, or would you prefer something else?"

**Important:** DO NOT hard-code developer names in the branch creation logic.

### 3. Infer Branch Type

Based on the description, choose the appropriate type:
- `feat` - New features or functionality
- `fix` - Bug fixes
- `refactor` - Code refactoring without changing functionality
- `chore` - Maintenance tasks, updates, tooling

**Examples:**
- "Add user authentication" → `feat`
- "Fix login redirect issue" → `fix`
- "Clean up error handling" → `refactor`
- "Update dependencies" → `chore`

If the type is ambiguous, choose the most appropriate or ask the user for clarification.

### 4. Format Brief Description

Convert the description to the required format:
- Lowercase only
- Use dashes to separate words
- Keep it concise (2-4 words typically)

**Examples:**
- "Add user authentication" → `user-auth`
- "Fix login redirect issue" → `login-redirect`
- "Update deployment scripts" → `update-deploy-scripts`

### 5. Create and Checkout Branch

Use the `scripts/create_branch.sh` script or git commands directly:

```bash
# Using the script
scripts/create_branch.sh <type> <brief-desc> [name]

# Or directly with git
git checkout -b <name>/<type>/<brief-desc>
```

**Default behavior:** Automatically checkout to the new branch after creation without asking for permission.

**Exception:** Only ask for confirmation before creating the branch if the user explicitly requests review first (e.g., "create a branch but let me review it first").

## Complete Example Interaction

**User:** "I need to work on adding a health check endpoint"

**Claude:**
1. Infers: `feat` type (new functionality)
2. Gets git user: `vic-lsh` → `vic`
3. Formats description: `health-check-endpoint`
4. Creates branch: `vic/feat/health-check-endpoint`
5. Automatically checks out to the new branch

**Output:**
```bash
git checkout -b vic/feat/health-check-endpoint
```

## Edge Cases

**Multiple words in description:**
- "Fix authentication in login flow" → `fix-auth-login`
- Keep it concise, focus on key terms

**Uncertain developer name:**
- Git config is `James L` → Ask: "Should I use 'james' for the branch name?"

**User requests review first:**
- User: "Create a branch but let me approve the name first"
- Show proposed name and wait for confirmation before creating
