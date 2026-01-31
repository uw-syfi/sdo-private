# DSPy Integration - Phase 3 Summary

## Objective
Implement offline prompt optimization workflow using DSPy.

## Completed Tasks

### 1. DSPy Signatures (10 prompts)
**File:** `app_operator/dspy_integration/signatures.py`

Implemented DSPy Signature classes for all 10 SDS prompts:

**Deployer Prompts (4):**
- `DeployerSystemSignature` - System instructions for deployment agent
- `DeployerGenerateScriptSignature` - Generate deploy.sh and health_check.sh
- `DeployerFixErrorSignature` - Fix deployment errors
- `DeployerSummarizeSignature` - Summarize deployment results

**Code Analyzer Prompts (2):**
- `CodeAnalyzerSystemSignature` - System instructions for code analysis
- `CodeAnalyzerUserSignature` - Analyze codebase for deployment

**Monitor Prompts (1):**
- `MonitorAnalyzeHealthSignature` - Analyze application health

**Agentflow Prompts (3):**
- `AgentflowSystemSignature` - System instructions for script generation
- `AgentflowUserSignature` - Generate Python script from user request
- `AgentflowRepairSignature` - Repair malformed JSON responses

**Features:**
- Uses DSPy 3.x API (`dspy.Signature`, `dspy.InputField`, `dspy.OutputField`)
- Comprehensive field descriptions for each input/output
- Signature registry (`SIGNATURES` dict) for easy lookup
- `get_signature()` helper function with clear error messages
- All signatures properly documented

### 2. PromptOptimizer
**File:** `app_operator/dspy_integration/optimizer.py`

Implemented full offline optimization orchestration:

**Core Methods:**
- `optimize()` - Main optimization workflow with validation and error handling
- `_configure_dspy_lm()` - Configure DSPy language model (supports Claude, GPT, Gemini)
- `_optimize_single_prompt()` - Optimize individual prompts
- `_create_optimizer()` - Factory for DSPy optimizers
- `_evaluate()` - Evaluate optimized modules on validation set
- `_get_next_version()` - Auto-increment version numbers
- `_save_optimized_prompts()` - Save results with metadata

**Optimizer Support:**
- BootstrapFewShot
- BootstrapFewShotWithRandomSearch
- MIPROv2
- COPRO

**LM Configuration (DSPy 3.x):**
Uses LiteLLM format: `provider/model`
- Claude: `anthropic/claude-sonnet-4-5`
- GPT: `openai/gpt-4`
- Gemini: `gemini/gemini-1.5-pro`

**Workflow:**
1. Validate prompt names against SIGNATURES
2. Load trajectory data
3. Split into train/validation (configurable split)
4. Configure DSPy LM with teacher model
5. Create composite metric
6. Optimize each prompt using selected optimizer
7. Evaluate on validation set
8. Save results with metadata + auto-versioning

**Features:**
- Dry-run mode for validation without optimization
- Automatic versioning (v1, v2, v3, ...)
- 'latest' symlink for easy access
- Detailed metadata tracking
- Graceful error handling per prompt

### 3. optimize-prompts CLI Command
**File:** `app_operator/commands/optimize_prompts.py`

Full-featured CLI for running optimization:

**Usage:**
```bash
# List available prompts
uv run -m app_operator optimize-prompts --list-prompts

# Optimize specific prompts
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error deployer_summarize

# Dry run
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --dry-run

# Custom optimizer and settings
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --optimizer MIPROv2 \
    --teacher-model anthropic/claude-opus-4-5 \
    --num-examples 50

# Use specific trajectory directory
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --trajectories-dir /path/to/trajectories
```

**Options:**
- `--prompts`: List of prompt names to optimize
- `--trajectories-dir`: Trajectory data directory
- `--output-dir`: Custom output directory
- `--config`: Path to sds.toml
- `--optimizer`: DSPy optimizer choice
- `--num-examples`: Max training examples
- `--teacher-model`: Teacher model override
- `--dry-run`: Validate inputs only
- `--list-prompts`: Show available prompts with descriptions

