# GEPA-Style Seed Prompts for SDS

## Motivation

The current v7 optimized prompts are very elaborate and may be stuck in a local minimum. Following the GEPA paper's approach, we should:

1. **Start small**: Use minimal seed prompts that just describe the core task
2. **Grow through optimization**: Let the optimizer (COPRO, MIPROv2, etc.) discover what details actually improve metrics
3. **Avoid prescriptive structure**: Don't force the optimizer into a specific format with meta-prompts

## Current Problems with v7

The current `deployer_fix_error` optimized prompt:
- Is a meta-prompt that generates SOPs with 5 structured sections
- Prescribes specific formatting (hypothesis-driven workflow, fix blocks, approval phrases)
- May constrain the optimizer from finding better approaches
- Doesn't directly map to the signature's output field

## GEPA Example (for reference)

**Seed:** "Given the fields question, summary 1, produce the fields query."

**Optimized:** 400+ word structured prompt with sections on:
- Input Understanding
- Purpose and Context
- Key Observations and Lessons
- How to Build the Query
- Practical Strategy
- Output

The optimizer discovered all these sections by learning what helps performance.

---

## Proposed Seed Prompts

### 1. deployer_system
**Current baseline:** 0 lines (no template exists)
**Proposed seed:**
```
You are a deployment agent for {{ agent_name }}. Help deploy applications in {{ repo_path }}.
```

### 2. deployer_generate_script
**Current baseline:** ~80 lines with detailed instructions
**Proposed seed:**
```
Analyze the repository and generate deployment scripts.

Repository: {{ repo_path }}
Code analysis: {{ code_analysis }}
Deployment issues: {{ deployment_issues }}

Generate:
1. deploy.sh - Script to deploy the application
2. health_check.sh - Script to verify the deployment is healthy
```

### 3. deployer_fix_error (MOST CRITICAL)
**Current baseline:** 112 lines with platform detection, DO's/DON'Ts, approach steps
**Current v7 optimized:** Meta-prompt with 5 structured sections
**Proposed seed:**
```
A deployment has failed. Analyze the error and fix the deployment scripts.

Error: {{ error_context }}
Attempt: {{ attempt }} of {{ max_attempts }}
Scripts: {{ deploy_script }}, {{ health_check_script }}
{% if previous_summary_note %}Previous attempt: {{ previous_summary_note }}{% endif %}

Identify the root cause and fix the issue.
```

**Rationale:** Let the optimizer discover:
- Platform detection strategies
- Log reading best practices (--tail flags)
- Output format requirements
- Safety constraints
- Diagnostic workflows

### 4. deployer_summarize
**Current baseline:** ~30 lines
**Proposed seed:**
```
Summarize the deployment activity in one line.

Deployment log: {{ deployment_log }}

Output format: <output_msg>one-line summary</output_msg>
```

### 5. code_analyzer_system
**Current baseline:** ~50 lines
**Proposed seed:**
```
You are a code analysis agent for {{ agent_name }}. Analyze codebases in {{ repo_path }} to identify deployment requirements and potential issues.
```

### 6. code_analyzer_user
**Current baseline:** ~100 lines with detailed analysis checklist
**Proposed seed:**
```
Analyze this repository to understand how to deploy it.

Repository: {{ repo_path }}
File tree: {{ file_tree }}

Identify:
1. What type of application this is
2. How it should be deployed
3. Potential deployment issues
```

### 7. monitor_analyze_health
**Current baseline:** ~40 lines
**Proposed seed:**
```
Analyze the application health check results.

Health check output: {{ health_check_output }}
Exit code: {{ exit_code }}
Iteration: {{ iteration }}

Determine if the application is healthy and provide your analysis.
```

### 8. agentflow_system
**Current baseline:** 338 lines with orchestration patterns, tool docs, examples
**Proposed seed:**
```
You are an autonomous script generation agent. Generate Python scripts that use orchestration patterns for complex multi-agent workflows.

Work directory: {{ work_dir }}
Loop bound: {{ loop_bound }}

You have access to: create_agent(), fan_out(), summarize(), judge_loop(), and file/command tools.
```

### 9. agentflow_user
**Current baseline:** ~60 lines with validation checklist
**Proposed seed:**
```
Generate a Python script for this task.

Task: {{ user_request }}
{% if clarification_history %}Previous Q&A: {{ clarification_history }}{% endif %}
Work directory: {{ work_dir }}
Loop bound: {{ loop_bound }}

If you need clarification, output status='clarify' with questions.
If ready, output status='ready' with the script code.
```

### 10. agentflow_repair
**Current baseline:** ~20 lines
**Proposed seed:**
```
Repair this malformed JSON response.

Original response: {{ original_response }}
Error: {{ error_message }}

Output a corrected, valid JSON response.
```

---

## Implementation Strategy

### Phase 1: Create Seed Prompt Templates
1. Create new Jinja2 templates in `app_operator/prompts/templates/seeds/`
2. One template per signature using the proposed seeds above
3. Keep them minimal (< 10 lines each)

### Phase 2: Re-run Optimization
1. Configure optimizer to start from seed prompts instead of baseline
2. Use COPRO or MIPROv2 (better for starting from scratch)
3. Let it run for more iterations to allow growth
4. Monitor metric improvements at each iteration

### Phase 3: Compare Results
1. Compare seed → optimized against baseline → optimized (v7)
2. Metrics to track:
   - Deployment success rate
   - Iteration efficiency
   - Token usage
   - Health check quality
3. Analyze what the optimizer discovered vs. what we hardcoded

### Phase 4: Iterative Refinement
If results are not better:
1. Analyze which prompts perform worse
2. Add minimal hints to those seeds (e.g., "Use --tail for logs")
3. Re-optimize
4. Repeat until metrics improve

---

## Expected Benefits

1. **Escape local minima**: Fresh start allows optimizer to explore new solution space
2. **Discover effective patterns**: Learn what actually helps vs. what we think helps
3. **Reduce overfitting**: Simpler prompts may generalize better
4. **Interpretable improvements**: See what the optimizer adds at each step

## Risks and Mitigations

**Risk 1:** Optimizer may not discover critical constraints (e.g., platform detection)
- **Mitigation:** Monitor early iterations; add minimal hints if needed

**Risk 2:** May require more training examples or iterations
- **Mitigation:** Use larger training set; run optimizer longer

**Risk 3:** Performance may initially be worse
- **Mitigation:** This is expected; GEPA shows improvements come with iterations

---

## Next Steps

1. **Review and approve** this proposal
2. **Create seed templates** in `app_operator/prompts/templates/seeds/`
3. **Modify optimizer** to use seed templates as starting point
4. **Run optimization** with increased iterations
5. **Evaluate results** and iterate

## Open Questions

1. Should we keep baseline templates for fallback?
2. Which optimizer is best for starting from minimal seeds? (COPRO, MIPROv2, BootstrapFewShot?)
3. How many iterations should we run? (GEPA used 5-10 rounds)
4. Should we optimize all 10 prompts or start with just deployer_fix_error?
