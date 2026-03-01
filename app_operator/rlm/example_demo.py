"""Example demonstration of RLM in SDS.

This shows:
1. How RLM handles long error logs efficiently
2. How RLM statistics are tracked for DSPy optimization
3. Comparison: traditional prompt vs RLM approach
"""

import json

from app_operator.rlm.environment import RLMEnvironment, RLMContext
from app_operator.rlm.metrics import RLMCompositeMetric


def create_example_error_log() -> str:
    """Create a realistic long error log (100K+ chars)."""
    # Simulate a log with multiple deployment failures
    error_log = ""

    # Add 50 iterations of similar errors
    for i in range(50):
        error_log += f"""
===== Deployment Attempt {i + 1} =====
[2026-02-13 10:{i:02d}:00] Starting deployment...
[2026-02-13 10:{i:02d}:05] Building Docker images...
[2026-02-13 10:{i:02d}:15] Step 1/12 : FROM golang:1.21
[2026-02-13 10:{i:02d}:16]  ---> a1b2c3d4e5f6
[2026-02-13 10:{i:02d}:17] Step 2/12 : WORKDIR /app
[2026-02-13 10:{i:02d}:18]  ---> Running in xyz123
[2026-02-13 10:{i:02d}:19]  ---> abc456
... (many build steps) ...
[2026-02-13 10:{i:02d}:45] Starting services...
[2026-02-13 10:{i:02d}:46] Error: Cannot start service frontend: driver failed programming external connectivity on endpoint hotel-frontend-{i}
[2026-02-13 10:{i:02d}:46] Error: Bind for 0.0.0.0:808{i % 10} failed: port is already allocated
[2026-02-13 10:{i:02d}:47] ERROR: for frontend  Cannot start service frontend: driver failed
[2026-02-13 10:{i:02d}:48] Encountered errors while bringing up the project.
[2026-02-13 10:{i:02d}:49] Deployment FAILED

"""

        # Add some different error types for realism
        if i % 5 == 0:
            error_log += f"""
[2026-02-13 10:{i:02d}:50] Warning: MongoDB connection timeout
[2026-02-13 10:{i:02d}:51] Error: dial tcp 172.17.0.{i}:27017: i/o timeout
"""

        if i % 7 == 0:
            error_log += f"""
[2026-02-13 10:{i:02d}:52] Error: Redis connection refused
[2026-02-13 10:{i:02d}:53] Error: dial tcp 127.0.0.1:6379: connect: connection refused
"""

    return error_log


def demonstrate_traditional_approach():
    """Show how traditional approach would handle long error log."""
    print("=" * 80)
    print("TRADITIONAL APPROACH (Without RLM)")
    print("=" * 80)

    error_log = create_example_error_log()

    print(f"\nError log size: {len(error_log):,} characters (~{len(error_log) // 4:,} tokens)")

    # Traditional prompt would include entire log
    traditional_prompt = f"""You are a deployment debugging agent.

The deployment failed. Here is the complete error log:

{error_log}

Analyze the errors and provide a fix for the deployment script.
"""

    prompt_size = len(traditional_prompt)
    print(f"Prompt size: {prompt_size:,} characters (~{prompt_size // 4:,} tokens)")

    # Check if it exceeds common context windows
    context_limits = {
        "GPT-4": 8192,
        "GPT-4-32k": 32768,
        "Claude-2": 100000,
        "Gemini-1.5-Pro": 1000000,
    }

    print("\nContext window compatibility:")
    estimated_tokens = prompt_size // 4
    for model, limit in context_limits.items():
        fits = "✓" if estimated_tokens < limit else "✗"
        print(f"  {fits} {model}: {limit:,} tokens")

    print(
        "\nProblems:\n"
        "  1. Large token cost (entire log sent to LLM)\n"
        "  2. LLM may struggle to identify patterns in repetitive logs\n"
        "  3. Important errors buried in noise\n"
        "  4. No opportunity for focused analysis"
    )

    return {
        "approach": "traditional",
        "log_size": len(error_log),
        "prompt_size": prompt_size,
        "estimated_tokens": estimated_tokens,
        "fits_in_8k": estimated_tokens < 8192,
    }