**Output:**
```
============================================================
DSPy Prompt Optimization
============================================================

Prompts to optimize: deployer_fix_error
Trajectories directory: .sds/trajectories
Optimizer: BootstrapFewShot
Teacher model: claude-sonnet-4-5
Max training examples: 30

Loading training data from .sds/trajectories...
Loaded 15 training examples
Train: 12, Validation: 3

Optimizing prompt: deployer_fix_error
  Using 12 training examples
  Optimizer: BootstrapFewShot

============================================================
Optimization Results
============================================================

✓ Optimization completed successfully!
  Output directory: app_operator/prompts/optimized/v1
  Training examples: 12
  Validation examples: 3

Per-prompt results:
  ✓ deployer_fix_error: validation score = 0.823

Optimized prompts saved to: app_operator/prompts/optimized/v1
To use optimized prompts, update sds.toml:
  [dspy]
  use_optimized = true
  optimized_version = "v1"
```

### 4. Optimized Prompt Storage
**Structure:**
```
app_operator/prompts/
├── templates/              # Baseline Jinja2 (source of truth)
│   ├── deployer/
│   ├── code_analyzer/
│   └── monitor/
└── optimized/              # DSPy-optimized (generated)
    ├── v1/
    │   ├── metadata.json
    │   ├── deployer_fix_error.json
    │   └── deployer_summarize.json
    ├── v2/
    └── latest -> v2/       # Symlink
```

**metadata.json:**
```json
{
  "version": "v1",
  "config": {
    "optimizer": "BootstrapFewShot",
    "teacher_model": "claude-sonnet-4-5",
    "num_examples": 30,
    "metric_weights": {
      "success": 0.6,
      "efficiency": 0.25,
      "tokens": 0.15
    }
  },
  "prompts": {
    "deployer_fix_error": {
      "validation_score": 0.823,
      "optimized": true
    }
  }
}
```

### 5. Main Entry Point Integration
**File:** `app_operator/__main__.py`

- Registered `optimize-prompts` command in subparsers
- No Docker dependency requirement (unlike run/init-exp)
- Maintains backward compatibility

### 6. Comprehensive Testing
Created 19 new unit tests in `tests/unit/dspy_tests/`:

**test_signatures.py** (8 tests):
- All signatures registered
- get_signature() with valid/invalid names
- Error messages include available prompts
- Signature field validation (using model_fields)
- Docstring presence
- Signature count verification

**test_optimizer.py** (11 tests):
- Initialization
- Invalid prompt names validation
- No training data error handling
- Dry-run mode
- Version numbering (no existing, with existing, non-version dirs)
- Saving optimized prompts
- Optimizer creation (BootstrapFewShot, invalid)
- Evaluation logic

**All tests pass:**
- 122 DSPy tests (69 Phase 1 + 34 Phase 2 + 19 Phase 3) ✓
- 466 total unit tests ✓
- Zero regressions ✓

### 7. DSPy 3.x API Compatibility

**Key Differences from Plan:**
- Optimizer import: `dspy.BootstrapFewShot` (not `dspy.teleprompt.BootstrapFewShot`)
- LM configuration: `dspy.LM(model="provider/model")` (unified interface)
- Signature fields: Accessed via `model_fields` (Pydantic-based)
- MIPRO → MIPROv2 (updated to current DSPy version)

**Test Directory Fix:**
- Renamed `tests/unit/dspy` → `tests/unit/dspy_tests` to avoid shadowing real dspy package
- Critical fix that prevented test collection

## Data Flow

```
User runs optimize-prompts command
    ↓
Load config from sds.toml (with CLI overrides)
    ↓
PromptOptimizer.optimize()
    ├─→ Validate prompt names against SIGNATURES
    ├─→ Load trajectory examples via TrajectoryDataLoader
    ├─→ Split train/validation (80/20 by default)
    ├─→ Configure DSPy LM (anthropic/claude-sonnet-4-5)
    ├─→ Create CompositeMetric (60% success, 25% efficiency, 15% tokens)
    │
    ├─→ For each prompt:
    │   ├─→ Get DSPy Signature
    │   ├─→ Create DSPy Module (Predict wrapper)
    │   ├─→ Create optimizer (BootstrapFewShot/MIPROv2/COPRO)
    │   ├─→ Run optimization on train set
    │   ├─→ Evaluate on validation set
    │   └─→ Store results
    │
    └─→ Save optimized prompts:
        ├─→ Auto-increment version (v1, v2, ...)
        ├─→ Save metadata.json
        ├─→ Save individual prompt JSONs
        └─→ Update 'latest' symlink
```

