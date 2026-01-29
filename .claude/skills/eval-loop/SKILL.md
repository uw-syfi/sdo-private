---
name: eval-loop
description: "Iterative evaluate-implement loop using two subagents. An evaluator subagent checks whether user-defined criteria are met and provides concrete next-step suggestions; an implementer subagent executes those suggestions. Use when the user asks to iteratively improve code until criteria are satisfied, run an eval loop, repeatedly refine output against acceptance criteria, or invokes /eval-loop. Triggers on requests like 'keep improving until all tests pass', 'iterate until coverage is above 90%', 'eval-loop make the UI match the mockup', or any task requiring repeated evaluate-then-fix cycles."
---

# Eval Loop

Orchestrate an iterative evaluate-implement loop between two subagents to meet user-defined criteria.

## Invocation

```
/eval-loop <criteria in natural language>
```

Example: `/eval-loop all unit tests pass and code coverage exceeds 80%`

## Procedure

### 1. Parse input and configure

Extract the **criteria** from the user's argument text. Ask the user:

- "How many maximum iterations?" (default: 10)

Store the criteria and max iterations for the loop.

### 2. Run the loop

Repeat up to max iterations:

#### a. Evaluate (call evaluator subagent)

Use the Task tool with `subagent_type: "general-purpose"` to spawn an evaluator agent. Prompt it with:

```
You are an evaluator. Assess the current state of the codebase against these criteria:

<criteria>
{criteria}
</criteria>

Investigate the codebase thoroughly — read files, run tests, check outputs — whatever is needed to determine whether the criteria are met. You are strictly read-only: do NOT edit, create, or delete any files. Only use read operations (Read, Glob, Grep) and non-destructive Bash commands (e.g., running tests, checking outputs).

Respond with EXACTLY this structure:
- **Status**: MET or NOT_MET
- **Assessment**: 1-3 sentences on current state
- **Suggestions**: If NOT_MET, provide a numbered list of concrete, actionable next steps to get closer to meeting the criteria. Be specific about which files to change and what to do.
```

Parse the evaluator's response:
- If status is **MET**, exit the loop.
- If status is **NOT_MET**, proceed to the implementer.

#### b. Implement (call implementer subagent)

Use the Task tool with `subagent_type: "general-purpose"` to spawn an implementer agent. Prompt it with:

```
You are an implementer. Your job is to make code changes to satisfy the criteria below.

<criteria>
{criteria}
</criteria>

The evaluator assessed the current state and provided these suggestions:

<suggestions>
{evaluator_suggestions}
</suggestions>

Implement the suggestions. Write code, edit files, run commands as needed. Focus only on the suggestions above — do not make unrelated changes.
```

After the implementer finishes, loop back to the evaluator.

### 3. Summarize

After the loop ends (criteria met or max iterations reached), output a summary:

- **Outcome**: Whether criteria were met or max iterations exhausted
- **Iterations used**: N of max
- **Final evaluator assessment**: Last evaluator's assessment text
- **Changes made**: Brief list of what the implementer changed across iterations

## Rules

- The main agent (you) MUST NOT read files, edit code, run tests, or do any implementation work itself. All investigation and implementation happens inside subagents. You must preserve your context window, because you can run for a long time.
- Always run the evaluator after each implementer pass — never skip evaluation.
- On the first iteration, run the evaluator before any implementation to establish a baseline.
- If the evaluator returns MET on the first iteration, report success immediately with no implementation needed.
- Pass only the most recent evaluator suggestions to the implementer — do not accumulate suggestions across iterations.
- The evaluator is strictly read-only. It must never edit, create, or delete files. It may only read files, search code, and run non-destructive commands (e.g., tests, linters).
