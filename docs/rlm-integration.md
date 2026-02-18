# RLM Integration with SDS

**Recursive Language Models (RLMs) for Long-Context Deployment Debugging**

Based on: Zhang et al., "Recursive Language Models" (MIT CSAIL, 2025)

## Overview

RLMs treat prompts as **part of the environment** rather than direct LLM input. This enables:

1. **Handling contexts beyond model token limits** (10M+ tokens)
2. **Programmatic exploration** via code execution
3. **Recursive decomposition** of complex tasks
4. **Cost-efficient processing** (50%+ token savings)

## Why RLMs for SDS?

SDS deployment debugging involves:

- **Long error logs** (50K+ characters per failed attempt)
- **Iteration history** (30+ previous attempts with full context)
- **Multi-file context** (Dockerfile, docker-compose, README, code analysis)
- **Pattern recognition** across repetitive failures

**Problem**: Traditional approach sends entire context to LLM
- Exceeds 8K context window for GPT-4
- High token costs (10K+ tokens per call)
- LLM struggles with repetitive logs
- Important errors buried in noise

**Solution**: RLM stores context in REPL, LLM queries it programmatically
- Tokens sent: Only filtered results (~2K tokens)
- Token savings: 50-80% reduction
- Better accuracy: Code-based pattern extraction
- Scalable: Can handle 10M+ token contexts

## Architecture

### Core Components

```
app_operator/rlm/
├── environment.py          # REPL wrapper for context storage
│   ├── RLMEnvironment     # Main RLM environment
│   ├── RLMContext         # Context dataclass
│   └── RLMCall            # Record of RLM actions
├── recursive_agent.py      # RLM-enabled deployment agent
│   └── RecursiveDeploymentAgent
├── metrics.py              # DSPy metrics for RLM
│   ├── RLMEfficiencyMetric
│   ├── RLMContextUtilizationMetric
│   └── RLMCompositeMetric
└── example_demo.py         # POC demonstration
```

### Integration Points

**1. Trajectory System** (`trajectory.py`)
- Already captures all needed context
- Add RLM-specific fields:
  ```python
  {
    "rlm_calls": [
      {
        "action_type": "execute_code",
        "depth": 0,
        "code": "result = re.findall(...)",
        "output": "...",
        "tokens_saved": 15000
      }
    ],
    "rlm_statistics": {
      "total_calls": 5,
      "code_executions": 3,
      "recursive_calls": 2,
      "total_tokens_saved": 30000
    }
  }
  ```

**2. Deployment Agent** (`cli_agent/agents.py`)
- Option to use `RecursiveDeploymentAgent` instead of standard agent
- Controlled via `sds.toml`:
  ```toml
  [rlm]
  enabled = true
  max_recursion_depth = 5
  auto_enable_threshold = 50000  # Auto-enable for logs > 50K chars
  ```

**3. DSPy Optimization** (`dspy_integration/`)
- New signatures for RLM prompts
- New metrics that reward RLM usage
- Training examples include RLM statistics

## How It Works: Example

### Scenario: Deployment fails with 100K char error log

**Traditional Approach**:
```
Prompt: "Fix this deployment error: [100K chars of logs]"
→ Sends 25K tokens to LLM
→ Exceeds GPT-4 8K limit
→ Even if it fits, expensive and slow
```

**RLM Approach**:
```python
# Step 1: LLM given context summary
"Available: error_log (100K chars), previous_attempts (30), ..."

# Step 2: LLM decides to filter using code
ACTION: execute_code
CODE:
  import re
  errors = re.findall(r'Error: ([^\n]+)', error_log)
  result = list(set(errors))[:10]  # Top 10 unique errors

# Step 3: Result returned (~200 chars)
Result: ['port 8080 conflict', 'Redis timeout', ...]

# Step 4: LLM analyzes pattern
ACTION: execute_code
CODE:
  ports = re.findall(r'port (\d+)', error_log)
  result = f"Ports: {set(ports)}, Count: {len(ports)}"

Result: "Ports: {8080, 8081, 8082}, Count: 150"

# Step 5: LLM makes focused recursive call
ACTION: recursive_call
SUBTASK: "Analyze port allocation in docker-compose.yml"
CONTEXT: {"ports": [8080, 8081, 8082]}

# Step 6: Recursive call returns fix
Result: "Use ports 9080-9082 instead"

# Step 7: Final answer
ACTION: final_answer
ANSWER: "Update docker-compose.yml ports section..."
```

