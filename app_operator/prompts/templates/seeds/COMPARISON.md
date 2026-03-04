# Baseline vs. Seed Prompts Comparison

This document compares the baseline Jinja2 templates with GEPA-style seed prompts for all 10 SDS prompts.

## Overview

| Prompt Name | Baseline Lines | Seed Lines | Reduction |
|-------------|---------------|------------|-----------|
| deployer_system | 0 (no template) | 1 | N/A |
| deployer_generate_script | ~80 | 7 | 91% |
| deployer_fix_error | 112 | 7 | 94% |
| deployer_summarize | ~30 | 5 | 83% |
| code_analyzer_system | ~50 | 1 | 98% |
| code_analyzer_user | ~100 | 6 | 94% |
| monitor_analyze_health | ~40 | 6 | 85% |
| agentflow_system | 338 | 4 | 99% |
| agentflow_user | ~60 | 7 | 88% |
| agentflow_repair | ~20 | 5 | 75% |
| **TOTAL** | ~830 | 49 | **94%** |

## Detailed Comparisons

### 1. deployer_fix_error (Most Critical)

**Baseline (112 lines):**
```
SECTIONS:
1. Platform Awareness (20 lines)
   - Instructions to detect Docker Compose/Kubernetes/Docker
   - Platform-specific command requirements

2. DO's and DON'Ts (20 lines)
   - 15 DO's: platform detection, error analysis, targeted fixes
   - 11 DON'Ts: no platform changes, no random changes

3. Approach (7 steps)
   - Step-by-step diagnostic workflow

4. Context Size Limits (10 lines)
   - Always use --tail for logs
   - Diagnose one service at a time
   - Read with tail, not cat

5. Expected Output (5 lines)
   - Summary format with <summary> tags
   - Must state issues, fixes, platform
```

**Seed (7 lines):**
```
A deployment has failed. Analyze the error and fix the deployment scripts.

Error: {{ error_context }}
Attempt: {{ attempt }} of {{ max_attempts }}
Scripts: {{ deploy_script }}, {{ health_check_script }}
{% if previous_summary_note %}Previous attempt: {{ previous_summary_note }}{% endif %}

Identify the root cause and fix the issue.
```

