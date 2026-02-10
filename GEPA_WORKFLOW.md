# GEPA Workflow Visualization

This document provides visual diagrams of the GEPA optimization workflow.

## High-Level Comparison

```
Traditional Approach (Baseline → Optimized)
══════════════════════════════════════════════

┌─────────────────────────────────────────────┐
│   Detailed Baseline Prompt (112 lines)     │
│   • Platform detection rules                │
│   • DO's and DON'Ts lists                   │
│   • 7-step diagnostic workflow              │
│   • Context size constraints                │
│   • Output format requirements              │
└─────────────────────────────────────────────┘
                    ↓
         ┌──────────────────────┐
         │  DSPy Optimizer      │
         │  (COPRO/MIPROv2)     │
         └──────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│   Optimized Prompt (115 lines)             │
│   • Refined wording                         │
│   • Reordered sections                      │
│   • Similar structure                       │
│   • Still in local minimum?                 │
└─────────────────────────────────────────────┘

⚠️ RISK: Optimizer refines within existing structure
❌ LIMITED: Can't explore fundamentally different approaches


GEPA Approach (Seed → Optimized)
════════════════════════════════

┌─────────────────────────────────────────────┐
│   Minimal Seed Prompt (7 lines)            │
│   • Task: Fix deployment error              │
│   • Inputs: error_context, scripts          │
│   • Output: Identify root cause and fix     │
└─────────────────────────────────────────────┘
                    ↓
         ┌──────────────────────┐
         │  DSPy Optimizer      │
         │  (COPRO/MIPROv2)     │
         │  • Tries candidates  │
         │  • Evaluates metrics │
         │  • Adds what helps   │
         └──────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│   Discovered Optimized Prompt (50 lines)   │
│   ✓ Platform detection (learned)            │
│   ✓ Log reading strategy (learned)          │
│   ✓ Output format (learned)                 │
│   ? Novel diagnostic approach (discovered!) │
└─────────────────────────────────────────────┘

✅ OPPORTUNITY: Optimizer explores new solution space
💡 DISCOVERY: May find strategies we didn't think of
```

## Optimization Loop (COPRO)

```
COPRO: Collaborative Prompt Optimization
════════════════════════════════════════

Round 1: Initial Candidates
─────────────────────────────
Seed: "Fix deployment error. Error: {{error_context}}"
                    ↓
    ┌───────────────┴───────────────┐
    │   Generate 5 candidates       │
    │   (slightly different styles)  │
    └───────────────┬───────────────┘
                    ↓
        Candidate 1: "Analyze and fix..."
        Candidate 2: "Debug the deployment..."
        Candidate 3: "Identify root cause..."
        Candidate 4: "Diagnose the error..."
        Candidate 5: "Troubleshoot..."
                    ↓
    ┌───────────────┴───────────────┐
    │   Evaluate on training set    │
    │   Metric: Success + Efficiency│
    └───────────────┬───────────────┘
                    ↓
        Best: Candidate 3 (Score: 0.72)


Round 2: Refinement
─────────────────────
Best from Round 1: "Identify root cause..."
                    ↓
    ┌───────────────┴───────────────┐
    │   Add details that helped:    │
    │   • "Check platform first"    │
    │   • "Use --tail for logs"     │
    │   • "Stop after root cause"   │
    └───────────────┬───────────────┘
                    ↓
        Candidate 1: Base + platform check
        Candidate 2: Base + log reading
        Candidate 3: Base + both strategies
        Candidate 4: Base + stop condition
        Candidate 5: Base + all three
                    ↓
    ┌───────────────┴───────────────┐
    │   Evaluate again              │
    └───────────────┬───────────────┘
                    ↓
        Best: Candidate 5 (Score: 0.85)


Round 3-5: Continued Refinement
─────────────────────────────────
Keep adding details that improve metrics
Until convergence or max_rounds reached

Final Output: Multi-paragraph structured prompt
```

## Metrics Flow