**Result**:
- Tokens sent: ~2,000 (vs 25,000)
- Savings: 92% reduction
- Accuracy: Better (code-based extraction)
- Cost: 8% of traditional

## DSPy Optimization with RLM

### Traditional DSPy Metrics (Pre-RLM)

```python
CompositeMetric(
    success_weight=0.5,        # Did deployment succeed?
    efficiency_weight=0.25,    # How many iterations?
    token_weight=0.15,         # How many tokens used?
    health_check_weight=0.1    # Health check quality
)
```

**Problem**: Doesn't encourage RLM usage
- Prompts don't specify to use code filtering
- LLM defaults to processing entire context
- No reward for efficient context exploration

### RLM-Enhanced DSPy Metrics

```python
RLMCompositeMetric(
    success_weight=0.35,              # Still most important
    efficiency_weight=0.20,           # Iteration efficiency
    token_weight=0.15,                # Overall token usage
    rlm_efficiency_weight=0.15,       # NEW: RLM call efficiency
    rlm_context_weight=0.15           # NEW: Context utilization
)
```

#### New Metric 1: RLMEfficiencyMetric

Evaluates how well prompts utilize RLM features:

```python
def __call__(self, example, prediction, trace):
    stats = example.rlm_statistics

    # 1. Total calls (prefer 3-10 calls, penalize excessive)
    calls_score = evaluate_call_count(stats['total_calls'])

    # 2. Recursion depth (prefer shallow, penalize deep)
    depth_score = evaluate_depth(stats['max_depth_reached'])

    # 3. Code-to-recursive ratio (prefer 2:1 ratio)
    #    More code filtering, fewer recursive calls
    ratio = stats['code_executions'] / stats['recursive_calls']
    ratio_score = evaluate_ratio(ratio, target=2.0)

    return (calls_score * 0.4 +
            depth_score * 0.3 +
            ratio_score * 0.3)
```

**What DSPy learns**:
- ✓ Prompts should encourage code execution first
- ✓ Filter context before recursive calls
- ✓ Avoid deep recursion (use iteration instead)
- ✗ Don't make 50 recursive calls (use code)
- ✗ Don't skip code and recurse immediately

#### New Metric 2: RLMContextUtilizationMetric

Evaluates token savings from context filtering:

```python
def __call__(self, example, prediction, trace):
    total_context_tokens = estimate_context_size(example)
    tokens_saved = example.rlm_statistics['total_tokens_saved']

    # Savings ratio (target: 50%+ savings)
    savings_ratio = tokens_saved / total_context_tokens

    # Score based on how close to target
    if savings_ratio >= 0.5:
        return 1.0  # Excellent
    else:
        return savings_ratio / 0.5  # Partial credit
```

**What DSPy learns**:
- ✓ Use code to extract relevant parts
- ✓ Filter before recursive calls
- ✓ Process summaries, not full logs
- ✗ Don't send entire error_log to LLM
- ✗ Don't bypass RLM environment

### DSPy Optimization Process

**Phase 1: Baseline (Without RLM awareness)**

```python
# Initial prompt (generated by BootstrapFewShot)
"""
Analyze the deployment error and provide a fix.
Error log: {error_log}
Previous attempts: {previous_attempts}
"""

# Behavior: LLM receives entire context directly
# Metric scores:
#   - Success: 0.6 (works sometimes)
#   - RLM efficiency: 0.0 (no RLM usage)
#   - Context utilization: 0.0 (sent everything)
# Composite: 0.21 (poor)
```

**Phase 2: After 10 RLM examples**

```python
# DSPy-optimized prompt (learned from high-scoring trajectories)
"""
You have access to error_log and previous_attempts as variables.

Step 1: Extract unique errors using regex
Step 2: Identify patterns across previous_attempts
Step 3: If needed, make a focused recursive call
Step 4: Provide fix based on analysis

Use ACTION: execute_code to query variables first.
"""

# Behavior: LLM uses code to filter, makes focused calls
# Metric scores:
#   - Success: 0.8 (better accuracy)
#   - RLM efficiency: 0.85 (3 code calls, 1 recursive)
#   - Context utilization: 0.90 (80% token savings)
# Composite: 0.84 (excellent!)
```

**Phase 3: After 50 RLM examples**