def demonstrate_rlm_approach():
    """Show how RLM approach handles the same error log."""
    print("\n" + "=" * 80)
    print("RLM APPROACH (With Recursive Language Models)")
    print("=" * 80)

    error_log = create_example_error_log()

    print(f"\nError log size: {len(error_log):,} characters (~{len(error_log) // 4:,} tokens)")

    # Create RLM context
    context = RLMContext(
        error_log=error_log,
        deployment_script="# deployment script content here",
        previous_attempts=[],
    )

    # Create RLM environment
    rlm_env = RLMEnvironment(context=context, max_recursion_depth=5)

    print("\nRLM Environment created - context stored as variables")
    print(f"Available to LLM:\n{context.get_summary()}")

    print("\n--- RLM Execution Trace ---\n")

    # Simulate RLM execution steps
    print("Step 1: LLM decides to extract unique errors using code")
    code1 = """
import re
# Extract all error lines
errors = re.findall(r'Error: ([^\\n]+)', error_log)
# Get unique errors
unique_errors = list(set(errors))
# Sort by frequency
from collections import Counter
error_counts = Counter(errors)
result = error_counts.most_common(5)
"""

    result1 = rlm_env.execute_code(code1, "Extract and rank errors by frequency")
    print(f"Result: {result1}\n")

    print("Step 2: LLM analyzes the pattern")
    code2 = """
# Focus on the most common error
most_common = 'port is already allocated'
# Find which ports are mentioned
ports = re.findall(r'Bind for [^:]+:(\\d+)', error_log)
result = f"Port conflict detected. Ports used: {set(ports)}"
"""

    result2 = rlm_env.execute_code(code2, "Analyze port conflicts")
    print(f"Result: {result2}\n")

    print("Step 3: LLM makes a recursive call to analyze port allocation")
    # Simulate recursive call with filtered context
    filtered_context = {"port_errors": "port 8080-8089 conflicts"}

    def dummy_llm(prompt: str) -> str:
        return "Port range 8080-8089 conflicts with existing services. Suggest using 9080-9089."

    result3 = rlm_env.recursive_call(
        "Analyze port allocation strategy and suggest fix",
        filtered_context=filtered_context,
        llm_function=dummy_llm,
    )
    print(f"Recursive call result: {result3}\n")

    print("Step 4: LLM provides final answer")
    final_answer = """Based on RLM analysis:
- Root cause: Port conflicts (8080-8089 already allocated)
- Fix: Update docker-compose.yml to use ports 9080-9089
- Confidence: High (pattern seen in 50/50 attempts)
"""
    print(f"Final Answer:\n{final_answer}")

    # Get statistics
    stats = rlm_env.get_statistics()

    print("\n--- RLM Statistics ---")
    print(json.dumps(stats, indent=2))

    baseline_tokens = stats['baseline_context_tokens']
    estimated_tokens = len(error_log) // 4
    tokens_saved = baseline_tokens

    print("\nBenefits:")
    print(
        f"  1. Tokens saved: ~{tokens_saved:,} "
        f"({tokens_saved / estimated_tokens * 100:.1f}% reduction)"
    )
    print(
        f"  2. Focused analysis: {stats['code_executions']} code queries + {stats['recursive_calls']} recursive calls")
    print(f"  3. Max recursion depth: {stats['max_depth_reached']} (efficient)")
    print("  4. Pattern extraction via code (more reliable than LLM parsing)")

    return {
        "approach": "rlm",
        "log_size": len(error_log),
        "total_calls": stats["total_calls"],
        "tokens_saved": tokens_saved,
        "tokens_sent": estimated_tokens - tokens_saved,
        "savings_ratio": tokens_saved / estimated_tokens,
    }


