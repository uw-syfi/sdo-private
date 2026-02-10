# GEPA-Style Prompt Optimization Tutorial

This tutorial demonstrates how to optimize SDS prompts using the GEPA (Generalized Prompt Engineering via Automated Optimization) approach.

## Background

Traditional prompt optimization often starts with elaborate, detailed prompts (100+ lines with specific instructions, workflows, and constraints). This can lead to:
- **Local minima**: The optimizer gets stuck refining a suboptimal structure
- **Overfitting**: Complex prompts may not generalize well
- **Missed opportunities**: Better approaches may exist that we haven't considered

GEPA's approach is different:
1. Start with minimal "seed" prompts (< 10 lines) that just describe the core task
2. Let the optimizer gradually discover what details actually improve performance
3. Avoid prescribing specific structures or workflows upfront

## Example from GEPA Paper

**Seed (7 words):**
```
Given the fields question, summary 1, produce the fields query.
```

**After Optimization (400+ words):**
The optimizer discovered and added:
- Input Understanding section
- Purpose and Context explanation
- Key Observations and Lessons
- How to Build the Query instructions
- Practical Strategy guidelines
- Output specifications

All of these emerged from learning what helps performance, not from human design.

---

## SDS Implementation

### Step 1: Compare Baseline vs. Seed Prompts

Let's examine the `deployer_fix_error` prompt:

**Baseline template (112 lines):**
```bash
cat app_operator/prompts/templates/deployer/fix_error.jinja2 | wc -l
# Output: 112
```

Key sections:
- Platform awareness instructions (20 lines)
- DO's and DON'Ts (20 lines)
- 7-step diagnostic approach
- Context size limits section
- Output format requirements

**Seed template (7 lines):**
```bash
cat app_operator/prompts/templates/seeds/deployer_fix_error.jinja2
```
```jinja2
A deployment has failed. Analyze the error and fix the deployment scripts.

Error: {{ error_context }}
Attempt: {{ attempt }} of {{ max_attempts }}
Scripts: {{ deploy_script }}, {{ health_check_script }}
{% if previous_summary_note %}Previous attempt: {{ previous_summary_note }}{% endif %}

Identify the root cause and fix the issue.
```

**Difference:** 112 lines → 7 lines (94% reduction)

### Step 2: Run Baseline Optimization (Current v7)

First, let's establish a baseline by optimizing from the detailed templates:

```bash
# Ensure you have trajectory data
ls .sds/trajectories/*.json

# Run optimization from baseline templates
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --optimizer COPRO \
    --num-examples 30 \
    --teacher-model "vertex_ai/gemini-2.5-pro"
```

**Expected output:**
```
Starting from: Baseline templates
Optimizer: COPRO
...
Optimized prompts saved to: app_operator/prompts/optimized/v8/
```

### Step 3: Run GEPA-Style Optimization (From Seeds)

Now, let's optimize from minimal seed prompts:

```bash
# Run optimization from seed templates
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --optimizer COPRO \
    --num-examples 30 \
    --teacher-model "vertex_ai/gemini-2.5-pro" \
    --use-seeds
```

**Expected output:**
```
Starting from: GEPA-style seeds
Optimizer: COPRO
...
Optimized prompts saved to: app_operator/prompts/optimized/v9/
```

### Step 4: Compare Results

Analyze what the optimizer discovered:

```bash
# Examine the baseline-optimized prompt
cat app_operator/prompts/optimized/v8/deployer_fix_error.dspy.json

# Examine the seed-optimized prompt
cat app_operator/prompts/optimized/v9/deployer_fix_error.dspy.json

# Compare the optimized instructions
diff -u \
  <(jq -r '.optimized_instruction' app_operator/prompts/optimized/v8/deployer_fix_error.dspy.json) \
  <(jq -r '.optimized_instruction' app_operator/prompts/optimized/v9/deployer_fix_error.dspy.json)
```

**Questions to answer:**
1. What did the optimizer add to the seed prompt?
2. Did it discover the same constraints as the baseline (platform detection, --tail flags)?
3. Did it find new strategies we didn't think of?
4. Is the structure different from the baseline?

### Step 5: Evaluate Performance

Compare metrics between baseline-optimized and seed-optimized versions:

```bash
# Collect fresh trajectories with baseline-optimized prompts
# (in sds.toml: optimized_version = "v8")
mkdir -p exp/hotel_baseline_opt_v8
uv run -m app_operator run /path/to/hotelReservation

# Collect trajectories with seed-optimized prompts
# (in sds.toml: optimized_version = "v9")
mkdir -p exp/hotel_seed_opt_v9
uv run -m app_operator run /path/to/hotelReservation

# Compare metrics
uv run -m app_operator analyze-prompts \
    --compare exp/hotel_baseline_opt_v8/.sds/trajectories:exp/hotel_seed_opt_v9/.sds/trajectories
```

**Metrics to compare:**
- **Success rate**: Which version deploys more successfully?
- **Iteration efficiency**: Which version requires fewer attempts?
- **Token usage**: Which version is more cost-efficient?
- **Health check quality**: Which version generates better health checks?

---

## Advanced: Multi-Prompt Optimization

Optimize all 10 SDS prompts using seeds:

