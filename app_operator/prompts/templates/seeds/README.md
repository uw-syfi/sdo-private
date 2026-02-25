# Seed Prompts for GEPA-Style Optimization

This directory contains minimal "seed" prompts for DSPy optimization, following the approach from the GEPA paper (Generalized Prompt Engineering via Automated Optimization).

## Philosophy

**Traditional approach (current v7):**
- Start with detailed, prescriptive prompts (100+ lines)
- Include specific instructions, workflows, constraints
- Risk: May be stuck in a local minimum

**GEPA approach (these seeds):**
- Start with minimal prompts (< 10 lines)
- Just describe the core task
- Let the optimizer discover what details actually help
- Gradually grow through iterative optimization

## Comparison: Baseline vs. Seed vs. Optimized

| Prompt | Baseline Lines | Seed Lines | v7 Optimized |
|--------|---------------|------------|--------------|
| deployer_system | 0 | 1 | N/A |
| deployer_generate_script | ~80 | 7 | N/A |
| deployer_fix_error | 112 | 7 | Meta-prompt with 5 sections |
| deployer_summarize | ~30 | 5 | N/A |
| code_analyzer_system | ~50 | 1 | N/A |
| code_analyzer_user | ~100 | 6 | N/A |
| monitor_analyze_health | ~40 | 6 | N/A |
| agentflow_system | 338 | 4 | N/A |
| agentflow_user | ~60 | 7 | N/A |
| agentflow_repair | ~20 | 5 | N/A |

## Usage

To optimize from seed prompts instead of baseline templates:

```bash
# Option 1: Modify optimizer to use seeds directory
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --optimizer MIPROv2 \
    --use-seeds

# Option 2: Temporarily replace baseline templates
cp app_operator/prompts/templates/seeds/* app_operator/prompts/templates/deployer/
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --optimizer MIPROv2
```

## Key Differences from Baseline

### deployer_fix_error
**Baseline (112 lines):**
- Platform awareness section (20 lines)
- DO's and DON'Ts (20 lines)
- Step-by-step approach (7 steps)
- Context size limits section (10 lines)
- Expected output format

**Seed (7 lines):**
- Just states: deployment failed, here's the error, fix it
- No prescribed workflow
- No specific constraints
- Let optimizer discover effective patterns

### agentflow_system
**Baseline (338 lines):**
- Comprehensive orchestration docs
- Tool descriptions with examples
- Pattern examples (fan_out, summarize, judge_loop)
- Best practices section

**Seed (4 lines):**
- States role and available tools
- Lists tool names only
- No examples or patterns
- Let optimizer learn what documentation helps

## Expected Evolution

Through iterative optimization, we expect the optimizer to discover:

1. **Critical constraints** (e.g., "Use --tail for logs")
2. **Effective workflows** (e.g., platform detection first)
3. **Output formatting** (e.g., XML tags for summaries)
4. **Safety guardrails** (e.g., don't change platforms)

But only if these actually improve the metrics!

## Metrics to Track

- Deployment success rate
- Iteration efficiency (fewer attempts)
- Token usage (cost)
- Health check quality (prevent reward hacking)

## Next Steps

1. Run optimization with seeds as starting point
2. Monitor metric improvements at each iteration
3. Analyze what the optimizer adds vs. what we hardcoded in baseline
4. Compare final performance: seed → optimized vs. baseline → optimized
5. Iterate: If performance is worse, add minimal hints to seeds

## References

- GEPA Paper: "Generalized Prompt Engineering via Automated Optimization"
- Key insight: Simple seed + iterative growth > elaborate starting point
- Example: "Given X, produce Y" → 400+ word structured prompt through optimization