def demonstrate_dspy_optimization():
    """Show how RLM statistics enable DSPy optimization."""
    print("\n" + "=" * 80)
    print("DSPy OPTIMIZATION WITH RLM")
    print("=" * 80)

    print("\nTraditional DSPy metrics:")
    print("  - DeploymentSuccessMetric: Did it work? (0/1)")
    print("  - IterationEfficiencyMetric: How many attempts?")
    print("  - TokenEfficiencyMetric: How many tokens used?")

    print("\nRLM-Enhanced DSPy metrics:")
    print("  - RLMEfficiencyMetric: How well does prompt use RLM features?")
    print("    * Rewards: Fewer recursive calls, code filtering before decisions")
    print("    * Penalizes: Deep recursion, no code usage, excessive calls")
    print("\n  - RLMContextUtilizationMetric: How much context filtered vs sent?")
    print("    * Rewards: High token savings (>50% of original context)")
    print("    * Penalizes: Sending entire log to LLM (defeats RLM purpose)")

    print("\nRLM Composite Metric Weights:")
    print("  - Success: 35% (still most important)")
    print("  - Iteration efficiency: 20%")
    print("  - Token efficiency: 15%")
    print("  - RLM call efficiency: 15% (NEW)")
    print("  - RLM context utilization: 15% (NEW)")

    print("\nExample optimization scenario:")
    print("\nPrompt V1 (Baseline):")
    print("  'Analyze error_log and fix deployment'")
    print("  -> LLM sends entire log, no code usage")
    print("  -> RLM score: 0.3 (poor context utilization)")

    print("\nPrompt V2 (DSPy Optimized):")
    print("  'First, use regex to extract unique errors from error_log.'")
    print("  'Then, analyze patterns and make recursive call if needed.'")
    print("  -> LLM uses code to filter, focused analysis")
    print("  -> RLM score: 0.85 (excellent)")

    print("\nDSPy learns:")
    print("  1. Prompts should explicitly encourage code execution")
    print("  2. Filtering before analysis improves both accuracy and cost")
    print("  3. Recursive calls should be focused, not exploratory")
    print("  4. Pattern: code -> filter -> recurse -> answer")

    # Create example metric
    metric = RLMCompositeMetric()

    # Simulate example and prediction
    class MockExample:
        def __init__(self):
            self.success = True
            self.iterations = 2
            self.token_usage = {"input": 500, "output": 200}
            self.rlm_statistics = {
                "total_calls": 4,
                "code_executions": 3,
                "recursive_calls": 1,
                "total_tokens_saved": 20000,
                "max_depth_reached": 1,
            }
            self.error_log = "x" * 100000  # 100K chars

    class MockPrediction:
        def __init__(self):
            self.rendered_prompt = "optimized prompt"

    example = MockExample()
    prediction = MockPrediction()

    score = metric(example, prediction)

    print(f"\nExample metric evaluation: {score:.3f}")
    print("  (This score guides DSPy to optimize toward RLM-friendly prompts)")


def main():
    """Run all demonstrations."""
    print("\n" + "=" * 80)
    print("RLM PROOF-OF-CONCEPT FOR SDS")
    print("Demonstrating Recursive Language Models for Deployment Debugging")
    print("=" * 80)

    # Run demonstrations
    trad_results = demonstrate_traditional_approach()
    rlm_results = demonstrate_rlm_approach()
    demonstrate_dspy_optimization()

    # Summary comparison
    print("\n" + "=" * 80)
    print("SUMMARY COMPARISON")
    print("=" * 80)

    print(f"\nLog size: {trad_results['log_size']:,} chars")

    print("\nTraditional Approach:")
    print(f"  - Tokens sent to LLM: ~{trad_results['estimated_tokens']:,}")
    print(f"  - Fits in 8K context: {'Yes' if trad_results['fits_in_8k'] else 'No'}")
    print("  - Analysis depth: Single-pass")
    print("  - Cost: Full context cost")

    print("\nRLM Approach:")
    print(f"  - Tokens sent to LLM: ~{rlm_results['tokens_sent']:,}")
    print(f"  - Tokens saved: ~{rlm_results['tokens_saved']:,} ({rlm_results['savings_ratio']:.1%} reduction)")
    print("  - Fits in 8K context: Yes (filtered)")
    print("  - Analysis depth: Multi-level (code + recursion)")
    print(f"  - Cost: {1 - rlm_results['savings_ratio']:.1%} of traditional")

    print("\nKey Insights for SDS:")
    print("  1. RLM enables handling 100K+ char logs efficiently")
    print("  2. Code-based filtering more reliable than LLM parsing")
    print("  3. DSPy can optimize prompts to use RLM features better")
    print("  4. New metrics (RLM efficiency, context utilization) guide optimization")
    print("  5. Trajectory already captures everything needed - minimal changes required")

    print("\n" + "=" * 80)
    print("END OF DEMONSTRATION")
    print("=" * 80)


if __name__ == "__main__":
    main()