**What's Missing in Seed:**
- Platform detection instructions
- Log reading best practices (--tail flags)
- Step-by-step workflow
- Output formatting requirements
- Safety constraints (don't change platforms)

**GEPA Hypothesis:** The optimizer will discover these constraints if they actually improve metrics. If not, they may be unnecessary overhead.

---

### 2. agentflow_system (Most Elaborate)

**Baseline (338 lines):**
```
SECTIONS:
1. Role and Mission (10 lines)
2. Available Runtime API (30 lines)
   - create_agent()
   - fan_out()
   - summarize()
   - judge_loop()

3. Tool Descriptions (80 lines)
   - read_file, write_file, list_files
   - find_files, search_content, run_command

4. Orchestration Patterns (150 lines)
   - fan_out examples
   - summarize examples
   - judge_loop examples
   - Combined patterns

5. Best Practices (50 lines)
   - When to use each pattern
   - Error handling
   - Performance tips
```

**Seed (4 lines):**
```
You are an autonomous script generation agent. Generate Python scripts that use orchestration patterns for complex multi-agent workflows.

Work directory: {{ work_dir }}
Loop bound: {{ loop_bound }}

You have access to: create_agent(), fan_out(), summarize(), judge_loop(), and file/command tools.
```

**What's Missing in Seed:**
- Comprehensive tool documentation
- Pattern examples with code
- Best practices guidelines
- Detailed API signatures

**GEPA Hypothesis:** The optimizer will learn which documentation actually helps script generation quality. It may discover that examples are critical, or that simple tool names are sufficient.

---

### 3. deployer_generate_script

**Baseline (~80 lines):**
```
SECTIONS:
1. Task Description (5 lines)
2. Analysis Requirements (20 lines)
   - Tech stack detection
   - Build process identification
   - Service architecture

3. deploy.sh Requirements (25 lines)
   - Must use --build flag
   - Proper service ordering
   - Health check integration

4. health_check.sh Requirements (20 lines)
   - Multiple check types (ports, endpoints, containers)
   - Exit code 0 for success
   - Meaningful error messages

5. Best Practices (10 lines)
```

**Seed (7 lines):**
```
Analyze the repository and generate deployment scripts.

Repository: {{ repo_path }}
Code analysis: {{ code_analysis }}
Deployment issues: {{ deployment_issues }}

Generate:
1. deploy.sh - Script to deploy the application
2. health_check.sh - Script to verify the deployment is healthy
```

**What's Missing in Seed:**
- --build flag requirement
- Health check diversity requirements
- Service ordering constraints
- Error handling guidelines

**GEPA Hypothesis:** The optimizer will learn what makes a good deployment script by seeing successful vs. failed examples. The health_check_quality metric prevents trivial solutions.

---

## Key Insights

### What Seeds Preserve
✅ **Core task description**: What needs to be done
✅ **Input/output interface**: Template variables and expected outputs
✅ **Basic structure**: Sections for different types of content

### What Seeds Remove
❌ **Prescriptive workflows**: Step 1, Step 2, Step 3...
❌ **Specific constraints**: "Always use --tail", "Never change platforms"
❌ **Best practices**: "DO's and DON'Ts" lists
❌ **Examples and demonstrations**: Code snippets, pattern examples

### Why This Matters

**Traditional approach (baseline → optimized):**
```
Detailed 112-line prompt
    ↓
Optimizer refines within existing structure
    ↓
Slightly better 115-line prompt
```
Risk: Stuck in local minimum defined by initial structure

**GEPA approach (seed → optimized):**
```
Minimal 7-line prompt
    ↓
Optimizer discovers what helps
    ↓
Evolved 50-line prompt with different structure
```
Opportunity: May find better solution space

---

## Experimental Design

To validate GEPA approach for SDS:

### Experiment 1: Single Prompt (deployer_fix_error)
```bash
# Control: Baseline → Optimized
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --optimizer COPRO

# Treatment: Seed → Optimized
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --optimizer COPRO \
    --use-seeds

# Compare metrics on fresh test set
```

**Hypothesis:** Seed-optimized will achieve similar or better success rate with fewer tokens.

### Experiment 2: All Prompts
```bash
# Optimize all 10 prompts from seeds
uv run -m app_operator optimize-prompts \
    --prompts deployer_system deployer_generate_script deployer_fix_error deployer_summarize \
              code_analyzer_system code_analyzer_user monitor_analyze_health \
              agentflow_system agentflow_user agentflow_repair \
    --use-seeds \
    --optimizer MIPROv2
```

**Hypothesis:** Seed-optimized prompts will be more consistent across different application types.

### Experiment 3: Iterative Refinement
```bash
# Round 1: Pure seeds
uv run -m app_operator optimize-prompts --use-seeds --prompts deployer_fix_error
# Evaluate and identify gaps

# Round 2: Seeds + minimal hints
# Edit seeds/deployer_fix_error.jinja2 to add one critical constraint
uv run -m app_operator optimize-prompts --use-seeds --prompts deployer_fix_error
# Evaluate again

# Round 3: Seeds + more hints
# Repeat until performance matches baseline
```

**Hypothesis:** 2-3 rounds of refinement will reach baseline performance with simpler prompts.

---

## Metrics for Success

### Primary Metrics (from CompositeMetric)
1. **Success Rate** (50% weight): % of deployments that succeed
2. **Iteration Efficiency** (25% weight): Average attempts to success
3. **Token Usage** (15% weight): Total tokens consumed
4. **Health Check Quality** (10% weight): Non-trivial health checks

### Secondary Metrics
5. **Prompt Length**: Number of tokens in optimized instruction
6. **Generalization**: Success rate on new applications not in training set
7. **Interpretability**: Human readability of optimized prompts

### Success Criteria

**Minimum Viable:**
- Seed-optimized achieves **≥ 90%** of baseline success rate
- Uses **≤ 110%** of baseline tokens

**Strong Success:**
- Seed-optimized achieves **≥ 100%** of baseline success rate
- Uses **≤ 100%** of baseline tokens
- Discovers **new strategies** not in baseline

**Exceptional Success:**
- Seed-optimized achieves **> 105%** of baseline success rate
- Uses **< 90%** of baseline tokens
- Generalizes **better to new apps**

---

## Next Steps

1. **Run Experiment 1**: Single prompt comparison (deployer_fix_error)
2. **Analyze results**: What did optimizer discover?
3. **Document learnings**: Update seeds with insights
4. **Run Experiment 2**: All prompts optimization
5. **Compare with baseline v7**: Full system evaluation
6. **Publish findings**: Share results with community
