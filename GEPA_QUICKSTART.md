# GEPA-Style Optimization Quick Start

Get started with GEPA-style prompt optimization in 5 minutes.

## What is GEPA?

GEPA (Generalized Prompt Engineering via Automated Optimization) is an approach that:
- Starts with **minimal "seed" prompts** (< 10 lines)
- Lets the **optimizer discover** what details help performance
- Avoids **local minima** from overly prescriptive starting prompts

**Example from GEPA paper:**
```
Seed:     "Given question and summary 1, produce query."
Optimized: 400+ word structured prompt with 6 sections
```

All the structure emerged from learning, not human design.

## Quick Start

### 1. One-Line Setup

```bash
# The seed templates are already created in app_operator/prompts/templates/seeds/
ls app_operator/prompts/templates/seeds/
```

### 2. Run Your First GEPA Optimization

```bash
# Optimize deployer_fix_error from minimal seed
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --use-seeds \
    --optimizer COPRO
```

**Expected output:**
```
Starting from: GEPA-style seeds
Optimizer: COPRO
...
✓ Optimization completed successfully!
  Output directory: app_operator/prompts/optimized/v8
```

### 3. Compare Results

```bash
# Check what the optimizer discovered
cat app_operator/prompts/optimized/v8/deployer_fix_error.dspy.json | jq -r '.optimized_instruction'

# Compare with baseline
diff \
  <(cat app_operator/prompts/templates/deployer/fix_error.jinja2) \
  <(cat app_operator/prompts/templates/seeds/deployer_fix_error.jinja2)
```

### 4. Test the Optimized Prompt

```bash
# Update sds.toml to use optimized prompts
[dspy]
use_optimized = true
optimized_version = "v8"

# Run deployment with optimized prompts
uv run -m app_operator run /path/to/app
```

### 5. Evaluate Performance

```bash
# Analyze metrics
uv run -m app_operator analyze-prompts --phase deployment

# Compare with baseline
uv run -m app_operator analyze-prompts \
    --compare baseline_dir:optimized_dir
```

## Comparison: Baseline vs. Seed

**deployer_fix_error:**

| Version | Lines | Key Features |
|---------|-------|--------------|
| Baseline | 112 | Platform detection, DO's/DON'Ts, 7-step workflow, context limits |
| Seed | 7 | Just task description + inputs |
| Reduction | **94%** | Let optimizer discover the rest |

## What to Expect

**First run:**
- ⏱️ Takes 5-15 minutes (depending on num_examples)
- 📊 May perform 5-15% worse than baseline-optimized
- 🔍 Discover which constraints the optimizer learned

**After refinement:**
- ✅ Should match baseline performance
- 💡 May discover new strategies
- 📉 Often uses fewer tokens

## Common Commands

```bash
# List available prompts
uv run -m app_operator optimize-prompts --list-prompts

# Optimize single prompt from seeds
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --use-seeds

# Optimize multiple prompts from seeds
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error deployer_generate_script \
    --use-seeds \
    --optimizer MIPROv2

# Dry run (validate without optimizing)
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --use-seeds \
    --dry-run

# Use custom config
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --use-seeds \
    --config path/to/sds.toml
```

## Troubleshooting

**"No training examples found"**
```bash
# Generate training data first
uv run -m app_operator run /path/to/app
ls .sds/trajectories/  # Should see *.json files
```

**"Optimization failed"**
```bash
# Check DSPy config in sds.toml
[dspy.optimization]
teacher_model = "vertex_ai/gemini-2.5-pro"  # Must be valid model
num_examples = 30  # Must have enough examples

# Verify API credentials
echo $GOOGLE_APPLICATION_CREDENTIALS  # For Vertex AI
```

**"Results worse than baseline"**
- This is expected on first run
- Add minimal hints to seeds and re-optimize
- Try different optimizer (COPRO vs MIPROv2)
- See GEPA_TUTORIAL.md for iterative refinement

## Next Steps

1. **Read the tutorial**: `GEPA_TUTORIAL.md` for detailed walkthrough
2. **Compare approaches**: `app_operator/prompts/templates/seeds/COMPARISON.md`
3. **Review seed prompts**: `app_operator/prompts/templates/seeds/*.jinja2`
4. **Understand the implementation**: `GEPA_SEED_PROMPTS.md` for technical details

## Key Files

```
app_operator/
├── prompts/
│   ├── templates/
│   │   ├── deployer/        # Baseline templates (112 lines)
│   │   │   └── fix_error.jinja2
│   │   └── seeds/           # GEPA-style seeds (7 lines)
│   │       ├── deployer_fix_error.jinja2
│   │       ├── COMPARISON.md
│   │       └── README.md
│   └── optimized/
│       ├── v7/              # Baseline → optimized
│       └── v8/              # Seed → optimized (GEPA)
└── dspy_integration/
    └── optimizer.py         # Supports --use-seeds flag
```

## Theory

**Why does GEPA work?**

1. **Avoids local minima**: Prescriptive prompts constrain the optimizer
2. **Discovers effective patterns**: Learns from data what actually helps
3. **Generalizes better**: Simpler prompts may be less overfit

**When to use GEPA vs. baseline?**

| Scenario | Use GEPA | Use Baseline |
|----------|----------|--------------|
| First optimization | ✅ Yes | Maybe |
| Stuck in local minimum | ✅ Yes | No |
| Have domain expertise | Maybe | ✅ Yes |
| Limited training data | No | ✅ Yes |
| Want interpretability | ✅ Yes | Maybe |

## References

- **Full Tutorial**: `GEPA_TUTORIAL.md`
- **Technical Details**: `GEPA_SEED_PROMPTS.md`
- **Comparison**: `app_operator/prompts/templates/seeds/COMPARISON.md`
- **DSPy Integration**: `app_operator/dspy_integration/README.md`
