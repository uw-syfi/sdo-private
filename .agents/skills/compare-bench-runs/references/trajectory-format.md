# SREGym Trajectory MD Format

Each problem's trajectory is stored under `<run_dir>/crucible/<problem_name>/` as two markdown files: `diagnosis.md` (diagnosis phase) and `mitigation.md` (mitigation phase). The two are concatenated for analysis.

## Structure

```
# SRE Judged Session State
## Session
- App: <app_name> / Namespace: <namespace>

## Diagnosis
### Iteration N — Agent Hypothesis
**Diagnosis**: <agent's root cause hypothesis>
**Justification**: <evidence and reasoning>

### Iteration N — Judge Independent Findings
<judge's blind investigation results>

### Iteration N — Judge Verdict (diagnosis)
- Status: APPROVED | REJECTED
- Reasoning: <why approved/rejected>

<benchmark_result>
success: True|False
message: ...
{
  "Diagnosis": {
    "judgment": "True|False",
    "reasoning": "...",
    "success": true|false,
    "accuracy": 0.0-100.0
  },
  "TTL": <seconds>
}
</benchmark_result>

## Mitigation
### Iteration N — Agent Strategy
**Mitigation**: <what the agent did>
**Justification**: <why>

### Iteration N — Judge Independent Findings
<judge's verification>

### Iteration N — Judge Verdict (mitigation)
- Status: APPROVED | REJECTED
- Reasoning: <why>

<benchmark_result>
success: True|False
{
  "Diagnosis": { ... },
  "TTL": <seconds>,
  "Mitigation": { "success": true|false },
  "TTM": <seconds>
}
</benchmark_result>
```

## Key Fields to Extract

- **success**: `True` or `False` in `<benchmark_result>` block
- **TTL** (Time to Locate): Seconds from start to accepted diagnosis
- **TTM** (Time to Mitigate): Seconds from start to accepted mitigation
- **accuracy**: 0.0-100.0, how closely diagnosis matched expected root cause
- **Diagnosis iterations**: Count of `### Iteration N — Agent Hypothesis` sections
- **Mitigation iterations**: Count of `### Iteration N — Agent Strategy` sections

## Analysis Signals

When comparing two runs of the same problem, look for:

1. **Different diagnoses**: What root cause did each agent identify?
2. **Investigation breadth**: How many kubectl commands / tool calls? Did one explore more?
3. **Judge behavior**: Did the judge approve/reject differently? Was it correct to do so?
4. **KB/LTM influence**: Does the agent reference "previous incidents" or "known patterns"?
5. **Iteration count**: More iterations = more judge rejections = likely over-generalization or inaccuracy
6. **Anchoring signals**: Did the agent lock onto a pattern without verifying?
7. **"Found it then lost it"**: Did the agent identify the correct cause then abandon it after judge feedback?