```python
# Further optimized (COPRO/MIPROv2)
"""
Available in REPL: error_log, previous_attempts, trajectory_data

Analysis protocol:
1. CODE: Extract error frequencies
   result = Counter(re.findall(r'Error: ([^\n]+)', error_log))

2. CODE: Check if pattern exists in previous_attempts
   result = [a for a in previous_attempts if 'port' in a['error']]

3. If pattern found in >50% attempts:
   RECURSIVE_CALL: Analyze specific error type

4. FINAL_ANSWER: Targeted fix with high confidence

Prioritize code-based analysis over LLM reasoning.
"""

# Behavior: Highly systematic, minimal token waste
# Metric scores:
#   - Success: 0.95 (very reliable)
#   - RLM efficiency: 0.95 (optimal call pattern)
#   - Context utilization: 0.95 (90% savings)
# Composite: 0.95 (near-optimal!)
```

## Integration Strategy

### Minimal Implementation (Quick Start)

**1. Enable RLM for deployment phase only**

```python
# app_operator/cli_agent/agents.py
from app_operator.rlm import RecursiveDeploymentAgent

def fix_deployment_error(self, error_log, ...):
    if len(error_log) > 50000:  # Auto-enable for long logs
        agent = RecursiveDeploymentAgent(self.trajectory)
        return agent.fix_deployment_error(...)
    else:
        # Use traditional approach
        return self._traditional_fix(...)
```

**2. Record RLM statistics in trajectory**

Already works! `RecursiveDeploymentAgent` uses trajectory callback.

**3. Add RLM metrics to DSPy optimization**

```python
# app_operator/dspy_integration/optimizer.py
from app_operator.rlm.metrics import RLMCompositeMetric

def optimize_prompts(self, ...):
    if self.config.rlm.enabled:
        metric = RLMCompositeMetric()
    else:
        metric = CompositeMetric()

    # Rest of optimization logic...
```

### Full Implementation (Production)

**1. RLM for all phases**

```toml
# sds.toml
[rlm]
enabled = true
max_recursion_depth = 5

# Per-phase configuration
[rlm.exploration]
enabled = true
auto_enable_threshold = 100000  # Large codebases

[rlm.deployment]
enabled = true
auto_enable_threshold = 50000   # Long error logs

[rlm.monitoring]
enabled = false  # Not needed for monitoring
```

**2. DSPy signatures for RLM**

```python
# app_operator/dspy_integration/rlm_signatures.py

class RLMDeployerFixError(dspy.Signature):
    """RLM-based deployment error fixing.

    You operate in an RLM environment with context variables.
    Use execute_code to filter, recursive_call for focused analysis.
    """

    available_variables: str = dspy.InputField(
        desc="Variables in REPL: error_log, previous_attempts, etc."
    )

    task_description: str = dspy.InputField(
        desc="What to accomplish"
    )

    recursion_depth: int = dspy.InputField(
        desc="Current recursion depth (0 = root)"
    )

    action_type: str = dspy.OutputField(
        desc="One of: execute_code, recursive_call, final_answer"
    )

    action_content: str = dspy.OutputField(
        desc="Python code, sub-prompt, or final answer"
    )
```

**3. Training data collection**

```bash
# Run with RLM enabled to collect training data
uv run -m app_operator run /path/to/app --config rlm_enabled.toml

# Trajectories now include RLM statistics
# DSPy can learn from high RLM-efficiency examples
```

## Benefits for SDS Prompt Optimization

### 1. Handle Larger Contexts

**Before RLM**:
- Limited to 8K tokens (GPT-4) or 100K (Claude-2)
- After 20+ failed deployment attempts, context exceeds limits
- Solution: Truncate history (lose information)

**With RLM**:
- Can handle 10M+ tokens
- Full history available as queryable variables
- LLM selectively accesses what it needs

### 2. Better Pattern Recognition

**Before RLM**:
```
Prompt: "Here's 100K chars of error logs. Find the pattern."
LLM: *struggles with repetitive text, may hallucinate*
```

**With RLM**:
```python
CODE: result = Counter(re.findall(r'Error: ([^\n]+)', error_log))
LLM: "Port 8080 conflict occurs 150 times - clear pattern"
```

### 3. Cost Reduction

**Example**: 30 deployment iterations, 50K chars error log each

**Traditional**:
- Total context: 30 × 50K = 1.5M chars = 375K tokens
- Cost per optimization: $15 (GPT-4 pricing)
- 10 optimization iterations: $150

**RLM**:
- Context filtered: ~50K tokens (87% reduction)
- Cost per optimization: $2
- 10 optimization iterations: $20
- **Savings: $130 (87%)**

### 4. DSPy Learns Better Strategies

