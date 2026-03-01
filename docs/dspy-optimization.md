# DSPy Prompt Optimization Guide

The Application Operator includes DSPy integration for offline prompt optimization. This allows you to automatically improve deployment success rates and efficiency by learning from historical trajectory data.

## Implementation Status

- ✅ **Phase 1-3**: Data pipeline, metrics, optimizer (COMPLETED)
- ✅ **Phase 4.1**: Field mappings, module loading, PromptLoader extension (COMPLETED)
- ✅ **Phase 4.2**: Runtime integration - agents use DSPy config (COMPLETED)
- ✅ **Phase 4.3**: Trajectory integration for version tracking (COMPLETED)
- ✅ **Phase 4.4**: Optimizer saves actual DSPy modules (COMPLETED)
- ⏳ **Phase 4.5-4.6**: Integration tests, documentation (PENDING)

## Key Features

- **Offline Optimization:** Use DSPy to optimize prompts based on past deployment trajectories
- **Multiple Optimizers:** Support for BootstrapFewShot, BootstrapFewShotWithRandomSearch, MIPROv2, COPRO
- **Comprehensive Metrics:** Track success rates, iteration efficiency, and token costs
- **Version Management:** Auto-versioned optimized prompts with metadata
- **Runtime Integration:** ✅ PromptLoader supports DSPy with automatic fallback to Jinja2
- **Canary Deployment:** ✅ Deterministic hash-based routing (same repo → same version)
- **Trajectory Tracking:** ✅ Records prompt version and fallback events
- **Auto-Rollback:** Automatic rollback on performance degradation (pending Phase 4.4)
- **Online Learning:** Optional feedback collection during production runs (pending Phase 4.4)

## Configuration

Add to `sds.toml`:

```toml
[dspy]
use_optimized = false           # Use DSPy-optimized prompts (default: false)
optimized_version = "latest"    # Version to use (e.g., "v1", "latest")
fallback_to_baseline = true     # Fall back to Jinja2 on errors (default: true)
enable_online_learning = false  # Enable feedback collection (default: false)
feedback_sample_rate = 0.1      # Fraction of runs to collect feedback (0.0-1.0, default: 0.1)
canary_deployment = false       # Enable canary rollout (default: false)
canary_percentage = 0.0         # Percentage for canary (0.0-1.0, default: 0.0)

[dspy.optimization]
optimizer = "BootstrapFewShot"  # DSPy optimizer: BootstrapFewShot, BootstrapFewShotWithRandomSearch, MIPROv2, COPRO
teacher_model = "claude-sonnet-4-5"  # Teacher model for optimization
num_examples = 30               # Number of training examples (positive integer)
validation_split = 0.2          # Validation data fraction (0.0-1.0)

[dspy.optimization.metric_weights]
success = 0.5        # Weight for deployment success (must sum to 1.0)
efficiency = 0.25    # Weight for iteration efficiency
tokens = 0.15        # Weight for token efficiency
health_check = 0.1   # Weight for health check script quality (prevents reward hacking)

[dspy.auto_rollback]
enabled = true                  # Enable automatic rollback on degradation
success_rate_threshold = 0.05   # Rollback if success rate drops by this fraction (0.0-1.0)
evaluation_window = 100         # Number of recent runs to evaluate (positive integer)
```

## Workflow

1. **Run the operator** to generate trajectory data:
   ```bash
   uv run -m app_operator run /path/to/app
   ```

2. **Analyze current performance** to establish baseline:
   ```bash
   uv run -m app_operator analyze-prompts --phase deployment
   ```

3. **Optimize prompts** using DSPy:
   ```bash
   uv run -m app_operator optimize-prompts \
       --prompts deployer_fix_error deployer_summarize \
       --optimizer BootstrapFewShot
   ```

4. **Enable optimized prompts** in `sds.toml`:
   ```toml
   [dspy]
   use_optimized = true
   optimized_version = "latest"
   ```

5. **Test optimized version** and compare:
   ```bash
   uv run -m app_operator run /path/to/app
   uv run -m app_operator analyze-prompts \
       --compare baseline_dir:optimized_dir
   ```

