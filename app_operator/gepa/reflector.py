"""Reflective prompt mutation using an LLM to analyze traces and propose changes."""

import re
import threading
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage

from app_operator.config import GEPAConfig
from app_operator.llm import create_chat_model
from app_operator.logger import logger


@dataclass
class ExecutionTrace:
    """Captured execution trace from an SDS agent run."""

    prompt_used: str
    agent_type: str
    phase: str
    messages: list[dict[str, Any]]
    evaluation_result: dict[str, Any]
    success: bool
    metric_scores: dict[str, float] = field(default_factory=dict)
    generated_scripts: dict[str, str] = field(default_factory=dict)
    efficiency: Any = None


class PromptReflector:
    """Uses an LLM to analyze execution traces and propose prompt mutations.

    The reflection LM can be a different (potentially stronger) model than
    the agent LM, following the original GEPA paper's pattern.
    """

    def __init__(self, gepa_config: GEPAConfig) -> None:
        self._gepa_config = gepa_config
        self._llm = None
        self._llm_lock = threading.Lock()

    def _get_llm(self):
        """Build the reflection LLM using create_chat_model directly."""
        if self._llm is None:
            with self._llm_lock:
                if self._llm is None:
                    model = self._gepa_config.reflection_model
                    if model is None:
                        raise ValueError("reflection_model must be set in GEPAConfig to use the reflection LLM")
                    self._llm = create_chat_model(
                        self._gepa_config.reflection_provider,
                        model,
                    )
        return self._llm

    def mutate(
        self,
        current_prompt: str,
        traces: list[ExecutionTrace],
        template_name: str,
    ) -> tuple[str, str]:
        """Analyze traces and propose a targeted prompt mutation.

        Returns:
            Tuple of (mutated_prompt, rationale).
        """
        reflection_prompt = self._build_reflection_prompt(current_prompt, traces, template_name)
        return self._call_reflection_lm(reflection_prompt)

    def crossover(
        self,
        prompt_a: str,
        prompt_b: str,
        traces_a: list[ExecutionTrace],
        traces_b: list[ExecutionTrace],
        template_name: str,
    ) -> tuple[str, str]:
        """Merge the best aspects of two prompts.

        Returns:
            Tuple of (merged_prompt, rationale).
        """
        crossover_prompt = self._build_crossover_prompt(prompt_a, prompt_b, traces_a, traces_b, template_name)
        return self._call_reflection_lm(crossover_prompt)

    def _build_reflection_prompt(
        self,
        current_prompt: str,
        traces: list[ExecutionTrace],
        template_name: str,
    ) -> str:
        """Build the prompt asking the reflection LM to analyze and mutate."""
        trace_text = self._format_traces(traces)
        metric_text = self._format_metric_scores(traces)
        scripts_text = self._format_generated_scripts(traces)
        failure_text = self._format_failure_patterns(traces)
        agent_type = traces[0].agent_type if traces else "deployer"
        guidelines = self._mutation_guidelines(agent_type)

        return f"""You are an expert prompt engineer optimizing prompts for \
an AI-powered DevOps system called SDS (Self-Defining Systems).

## Current Prompt Template: {template_name}

```
{current_prompt}
```

{metric_text}

{scripts_text}

{failure_text}

## Recent Execution Traces

The following traces show how the current prompt performed on recent tasks:

{trace_text}

{guidelines}

## Your Task

Analyze the execution traces above. Identify:
1. What went wrong in failed cases (if any)
2. What patterns successful cases share
3. Specific instructions that are missing, unclear, or counterproductive

Then propose a TARGETED mutation to the prompt text. Your mutation should:
- Address specific failure modes observed in the traces
- Preserve instructions that led to successful outcomes
- Be minimal — change only what needs changing
- Keep the prompt concise (shorter prompts tend to work better)
- Maintain all Jinja2 template variables ({{{{ variable_name }}}}) exactly as they are

## Output Format

Respond with:

<rationale>
Explain what you observed and why you're making these changes.
</rationale>

<mutated_prompt>
The full mutated prompt text here. Include ALL Jinja2 template variables unchanged.
</mutated_prompt>"""

    def _build_crossover_prompt(
        self,
        prompt_a: str,
        prompt_b: str,
        traces_a: list[ExecutionTrace],
        traces_b: list[ExecutionTrace],
        template_name: str,
    ) -> str:
        """Build crossover prompt."""
        traces_a_text = self._format_traces(traces_a)
        traces_b_text = self._format_traces(traces_b)

        return f"""You are an expert prompt engineer performing crossover \
(merging) of two prompt variants.

## Template: {template_name}

## Prompt A:
```
{prompt_a}
```

### Prompt A Execution Traces:
{traces_a_text}

## Prompt B:
```
{prompt_b}
```

### Prompt B Execution Traces:
{traces_b_text}

## Your Task

Merge the best aspects of both prompts into a single improved prompt. Consider:
- Which instructions from each prompt led to better outcomes?
- Are there complementary strengths to combine?
- Can you resolve weaknesses from one prompt using strengths from the other?

Preserve all Jinja2 template variables ({{{{ variable_name }}}}) exactly.

<rationale>
Explain what you took from each prompt and why.
</rationale>

<mutated_prompt>
The merged prompt text here.
</mutated_prompt>"""

    def _format_traces(self, traces: list[ExecutionTrace]) -> str:
        """Format execution traces into readable text for the reflection LM."""
        if not traces:
            return "(no traces available)"

        parts = []
        for i, trace in enumerate(traces):
            parts.append(f"### Trace {i + 1} ({trace.agent_type} / {trace.phase})")
            parts.append(f"Success: {trace.success}")
            parts.append(f"Evaluation: {trace.evaluation_result}")

            messages = trace.messages
            if len(messages) > 20:
                parts.append(f"(showing last 20 of {len(messages)} messages)")
                messages = messages[-20:]
            for msg in messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if content and len(content) > 1000:
                    content = content[:1000] + "..."

                if role == "tool_call":
                    tool = msg.get("tool", "unknown")
                    exit_code = msg.get("exit_code", "N/A")
                    stderr = msg.get("stderr", "")
                    stdout = msg.get("stdout", "")
                    if stderr and len(stderr) > 500:
                        stderr = stderr[:500] + "..."
                    if stdout and len(stdout) > 500:
                        stdout = stdout[:500] + "..."
                    parts.append(f"  [{role}] {tool} (exit={exit_code})")
                    if stdout:
                        parts.append(f"    stdout: {stdout}")
                    if stderr:
                        parts.append(f"    stderr: {stderr}")
                else:
                    parts.append(f"  [{role}] {content}")
            parts.append("")
        return "\n".join(parts)

    def _format_metric_scores(self, traces: list[ExecutionTrace]) -> str:
        """Format metric scores from traces into a readable section."""
        if not traces or not any(t.metric_scores for t in traces):
            return ""

        parts = ["## Metric Scores"]
        for i, trace in enumerate(traces):
            if not trace.metric_scores:
                continue
            if len(traces) > 1:
                parts.append(f"### Trace {i + 1}")
            for name, score in sorted(trace.metric_scores.items()):
                parts.append(f"- {name}: {score:.2f}")
            total = sum(trace.metric_scores.values())
            count = len(trace.metric_scores)
            if count > 0:
                parts.append(f"Overall: {total / count:.3f}")
        return "\n".join(parts)

    def _format_generated_scripts(self, traces: list[ExecutionTrace]) -> str:
        """Format generated scripts from traces."""
        if not traces or not any(t.generated_scripts for t in traces):
            return ""

        parts = ["## Generated Scripts"]
        for trace in traces:
            for name, content in trace.generated_scripts.items():
                lines = content.split("\n")
                excerpt = "\n".join(lines[:100])
                parts.append(f"\n### {name} (excerpt, first 100 lines):")
                parts.append(f"```bash\n{excerpt}\n```")
        return "\n".join(parts)

    def _format_failure_patterns(self, traces: list[ExecutionTrace]) -> str:
        """Extract and format common failure patterns from traces."""
        patterns = self._extract_failure_patterns(traces)
        if not patterns:
            return ""

        parts = ["## Key Failure Patterns Detected"]
        parts.extend(f"- {pattern}" for pattern in patterns)
        return "\n".join(parts)

    @staticmethod
    def _extract_failure_patterns(traces: list[ExecutionTrace]) -> list[str]:
        """Parse traces for common failure patterns.

        Note: evaluator.py has a parallel implementation that operates on
        raw trajectory dicts rather than ExecutionTrace objects.
        """
        patterns = []
        for trace in traces:
            if trace.success:
                continue
            for msg in trace.messages:
                if msg.get("role") != "tool_call":
                    continue
                exit_code = msg.get("exit_code")
                stderr = str(msg.get("stderr", ""))
                stdout = str(msg.get("stdout", ""))
                args_str = str(msg.get("args", ""))

                if exit_code == -1:
                    patterns.append("Script exited with code -1 (crashed before producing output)")
                elif exit_code and exit_code != 0:
                    tool = msg.get("tool", "unknown")
                    patterns.append(f"{tool} exited with code {exit_code}")

                if "docker-compose" in args_str or "docker-compose" in stdout:
                    patterns.append(
                        "Agent used `docker-compose` (hyphenated) but only `docker compose` (space) is available"
                    )
                if ("mvn " in args_str or "mvn " in stdout) and "./mvnw" not in args_str and "./mvnw" not in stdout:
                    patterns.append("Agent used `mvn` but Maven may not be installed; should use `./mvnw`")
                if "command not found" in stderr.lower():
                    patterns.append(f"Command not found: {stderr[:200]}")

        return list(dict.fromkeys(patterns))

    _MUTATION_GUIDELINES: dict[str, str] = {
        "deployer": """## Mutation Guidelines for Deployer Prompts
- The prompt instructs a coding agent that generates bash scripts
- Common issues: wrong CLI tools (docker-compose vs docker compose), \
missing prerequisites
- Effective prompts are specific about tool versions and fallback patterns
- Scripts should handle missing tools gracefully (check before use)
- Consider adding explicit "AVOID" patterns based on observed failures""",
        "monitor": """## Mutation Guidelines for Monitor Prompts
- The prompt instructs an agent to analyze health check results
- Effective prompts produce structured output with severity levels
- Include expectations for exec_summary, recommendations, and actions""",
        "code_analyzer": """## Mutation Guidelines for Code Analyzer Prompts
- The prompt instructs an agent to analyze a codebase before deployment
- Effective prompts identify build systems, deployment platforms, \
and potential issues
- Include expectations for code_analysis.md and deployment_issues.md""",
    }

    @staticmethod
    def _mutation_guidelines(agent_type: str) -> str:
        """Return domain-specific mutation guidelines."""
        return PromptReflector._MUTATION_GUIDELINES.get(agent_type, "")

    def _call_reflection_lm(self, prompt: str) -> tuple[str, str]:
        """Call the reflection LM and parse the response.

        Returns:
            Tuple of (mutated_prompt, rationale).

        Raises:
            ValueError: If the response is missing <mutated_prompt> tags.
        """
        llm = self._get_llm()
        response = llm.invoke([HumanMessage(content=prompt)])
        # Some models (e.g. Gemini with thinking) return content as a list
        # of parts; extract text parts and join them.
        raw = response.content
        if isinstance(raw, list):
            response_text = "\n".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in raw)
        else:
            response_text = raw

        rationale_match = re.search(r"<rationale>(.*?)</rationale>", response_text, re.DOTALL)
        rationale = rationale_match.group(1).strip() if rationale_match else ""

        prompt_match = re.search(
            r"<mutated_prompt>(.*?)</mutated_prompt>",
            response_text,
            re.DOTALL,
        )
        if not prompt_match:
            logger.warning("Reflection LM did not produce <mutated_prompt> tags")
            raise ValueError("Reflection LM response missing <mutated_prompt> tags")

        mutated_prompt = prompt_match.group(1).strip()
        return mutated_prompt, rationale
