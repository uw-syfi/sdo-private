---
name: compare-bench-runs
description: >
  Compare two SREGym benchmark runs problem-by-problem, analyzing agent behavior differences,
  diagnosis quality, timing, and judge effectiveness. Use when the user asks to compare benchmark
  runs, diff two SREGym experiments, analyze differences between crucible runs, understand why
  one run performed differently, or invokes /compare-bench-runs. Triggers on requests like
  "compare these two runs", "diff noltm vs ltm", "why did this run regress", "compare benchmark
  results", "analyze the difference between run A and run B", "deep comparison between runs".
---

# Compare Benchmark Runs

Compare two SREGym crucible benchmark runs by analyzing every problem's trajectory, identifying
behavioral differences, and synthesizing findings.

## Prerequisites

Each run directory (`bench/sregym/logs/<run_name>/`) contains:
- `sregym_*_w*_<problem>.md` — per-problem trajectory files (see `references/trajectory-format.md`)
- `*_crucible_results.csv` — result CSVs
- `problem_logs/<problem>.log` — execution logs

## Workflow

### Step 1: Extract and Compare Results

Run the extraction script to get a structured comparison:

```bash
python3 <skill_dir>/scripts/extract_results.py <run1_dir> <run2_dir> \
  --label1 <run1_name> --label2 <run2_name> > /tmp/bench_comparison.json
```

Read the JSON output to identify:
- **Improved problems** (run1 failed, run2 succeeded)
- **Regressed problems** (run1 succeeded, run2 failed)
- **Both-succeeded with TTL changes** (sort by speedup_pct)
- **Both-failed** (check for significant TTL differences)
- **Problems only in one run**

Print a summary table to the user before proceeding.

### Step 2: Analyze Each Problem with Subagents

Create an output directory (bench/sregym/logs/diff/<diff_dir>_analysis).

Launch one Agent subagent per problem (or batch 2-3 similar problems per agent). Use `run_in_background: true` and launch in parallel batches of ~15.

Each subagent prompt should include:
1. The problem name and outcome in both runs (success/fail, TTL, accuracy)
2. The exact file paths for both trajectory MDs
3. Instructions to compare:
   - Diagnoses produced by each agent
   - Why each succeeded or failed
   - Investigation approach differences (tool calls, breadth vs depth)
   - Judge behavior (approved/rejected, was it correct?)
   - KB/LTM influence signals (references to "previous incidents", pattern matching)
   - Token/TTL comparison and what drove the difference
4. Instructions to write the analysis to a specific output file

**Subagent prompt template:**
```
Analyze benchmark problem "<problem>" across two runs.
<OUTCOME>. Run1 (<label1>) TTL: <ttl1>s, Run2 (<label2>) TTL: <ttl2>s.

Read these MD trajectory files:
- Run1: <run1_dir>/<run1_md_file>
- Run2: <run2_dir>/<run2_md_file>

Compare: (1) diagnoses produced, (2) why each succeeded/failed,
(3) investigation approach differences, (4) judge behavior and correctness,
(5) any LTM/KB behavioral differences, (6) TTL comparison and drivers.

Write analysis to <output_dir>/<problem>.md
```

### Step 3: Synthesize Findings

After all subagents complete, write a `SYNTHESIS.md` in the output directory covering:

1. **Overview table**: Solve rates, median TTL, total problems
2. **Outcome changes**: Tables for improvements and regressions with key drivers
3. **Both-succeeded TTL table**: Sorted by speedup percentage
4. **Both-failed table**: With impact assessment (positive/negative/neutral for speed and accuracy)
5. **High-level takeaways**: Patterns across problems (see Analysis Patterns below)
6. **Judge effectiveness analysis**: Where the judge helped vs hurt
7. **Actionable recommendations**: For KB content, agent architecture, and judge improvements
8. **Per-problem file index**: Links to all individual analysis files

### Prioritization

Adapt focus based on user request:
- **"Why did X regress?"** — Focus on outcome-changed problems
- **"Was LTM helpful?"** — Focus on both-succeeded TTL changes + regressions
- **"How did the judge do?"** — Focus on judge behavior across all problems
- **"What problems are hardest?"** — Focus on both-failed problems
