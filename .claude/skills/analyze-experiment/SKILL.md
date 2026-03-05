---
name: analyze-experiment
description: >
  Analyze SDS Operator experiment runs by examining agent trajectories, deployment logs, and results.
  Use when the user asks to analyze an experiment, review experiment results, examine agent trajectories,
  understand what an agent did during deployment, compare experiment runs, find detours or inefficiencies
  in agent behavior, or invokes /analyze-experiment. Triggers on requests like "analyze experiment with-ca",
  "what happened in this experiment run", "compare with-ca vs without-ca", "did the agent get stuck",
  "review the trajectory", "how did the agent deploy this app", "what detours did the agent make".
---

# Analyze Experiment

Analyze SDS Operator experiment runs to understand agent deployment behavior, identify inefficiencies, and compare runs.

## Experiment Data Locations

Experiments store data in two places:

1. **Config & aggregated results:** `exp_config/<exp-name>/`
   - `config.toml` — experiment configuration (apps, repeats, agent settings)
   - `logs/results.json` — aggregated results (success, iterations, timing, phase durations)
   - `logs/<app>.log` or `logs/<app>_run_<i>.log` — execution logs
   - `logs/trajectory_*.json` — copied trajectory files

2. **Working directories:** `exp/<app-name>/<exp-name>/` (or `<exp-name>_run_<i>/` for repeats)
   - `.sds/trajectories/trajectory_*.json` — full agent trajectories
   - `.sds/trajectory.json` — symlink to latest trajectory
   - `.sds/deploy.sh`, `.sds/health_check.sh` — generated scripts
   - `.sds/code_analysis.md` — agent's codebase analysis
   - `.sds/deployment_issues.md` — identified issues
   - `.sds/fix_summary.md` — consolidated fix summaries
   - `.sds/logs/deploy_attempt_*.log`, `health_check_attempt_*.log`, `fix_summary_*.log`

**Important:** The user may specify custom paths. Always follow user instructions for where to find data.

## Analysis Workflow

### Step 1: Gather Context

1. Read `exp_config/<exp-name>/logs/results.json` for high-level metrics (success rate, iterations, timing)
2. Read `exp_config/<exp-name>/config.toml` to understand experiment parameters
3. Identify which apps and repeats to analyze

### Step 2: Analyze Trajectories

For each run, read the trajectory JSON. The trajectory structure is documented in `references/trajectory-schema.md`.

**Use subagents for parallel analysis.** When analyzing multiple runs or apps, launch Agent subagents (subagent_type="general-purpose") with targeted questions:

```
For each app/run, spawn a subagent asking:
- "Read trajectory at <path>. Summarize: (1) how many deployment attempts, (2) what errors occurred per attempt, (3) what fixes the agent tried, (4) did the agent succeed, (5) any detours or repeated errors."
```

For single-run deep analysis, ask subagents focused questions:
- "Read <trajectory_path> and list every tool_call in the deployment phase with its exit_code. Which commands failed?"
- "Read <trajectory_path> and compare fix_summary logs across attempts. Did the agent repeat the same fix?"
- "Read <code_analysis.md> and <deployment_issues.md>. Were the identified issues accurate? Did the agent miss anything?"

### Step 3: Identify Patterns

Look for these common patterns. See `references/failure-patterns.md` for detailed descriptions.

**Efficiency indicators:**
- **Attempt count**: 1-3 is healthy, 4+ suggests problems
- **Error repetition**: Same error across attempts = agent stuck
- **Detours**: Agent exploring unrelated files, fixing non-issues, or undoing previous fixes
- **Script quality**: Mixed platform commands (docker + kubectl), missing error handling

**Success indicators:**
- Linear progression (each attempt fixes a distinct issue)
- Targeted fixes (agent reads error, identifies root cause, applies minimal fix)
- Platform consistency (all docker compose OR all kubectl)

**Failure indicators:**
- Identical errors repeating across attempts
- Cascading failures (fixing one thing breaks another)
- Agent not reading error logs before attempting fixes
- Context overflow (huge fix summaries, garbled responses)
- Script generation timeout

### Step 4: Report Findings

Structure the report as:

1. **Summary**: Success/failure, total attempts, wall-clock time
2. **Timeline**: What happened in each phase (code analysis → script generation → deployment attempts → monitoring)
3. **Key Issues**: What problems were encountered and how they were resolved (or not)
4. **Detours & Inefficiencies**: Where the agent wasted time or made unnecessary changes
5. **Recommendations**: How the agent could have done better

### Comparing Multiple Experiments

When comparing experiments (e.g., with-ca vs without-ca):

1. Read `results.json` from each experiment for metrics comparison
2. Use subagents in parallel — one per experiment — to analyze trajectories
3. Compare and contrast:
   - Success rates, iteration counts, timing
   - Quality of code analysis (if applicable)
   - Types of errors encountered
   - Fix strategies and effectiveness
   - Whether code analysis (or other features) helped reduce iterations or avoid errors
4. Present a side-by-side comparison table

## Key Files to Read

| File | Purpose |
|------|---------|
| `results.json` | High-level metrics |
| `trajectory_*.json` | Full agent conversation and tool calls |
| `code_analysis.md` | Agent's understanding of the codebase |
| `deployment_issues.md` | Issues the agent identified proactively |
| `fix_summary_*.log` | Per-attempt fix descriptions |
| `deploy.sh` | Generated deployment script |
| `health_check.sh` | Generated health check script |
| `deploy_attempt_*.log` | Raw deployment output |

## References

- **Trajectory schema**: See [references/trajectory-schema.md](references/trajectory-schema.md) for the full JSON structure
- **Failure patterns**: See [references/failure-patterns.md](references/failure-patterns.md) for common deployment failure patterns and their root causes