```bash
# List all available prompts
uv run -m app_operator optimize-prompts --list-prompts

# Optimize all deployer prompts from seeds
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error deployer_generate_script deployer_summarize \
    --optimizer MIPROv2 \
    --use-seeds \
    --num-examples 50

# Optimize all prompts from seeds
uv run -m app_operator optimize-prompts \
    --prompts deployer_system deployer_generate_script deployer_fix_error deployer_summarize \
              code_analyzer_system code_analyzer_user monitor_analyze_health \
              agentflow_system agentflow_user agentflow_repair \
    --optimizer MIPROv2 \
    --use-seeds \
    --num-examples 50
```

---

## Best Practices

### 1. Seed Design Principles

**Good seeds:**
- ✅ Describe the task clearly and concisely
- ✅ Include input/output specifications
- ✅ Use template variables correctly
- ✅ Are 3-10 lines long

**Bad seeds:**
- ❌ Too vague: "Fix the deployment"
- ❌ Too prescriptive: "First do X, then Y, then Z..."
- ❌ Missing context: No mention of inputs/outputs
- ❌ Too long: Defeats the purpose of starting small

**Example (good):**
```jinja2
Analyze the application health check results.

Health check output: {{ health_check_output }}
Exit code: {{ exit_code }}

Determine if the application is healthy.
```

### 2. Optimizer Selection

Different optimizers work better for different scenarios:

| Optimizer | Best For | Training Time | Notes |
|-----------|----------|---------------|-------|
| **BootstrapFewShot** | Quick iteration | Fast | Good for initial experiments |
| **BootstrapFewShotWithRandomSearch** | Better quality | Medium | Adds random search over BootstrapFewShot |
| **MIPROv2** | High quality | Slow | Best for final optimization |
| **COPRO** | Seed → Growth | Medium | Best for GEPA approach |

**Recommendation for GEPA:** Use **COPRO** or **MIPROv2** when starting from seeds, as they're designed to iteratively refine instructions.

### 3. Training Data Requirements

For GEPA to work well, you need:
- **Diverse examples**: Various failure modes, apps, scenarios
- **Sufficient quantity**: At least 20-30 examples per prompt
- **Quality labels**: Ground truth from successful deployments
- **Phase filtering**: Don't mix examples from different phases

```bash
# Check if you have enough data
uv run -m app_operator analyze-prompts --phase deployment

# Expected output:
#   Total examples: 45
#   Success rate: 68%
#   Average iterations: 3.2
```

If you have < 20 examples, run more deployments first:
```bash
# Generate more training data
for i in {1..5}; do
    uv run -m app_operator run /path/to/app
done
```

### 4. Iterative Refinement

If seed-optimized prompts perform worse than baseline:

**Step 1: Analyze the gap**
```bash
# What did the optimizer miss?
diff baseline_prompt.txt seed_optimized_prompt.txt
```

**Step 2: Add minimal hints to seeds**
```jinja2
# Before (too minimal)
Fix the deployment error.

# After (minimal hint added)
Fix the deployment error. Use --tail when reading logs to avoid context overflow.
```

**Step 3: Re-optimize**
```bash
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --use-seeds \
    --optimizer COPRO
```

**Step 4: Evaluate again**
```bash
uv run -m app_operator analyze-prompts --compare v8:v10
```

Repeat until performance matches or exceeds baseline.

---

## Troubleshooting

### Issue: "No training examples found"

**Solution:** Ensure trajectory files exist and contain relevant data:
```bash
ls -lh .sds/trajectories/
uv run -m app_operator analyze-prompts --phase deployment
```

### Issue: "Optimizer produced worse results than baseline"

**Possible causes:**
1. **Insufficient training data**: Need more diverse examples
2. **Seeds too minimal**: Add critical hints (e.g., platform detection)
3. **Wrong optimizer**: Try COPRO or MIPROv2 instead
4. **Metric mismatch**: Optimizer optimizing for wrong metric

**Solution:** Start with minimal hints and iterate.

### Issue: "Optimization is too slow"

**Solution:** Reduce training examples or use faster optimizer:
```bash
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --use-seeds \
    --num-examples 20 \
    --optimizer BootstrapFewShot  # Faster than COPRO/MIPROv2
```

---

## Expected Outcomes

Based on GEPA paper results, you should expect:

**Short term (first optimization):**
- Seed-optimized prompts may perform **5-15% worse** than baseline-optimized
- They will be **shorter and simpler** (good for interpretability)
- Optimizer will discover **some but not all** baseline constraints

**Medium term (after hints + re-optimization):**
- Seed-optimized prompts should **match baseline performance**
- They may discover **new strategies** not in baseline
- More **generalizable** to new scenarios

**Long term (multiple iterations):**
- Seed-optimized prompts should **exceed baseline performance**
- **Lower token costs** due to more focused instructions
- **Better adaptation** to new application types

---

## Next Steps

After completing this tutorial:

1. **Document findings**: What did the optimizer discover?
2. **Update seeds**: Add minimal hints based on learnings
3. **Re-optimize**: Run another round with improved seeds
4. **Contribute back**: Share successful seed patterns

## References

- GEPA Paper: [Link to paper]
- DSPy Documentation: https://dspy-docs.vercel.app/
- SDS DSPy Integration: `app_operator/dspy_integration/README.md`
- Seed Templates: `app_operator/prompts/templates/seeds/`