**Traditional optimization learns**:
- "Include more examples" → longer prompts
- "Add detailed instructions" → longer prompts
- Result: Token costs increase

**RLM optimization learns**:
- "Filter using regex before analysis"
- "Check previous_attempts programmatically"
- "Recurse on specific error types only"
- Result: **Both accuracy and efficiency improve**

### 5. Emergent Behaviors (from paper)

RLMs exhibit useful patterns without explicit training:

**Filtering**:
```python
# LLM learns to filter input before processing
CODE: relevant = [line for line in error_log.split('\n')
                  if 'Error' in line or 'Failed' in line]
```

**Chunking**:
```python
# For large files, process in chunks
CODE: chunks = [error_log[i:i+5000]
                for i in range(0, len(error_log), 5000)]
# Then recursive_call on each chunk
```

**Answer verification**:
```python
# Verify fix by simulating deployment
RECURSIVE_CALL: "Simulate deployment with proposed fix"
# If simulation fails, iterate
```

## Metrics Comparison

### Example Trajectory Evaluation

**Trajectory**: Hotel Reservation deployment, 15 iterations

**Traditional Metrics**:
```python
{
    "success": 1.0,              # Eventually succeeded
    "iteration_efficiency": 0.6,  # 15 iters (not great)
    "token_efficiency": 0.4,      # Used 45K tokens
    "composite": 0.73
}
```

**RLM-Enhanced Metrics**:
```python
{
    "success": 1.0,
    "iteration_efficiency": 0.6,
    "token_efficiency": 0.8,            # Only used 15K tokens (RLM filtering)
    "rlm_efficiency": 0.85,             # 4 calls, depth 2, good ratio
    "rlm_context_utilization": 0.90,   # 90% token savings
    "composite": 0.86                   # 13 points higher!
}
```

**Impact on DSPy optimization**:
- Traditional: Optimizes toward success, but token-heavy
- RLM: Optimizes toward success + efficient RLM usage
- Result: Better prompts that are both accurate AND cheap

## Configuration

### sds.toml

```toml
[rlm]
# Enable RLM globally
enabled = false  # Set to true to enable

# Maximum recursion depth for recursive_call
max_recursion_depth = 5

# Auto-enable RLM for large contexts
auto_enable_threshold = 50000  # chars

# REPL environment type
repl_environment = "python"  # python, bash, mixed

# Phases to use RLM
[rlm.phases]
exploration = true       # Code analysis benefits from RLM
deployment = true        # Error log analysis is main use case
monitoring = false       # Short health check logs don't need it

[dspy]
# Use RLM-enhanced metrics when RLM is enabled
use_rlm_metrics = true

# RLM metric weights (only used if use_rlm_metrics = true)
[dspy.rlm_weights]
success = 0.35
iteration_efficiency = 0.20
token_efficiency = 0.15
rlm_efficiency = 0.15
rlm_context_utilization = 0.15
```

### Command-Line Usage

```bash
# Run with RLM enabled
uv run -m app_operator run /path/to/app --rlm-enabled

# Optimize prompts with RLM metrics
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error \
    --optimizer MIPROv2 \
    --use-rlm-metrics

# Analyze RLM usage in trajectories
uv run -m app_operator analyze-rlm-usage \
    --trajectory .sds/trajectory.json
```

## Next Steps

### POC Validation (This PR)

1. ✓ Core RLM environment implementation
2. ✓ RLM-enabled deployment agent
3. ✓ RLM metrics for DSPy
4. ✓ Example demonstration
5. ⏳ Unit tests for RLM components
6. ⏳ Integration with existing agents

### Production Implementation (Future)

1. Real LLM integration (Gemini, Claude, OpenAI)
2. Async execution for parallel code calls
3. Sandboxed code execution (security)
4. RLM for exploration and monitoring phases
5. Advanced chunking strategies
6. Benchmark against traditional approach

### Research Directions

1. **Train RLM-optimized models** (from paper section 5.2)
   - Use RLM trajectories to fine-tune base models
   - Models learn to use RLM features implicitly

2. **Adaptive recursion depth**
   - Learn optimal depth per task type
   - Trade-off: depth vs cost

3. **Hybrid RLM + RAG**
   - Combine with vector retrieval
   - Best of both: structured + semantic search

## References

- Zhang et al., "Recursive Language Models", MIT CSAIL, 2025
- DSPy documentation: https://dspy-docs.vercel.app/
- SDS trajectory system: `app_operator/trajectory.py`
- SDS DSPy integration: `docs/dspy-optimization.md`