```
Example Evaluation for deployer_fix_error
═══════════════════════════════════════════

┌─────────────────────────────────────────┐
│  Training Example                       │
│  • error_context: "Port 3000 in use"    │
│  • Ground truth: "Change to port 3001"  │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│  Invoke DSPy Module with Candidate      │
│  Module(error_context="Port 3000...")   │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│  Agent Execution                        │
│  • Read deployment script               │
│  • Identify port conflict               │
│  • Edit script to use port 3001         │
│  • Result: Success in 2 iterations      │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│  Compute Metrics                        │
│  ✓ Success: 1.0 (deployed successfully) │
│  ✓ Efficiency: 0.9 (2 iters, good!)     │
│  ✓ Tokens: 0.8 (used 2000 tokens)       │
│  ✓ Health: 1.0 (non-trivial check)      │
│                                          │
│  Composite: 0.5*1.0 + 0.25*0.9          │
│           + 0.15*0.8 + 0.1*1.0           │
│           = 0.945                        │
└─────────────────────────────────────────┘
              ↓
     Return score to optimizer
```

## Data Flow

```
End-to-End Data Flow
════════════════════

1. Generate Training Data
──────────────────────────
┌────────────┐    ┌────────────┐    ┌────────────┐
│ Run App    │ → │ Deploy &   │ → │ Save       │
│ Operator   │    │ Fix Errors │    │ Trajectory │
└────────────┘    └────────────┘    └────────────┘
                                          ↓
                              .sds/trajectories/run_*.json


2. Load Training Data
──────────────────────
┌────────────────────────────────────┐
│ TrajectoryDataLoader               │
│ • Load .sds/trajectories/*.json    │
│ • Filter by phase (deployment)     │
│ • Extract prompt_kwargs            │
│ • Extract rendered_prompt          │
│ • Extract success/iterations       │
└────────────────────────────────────┘
              ↓
   List[TrajectoryExample]
        • prompt_kwargs: {...}
        • rendered_prompt: "Fix..."
        • success: True
        • iterations: 3


3. Convert to DSPy Format
──────────────────────────
┌────────────────────────────────────┐
│ PromptOptimizer                    │
│ • Map kwargs → signature fields    │
│ • Use seed template if --use-seeds │
│ • Create dspy.Example objects      │
└────────────────────────────────────┘
              ↓
   List[dspy.Example]
        • error_context: "..."  (input)
        • attempt: 2            (input)
        • rendered_prompt: "..." (output)


4. Optimize Prompts
───────────────────
┌────────────────────────────────────┐
│ DSPy Optimizer (COPRO)             │
│ • Generate candidates              │
│ • Evaluate with CompositeMetric    │
│ • Refine best candidates           │
│ • Iterate until convergence        │
└────────────────────────────────────┘
              ↓
   Optimized dspy.Predict module


5. Save Optimized Prompts
──────────────────────────
┌────────────────────────────────────┐
│ PromptOptimizer._save_optimized    │
│ • Save module.dspy.json            │
│ • Save metadata.json               │
│ • Auto-version (v1, v2, v3...)     │
└────────────────────────────────────┘
              ↓
   app_operator/prompts/optimized/v8/
        • deployer_fix_error.dspy.json
        • metadata.json


6. Use in Production
────────────────────
┌────────────────────────────────────┐
│ PromptLoader                       │
│ • Check use_optimized = true       │
│ • Load optimized module            │
│ • Invoke module(**kwargs)          │
│ • Fallback to Jinja2 on error      │
└────────────────────────────────────┘
              ↓
   Rendered prompt sent to coding agent
```

## Iterative Refinement Workflow

```
GEPA Iterative Refinement
═══════════════════════════

Iteration 1: Pure Seeds
────────────────────────
Seed (7 lines) → Optimize → Optimized (40 lines) → Test
                                                       ↓
                                          Metrics: 85% success
                                          (Baseline: 92% success)
                                                       ↓
                                          Gap: -7% (too big)


Iteration 2: Seeds + Critical Hint
────────────────────────────────────
Seed + "Use --tail for logs" → Optimize → Optimized (45 lines) → Test
                                                                     ↓
                                                      Metrics: 90% success
                                                      (Baseline: 92%)
                                                                     ↓
                                                      Gap: -2% (better!)


Iteration 3: Seeds + Platform Hint
────────────────────────────────────
Seed + logs + "Detect platform" → Optimize → Optimized (50 lines) → Test
                                                                        ↓
                                                        Metrics: 93% success
                                                        (Baseline: 92%)
                                                                        ↓
                                                        Gap: +1% (SUCCESS!)
                                                                        ↓
                        ┌───────────────────────────────────────────────┘
                        │
                        ↓
        ┌───────────────────────────────────┐
        │  Analyze What Was Discovered:     │
        │  • Platform detection strategy    │
        │  • Novel error categorization     │
        │  • Adaptive fix selection logic   │
        │  → Update seeds for next project  │
        └───────────────────────────────────┘
```