## Rate Limit Handling

When running multiple optimization experiments, you may encounter API rate limits. The system includes built-in rate limit handling:

### Configuration

**Agent-level retry settings** in `sds.toml`:
```toml
[agent]
max_retries = 3              # Maximum retries for API calls (default: 3)
retry_base_delay = 5         # Base delay for exponential backoff in seconds (default: 5)
rate_limit_backoff = 60      # Additional delay when hitting rate limits in seconds (default: 60)
```

**E2E optimization settings** in your E2E config file:
```toml
iterations = 3
prompts = ["deployer_fix_error", "deployer_summarize"]
inter_run_delay = 30         # Delay between consecutive runs in seconds (default: 30)
max_retries = 3              # Max retries per run (default: 3)
rate_limit_backoff = 60      # Rate limit backoff in seconds (default: 60)

[training]
apps = ["/path/to/app1", "/path/to/app2"]

[validation]
apps = ["/path/to/app3"]
```

### How It Works

1. **Automatic Detection:** The system detects rate limit errors (HTTP 429, "Resource exhausted") from all major providers (Gemini, OpenAI, Anthropic)
2. **Exponential Backoff:** Failed requests retry with exponential backoff: 5s, 10s, 20s, etc.
3. **Rate Limit Backoff:** When rate limits are detected, adds extra delay (default 60s) on top of exponential backoff
4. **Inter-run Delays:** Adds configurable delay between consecutive operator runs to prevent quota exhaustion
5. **Informative Logging:** Clear logs indicate when rate limits are hit and how long the system is waiting

### Best Practices

1. **Start conservatively:** Use `inter_run_delay = 60` for initial runs to avoid rate limits
2. **Monitor your quota:** Check your provider's quota usage dashboard
3. **Reduce delay if successful:** If no rate limits encountered, reduce `inter_run_delay` to 30s or lower
4. **Use appropriate models:** Teacher models (for optimization) have different rate limits than student models
5. **Spread experiments over time:** For large experiments, consider splitting into multiple sessions

### Example: Handling Rate Limit Failure

If you see a failure like in `hotelReservation_iter3_train`:
```
Script generation failed: gemini exited with code 1
...
ApiError: {"error":{"code": 429, "message": "Resource exhausted"...}}
```

**Solution:**
1. Increase `inter_run_delay` in your config (e.g., from 30 to 60 seconds)
2. Wait for quota to refresh (usually a few minutes)
3. Resume the experiment - it will skip completed runs and retry failed ones

## Available Prompts

The DSPy integration provides signatures for 10 prompts across different agent types:

**Deployer Prompts (4):**
- `deployer_system` - System instructions for deployment agent
- `deployer_generate_script` - Generate deploy.sh and health_check.sh
- `deployer_fix_error` - Fix deployment errors (most critical for optimization)
- `deployer_summarize` - Summarize deployment results

**Code Analyzer Prompts (2):**
- `code_analyzer_system` - System instructions for code analysis
- `code_analyzer_user` - Analyze codebase for deployment

**Monitor Prompts (1):**
- `monitor_analyze_health` - Analyze application health

**Agentflow Prompts (3):**
- `agentflow_system` - System instructions for script generation
- `agentflow_user` - Generate Python script from user request
- `agentflow_repair` - Repair malformed JSON responses

## Metrics

The optimization process uses a composite metric combining four factors:

1. **Deployment Success (50% weight):** Binary metric for successful deployment
2. **Iteration Efficiency (25% weight):** Rewards fewer iterations to success
3. **Token Efficiency (15% weight):** Rewards lower token usage
4. **Health Check Quality (10% weight):** Validates health check scripts are non-trivial to prevent reward hacking

The **Health Check Quality metric** prevents "reward hacking" by ensuring generated `health_check.sh` scripts actually perform meaningful checks rather than trivial always-passing scripts (e.g., `#!/bin/bash\nexit 0`). It evaluates:
- Script complexity (minimum 20 lines of actual code)
- Presence of real check commands (curl, nc, docker, redis-cli, mongo, etc.)
- Diversity of check types (ports, endpoints, containers, databases)