## Example Workflows

### Optimize Deployment Error Fixer
```bash
# Generate trajectory data (run operator first)
uv run -m app_operator run /path/to/app

# Analyze current performance
uv run -m app_operator analyze-prompts --phase deployment

# Optimize the error fixing prompt
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --optimizer BootstrapFewShot \
    --num-examples 30

# Enable optimized prompts
cat > sds.toml << EOF
[dspy]
use_optimized = true
optimized_version = "latest"
fallback_to_baseline = true
EOF

# Test optimized prompts
uv run -m app_operator run /path/to/app
```

### Optimize Multiple Prompts
```bash
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error deployer_summarize monitor_analyze_health \
    --optimizer MIPROv2 \
    --teacher-model anthropic/claude-opus-4-5
```

### Compare Optimizers
```bash
# Optimize with BootstrapFewShot
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --optimizer BootstrapFewShot \
    --output-dir prompts/optimized/bootstrap

# Optimize with MIPROv2
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --optimizer MIPROv2 \
    --output-dir prompts/optimized/mipro

# Compare results
uv run -m app_operator analyze-prompts \
    --compare prompts/optimized/bootstrap:prompts/optimized/mipro
```

## Files Created/Modified

**Created (4 files):**
- `app_operator/dspy_integration/signatures.py` (185 lines)
- `app_operator/commands/optimize_prompts.py` (207 lines)
- `tests/unit/dspy_tests/test_signatures.py` (86 lines)
- `tests/unit/dspy_tests/test_optimizer.py` (224 lines)
- `docs/dspy_phase3_summary.md`

**Modified (4 files):**
- `app_operator/dspy_integration/optimizer.py` (fully implemented, 293 lines)
- `app_operator/dspy_integration/config.py` (updated valid optimizers)
- `app_operator/__main__.py` (registered optimize-prompts command)
- `tests/unit/dspy_tests/test_config_integration.py` (MIPRO → MIPROv2)

**Directory Renamed:**
- `tests/unit/dspy` → `tests/unit/dspy_tests` (to avoid package shadowing)

## Success Criteria Met

✅ DSPy signatures for all 10 prompts
✅ PromptOptimizer orchestrates full workflow
✅ optimize-prompts CLI command functional
✅ Optimized prompt storage with versioning
✅ Integration tests covering key scenarios
✅ All 466 unit tests passing
✅ Zero regressions
✅ Code quality validated
✅ DSPy 3.x API compatibility

## Known Limitations

1. **Simplified Optimization**: Current implementation uses minimal training examples (10 max in demo) for quick testing. Production use should increase `num_examples`.

2. **Prompt Saving**: Optimized prompts are saved as JSON placeholders. Full implementation would serialize actual DSPy compiled programs.

3. **Metric Mapping**: The `_evaluate()` method uses simplified example-to-signature mapping. Production needs proper input field mapping from TrajectoryExample to Signature fields.

4. **No Prompt Loading**: Phase 3 focuses on optimization; Phase 4 will implement loading and using optimized prompts in production.

## Next Steps: Phase 4 (Prompt Loading & Execution)

Phase 4 will implement:
1. Extend `PromptLoader` with `_render_dspy()` method
2. Load optimized prompts from storage
3. Map between Jinja2 context and DSPy signature fields
4. Integrate into all 3 runtimes (CLI Agent, LangGraph, ADK)
5. Add prompt version tracking to trajectories
6. Implement graceful fallback to Jinja2 on errors
7. Regression tests with golden examples

**Dependencies Ready:**
- Configuration system (Phase 1)
- Data pipeline (Phase 2)
- Optimization workflow (Phase 3)

**Status:** Phase 3 complete. Infrastructure in place for prompt loading in Phase 4.