## File Structure After GEPA

```
app_operator/prompts/
├── templates/
│   ├── deployer/
│   │   ├── fix_error.jinja2        # Baseline (112 lines)
│   │   ├── generate_script.jinja2  # Baseline (80 lines)
│   │   └── summarize.jinja2        # Baseline (30 lines)
│   │
│   ├── seeds/                      # ⭐ NEW: GEPA-style seeds
│   │   ├── README.md
│   │   ├── COMPARISON.md
│   │   ├── deployer_fix_error.jinja2        # Seed (7 lines)
│   │   ├── deployer_generate_script.jinja2  # Seed (7 lines)
│   │   ├── deployer_summarize.jinja2        # Seed (5 lines)
│   │   └── ... (7 more seeds)
│   │
│   ├── code_analyzer/
│   │   ├── system.jinja2           # Baseline
│   │   └── user.jinja2             # Baseline
│   │
│   └── monitor/
│       └── analyze_health.jinja2   # Baseline
│
├── optimized/
│   ├── v1-v6/                      # Previous optimizations
│   ├── v7/                         # Baseline → Optimized (COPRO)
│   │   ├── deployer_fix_error.dspy.json
│   │   └── metadata.json
│   │
│   └── v8/                         # ⭐ Seed → Optimized (GEPA)
│       ├── deployer_fix_error.dspy.json
│       └── metadata.json
│
└── __init__.py                     # PromptLoader

GEPA Documentation:
├── GEPA_QUICKSTART.md              # 5-minute start
├── GEPA_TUTORIAL.md                # Detailed walkthrough
├── GEPA_SEED_PROMPTS.md            # Proposal & design
└── GEPA_WORKFLOW.md                # This file
```

## Success Criteria Visualization

```
GEPA Success Metrics
════════════════════

        Success Rate
        ────────────
Baseline    ████████████████ 92%
Seed v1     ███████████████  85%  ⚠️ Too low
Seed v2     █████████████████ 90%  📈 Improving
Seed v3     ██████████████████ 93% ✅ SUCCESS!

        Iteration Efficiency
        ────────────────────
Baseline    ███████████████ 3.2 avg
Seed v1     ████████████████ 3.5 avg  ⚠️ Worse
Seed v2     ██████████████  3.1 avg  📈 Better
Seed v3     █████████████   2.8 avg  ✅ BEST!

        Token Usage
        ───────────
Baseline    ████████████████ 8500 tokens
Seed v1     ██████████      5200 tokens  ✅ Lower!
Seed v2     ████████████    6100 tokens  📈 Still lower
Seed v3     █████████████   6800 tokens  ✅ 20% savings

        Overall Composite Score
        ───────────────────────
Baseline    ████████████████ 0.875
Seed v1     ██████████████  0.802  ⚠️ Lower
Seed v2     ████████████████ 0.860  📈 Close
Seed v3     █████████████████ 0.910 ✅ BETTER!

Legend:
⚠️  = Needs improvement
📈  = Making progress
✅  = Success!
```

## When to Use GEPA vs. Baseline

```
Decision Tree
═════════════

Start
  │
  ├─ Do you have strong domain expertise?
  │  ├─ Yes → Start with baseline, optimize later
  │  └─ No  → Continue ↓
  │
  ├─ Are you stuck in local minimum?
  │  ├─ Yes → ✅ Use GEPA
  │  └─ No  → Continue ↓
  │
  ├─ Do you have sufficient training data (>30 examples)?
  │  ├─ Yes → Continue ↓
  │  └─ No  → Start with baseline
  │
  ├─ Is interpretability important?
  │  ├─ Yes → ✅ Use GEPA (simpler prompts)
  │  └─ No  → Either works
  │
  └─ Want to discover novel strategies?
     ├─ Yes → ✅ Use GEPA
     └─ No  → Baseline is fine
```

---

This workflow visualization shows the complete GEPA integration in SDS. Use `GEPA_QUICKSTART.md` to get started in 5 minutes!