Metric weights are configurable in `sds.toml` under `[dspy.optimization.metric_weights]`.

## Data Pipeline

```
Trajectory Files (.sds/trajectories/*.json)
    ↓
TrajectoryDataLoader → Extract examples by phase
    ↓
MetricsAggregator → Compute statistics
    ↓
PromptOptimizer → Run DSPy optimization
    ↓
Save versioned prompts (app_operator/prompts/optimized/vN/)
```

## Runtime Architecture

### PromptLoader Rendering Flow

```
agent calls render(template_name, **kwargs)
    ↓
Check: use_optimized? canary routing?
    ↓
├─→ YES: _render_dspy()
│     ├─→ Load optimized module (cached)
│     ├─→ Map kwargs → DSPy signature fields
│     ├─→ Invoke DSPy module
│     ├─→ Extract output field
│     ├─→ On Error: Fallback to Jinja2
│     └─→ Record version in trajectory
│
└─→ NO: _render_jinja2()
      └─→ Record version in trajectory
```

### Key Components

- **Field Mappings** (`field_mappings.py`): Maps Jinja2 kwargs to DSPy InputFields with auto + explicit mappings
- **Module Loader** (`loader.py`): Loads and caches DSPy modules with version resolution ("latest" → vN)
- **PromptLoader** (`prompts/__init__.py`): Extended to support both Jinja2 and DSPy rendering
- **Trajectory** (`trajectory.py`): Records `prompt_version` and `fallback_occurred` for each conversation

### Canary Deployment

- Deterministic routing: `hash(repo_path) % 100 / 100.0 < canary_percentage`
- Same repo always gets same version (predictable debugging)
- Configurable percentage in `sds.toml`

### Fallback Strategy

- Transparent fallback on any DSPy error (missing file, corrupted JSON, invocation error)
- Logged with context for debugging
- Recorded in trajectory for analysis

### Runtime Integration

- **CLI Agent Runtime**: All agents (DeploymentAgent, AppMonitor, CodeAnalyzerAgent) accept and use `dspy_config`
- **LangGraph Runtime**: Loader initialized with `config.dspy` in `build_graph()`
- **Configuration Flow**: `sds.toml` → `Config.dspy` → Agent constructors → `get_loader(dspy_config)`
- **Prompt Helpers**: All prompt functions (deployer, monitor, code_analyzer) pass `dspy_config` to loader
- **Defensive Programming**: Error handling for mock configs in tests (canary_percentage validation)

## Module Structure

**DSPy Integration** (`app_operator/dspy_integration/`):
- **Configuration (`config.py`):** DSPy optimization settings, auto-rollback config, metric weights
- **Data Loader (`data_loader.py`):** Load training examples from trajectory files with phase filtering
- **Metrics (`metrics.py`):** DeploymentSuccessMetric, IterationEfficiencyMetric, TokenEfficiencyMetric, CompositeMetric
- **Metrics Aggregator (`metrics_aggregator.py`):** Compute statistics and compare optimization versions
- **Optimizer (`optimizer.py`):** Orchestrate DSPy optimization workflow with multiple optimizer support
- **Signatures (`signatures.py`):** DSPy signatures for all 10 SDS prompts (deployer, monitor, agentflow)
- **Cost Calculation (`cost.py`):** Token cost calculation for Claude, GPT, and Gemini models
- **Feedback (`feedback.py`):** Online learning feedback collection
- **Monitor (`monitor.py`):** Performance monitoring for auto-rollback

## Usage Tips

- Use `analyze-prompts` to establish baseline metrics
- Run `optimize-prompts` with `--dry-run` first to validate inputs
- Compare results using `analyze-prompts --compare`
- When adding DSPy signatures: Add to `signatures.py` and register in `SIGNATURES` dict
- When extending metrics: Modify `metrics.py` and ensure weights sum to 1.0
