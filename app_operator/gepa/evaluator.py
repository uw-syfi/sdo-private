"""Evaluation harness for measuring SDS agent prompt quality."""

import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from app_operator.gepa.reflector import ExecutionTrace
from app_operator.logger import logger


@dataclass
class EvaluationExample:
    """A single evaluation example (training or validation)."""

    repo_path: Path
    expected_outcome: dict[str, Any]
    agent_type: str
    description: str


@dataclass
class EfficiencyMetrics:
    """Efficiency metrics tracked separately from quality metrics.

    These are recorded for analysis but NOT used in Pareto selection.
    """

    total_characters: int = 0
    estimated_tokens: int = 0
    turn_count: int = 0
    tool_call_count: int = 0
    wall_clock_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sum_efficiency_metrics(
    metrics_list: list["EfficiencyMetrics"],
) -> EfficiencyMetrics:
    """Sum a list of EfficiencyMetrics into a single aggregate."""
    total = EfficiencyMetrics()
    for m in metrics_list:
        for f in fields(EfficiencyMetrics):
            setattr(total, f.name, getattr(total, f.name) + getattr(m, f.name))
    return total


def extract_efficiency_metrics(
    trajectory: dict[str, Any],
) -> EfficiencyMetrics:
    """Extract efficiency metrics from a trajectory."""
    total_chars = 0
    turn_count = 0
    tool_call_count = 0

    for phase_key in ["deployment", "script_generation", "monitoring", "exploration"]:
        conversations = trajectory.get(phase_key, [])
        turn_count += len(conversations)
        for conv in conversations:
            for msg in conv.get("messages", []):
                for text_field in ["content", "stdout", "stderr"]:
                    val = msg.get(text_field)
                    if val:
                        total_chars += len(str(val))
                if msg.get("role") == "tool_call":
                    tool_call_count += 1

    wall_clock = 0.0
    metadata = trajectory.get("metadata", {})
    start_str = metadata.get("start_time", "")
    end_str = metadata.get("end_time", "")
    if start_str and end_str:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y%m%d-%H%M%S"]:
            try:
                start = datetime.strptime(start_str, fmt)
                end = datetime.strptime(end_str, fmt)
                wall_clock = max((end - start).total_seconds(), 0.0)
                break
            except ValueError:
                continue

    return EfficiencyMetrics(
        total_characters=total_chars,
        estimated_tokens=total_chars // 4,  # ~4 chars per token for English
        turn_count=turn_count,
        tool_call_count=tool_call_count,
        wall_clock_seconds=wall_clock,
    )


@dataclass
class EvaluationResult:
    """Result of evaluating a candidate on a set of examples."""

    candidate_id: str
    scores: dict[str, float]
    overall_score: float
    traces: list[ExecutionTrace] = field(default_factory=list)
    efficiency: EfficiencyMetrics | None = None


class SDSEvaluator:
    """Evaluates SDS agent prompts by running agents on test repositories."""

    def __init__(
        self,
        metrics: dict[str, Callable],
        agent_factory: Callable,
        templates_dir: Path,
        runners: dict[str, Any] | None = None,
    ) -> None:
        """
        Args:
            metrics: Named metric functions.
                Each takes (example, trajectory_data) -> float.
            agent_factory: Callable that creates a CodingAgent instance.
            templates_dir: Path to templates (for injecting candidate prompts).
            runners: Optional mapping of agent_type -> AgentRunner. When
                provided, ``_run_agent`` dispatches through these runners
                instead of importing cli_agent agents directly.
        """
        self.metrics = metrics
        self.agent_factory = agent_factory
        self.templates_dir = templates_dir
        self.runners = runners or {}

    @staticmethod
    def _check_repos_clean(examples: list[EvaluationExample]) -> None:
        """Validate that all test repos have a clean git working tree.

        Raises ValueError if any repo has uncommitted changes, preventing
        accidental data loss from the post-run git reset.
        """
        seen: set[Path] = set()
        for ex in examples:
            repo = Path(ex.repo_path).resolve()
            if repo in seen:
                continue
            seen.add(repo)
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise ValueError(f"Not a git repository (or git failed): {repo}")
            if result.stdout.strip():
                raise ValueError(
                    f"Test repo {repo} has uncommitted changes. "
                    "GEPA evaluation resets repos with 'git checkout .' and "
                    "'git clean -fd' after each run. Commit or stash your "
                    "changes before running evaluation."
                )

    def evaluate(
        self,
        prompt_text: str,
        template_name: str,
        examples: list[EvaluationExample],
        candidate_id: str = "",
    ) -> EvaluationResult:
        """Evaluate a prompt candidate on a set of examples.

        For each example:
        1. Inject prompt_text into the Jinja2 template
        2. Run the SDS agent on the example repo
        3. Capture the trajectory
        4. Score using the metrics
        5. Restore the original template
        """
        self._check_repos_clean(examples)

        all_scores: dict[str, list[float]] = {name: [] for name in self.metrics}
        all_traces: list[ExecutionTrace] = []

        for example in examples:
            trajectory_data, trace = self._run_single_example(prompt_text, template_name, example)
            all_traces.append(trace)

            for metric_name, metric_fn in self.metrics.items():
                score = metric_fn(example, trajectory_data)
                all_scores[metric_name].append(score)

        avg_scores = {name: sum(scores) / len(scores) if scores else 0.0 for name, scores in all_scores.items()}

        overall = sum(avg_scores.values()) / len(avg_scores) if avg_scores else 0.0

        total_efficiency = _sum_efficiency_metrics([t.efficiency for t in all_traces if t.efficiency])

        return EvaluationResult(
            candidate_id=candidate_id,
            scores=avg_scores,
            overall_score=overall,
            traces=all_traces,
            efficiency=total_efficiency,
        )

    def _run_single_example(
        self,
        prompt_text: str,
        template_name: str,
        example: EvaluationExample,
    ) -> tuple[dict[str, Any], ExecutionTrace]:
        """Run a single evaluation example.

        Uses a temporary copy of the templates directory so that the
        candidate prompt can be injected without mutating the original
        files on disk.  After the agent runs, the experiment repo is
        reset via git to prevent state corruption across evaluations.
        """
        tmp_dir = tempfile.mkdtemp(prefix="gepa_eval_")
        try:
            tmp_templates = Path(tmp_dir) / "templates"
            shutil.copytree(self.templates_dir, tmp_templates)

            (tmp_templates / template_name).write_text(prompt_text)

            trajectory_data = self._execute_agent(example, templates_dir=tmp_templates)

            phase_key = self._agent_type_to_phase(example.agent_type)
            messages = []
            for conv in trajectory_data.get(phase_key, []):
                messages.extend(conv.get("messages", []))

            efficiency = extract_efficiency_metrics(trajectory_data)

            trace = ExecutionTrace(
                prompt_used=prompt_text,
                agent_type=example.agent_type,
                phase=phase_key,
                messages=messages,
                evaluation_result={},
                success=self._check_success(trajectory_data),
                efficiency=efficiency,
            )

            return trajectory_data, trace

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self._reset_repo(example.repo_path)

    @staticmethod
    def _reset_repo(repo_path: Path) -> None:
        """Reset the experiment repo to its clean git state."""
        try:
            subprocess.run(
                ["git", "checkout", "."],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "clean", "-fd", "--exclude=.sds/trajectories", "--exclude=.sds/logs"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to reset repo {repo_path}: {e}")

    def _execute_agent(
        self,
        example: EvaluationExample,
        templates_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Execute the SDS agent on a test repo and return trajectory data.

        When *templates_dir* is provided the global PromptLoader is
        temporarily swapped so the agent picks up the candidate prompt
        that was written into that directory.

        WARNING: not thread-safe.  The global ``_loader`` is unprotected
        during ``_run_agent`` execution.  Do not call ``evaluate()`` from
        multiple threads concurrently.
        """
        import app_operator.prompts as prompts_module
        from app_operator.filesystem import RealFilesystem
        from app_operator.trajectory import TrajectoryRecorder

        repo_path = Path(example.repo_path)
        recorder = TrajectoryRecorder(repo_path)
        agent = self.agent_factory()
        filesystem = RealFilesystem()

        saved_loader = None
        if templates_dir is not None:
            with prompts_module._loader_lock:
                saved_loader = prompts_module._loader
                prompts_module._loader = prompts_module.PromptLoader(templates_dir=templates_dir)

        try:
            self._run_agent(example, repo_path, agent, filesystem, recorder, self.runners)
        finally:
            if saved_loader is not None:
                with prompts_module._loader_lock:
                    prompts_module._loader = saved_loader

        trajectory_path = recorder.finalize()

        try:
            return json.loads(trajectory_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read trajectory: {e}")
            return {}

    @staticmethod
    def _run_agent(example, repo_path, agent, filesystem, recorder, runners):
        """Dispatch to the appropriate agent based on example type."""
        runner = runners.get(example.agent_type)
        if runner is None:
            logger.warning(f"No runner registered for agent_type={example.agent_type!r}; skipping.")
            return
        runner.run(repo_path, agent, filesystem, recorder)

    @staticmethod
    def _agent_type_to_phase(agent_type: str) -> str:
        """Map agent type to trajectory phase key."""
        mapping = {
            "deployer": "deployment",
            "monitor": "monitoring",
            "code_analyzer": "exploration",
        }
        return mapping.get(agent_type, agent_type)

    @staticmethod
    def _check_success(trajectory_data: dict) -> bool:
        """Check if the agent run was successful from trajectory data."""
        status = trajectory_data.get("metadata", {}).get("status", "")
        return status == "completed"


# --- HELPER FUNCTIONS ---


def extract_generated_scripts(
    trajectory: dict[str, Any],
) -> dict[str, str]:
    """Extract generated script content from write_file tool calls.

    Parses the script_generation phase for write_file tool calls
    where the path ends with .sh, returning a dict of script_name -> content.
    """
    scripts: dict[str, str] = {}
    gen_phases = trajectory.get("script_generation", [])

    for phase in gen_phases:
        for msg in phase.get("messages", []):
            if msg.get("role") != "tool_call":
                continue
            tool = str(msg.get("tool", ""))
            if "write_file" not in tool and "write" not in tool:
                continue
            args = msg.get("args", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    continue
            if not isinstance(args, dict):
                continue

            path = str(args.get("path", args.get("file_path", "")))
            content = str(args.get("content", args.get("file_content", "")))

            if path.endswith(".sh") and content:
                name = path.rsplit("/", 1)[-1]
                scripts[name] = content

    return scripts


# --- METRIC FUNCTIONS ---

# == Deployer Metrics ==


def script_completeness_metric(
    example: EvaluationExample,
    trajectory: dict[str, Any],
) -> float:
    """Quality of generated scripts based on actual write_file tool call content.

    Extracts actual script content from write_file tool calls (not assistant
    commentary), then scores based on 10 feature checks.  Some checks have
    partial credit, so the raw sum can exceed 1.0; the return value is
    clamped to [0.0, 1.0].
    """
    scripts = extract_generated_scripts(trajectory)
    if not scripts:
        gen_phases = trajectory.get("script_generation", [])
        if not gen_phases:
            return 0.0
        all_content = ""
        for phase in gen_phases:
            for msg in phase.get("messages", []):
                all_content += str(msg.get("content", "")) + " "
                all_content += str(msg.get("stdout", "")) + " "
        content = all_content
    else:
        content = "\n".join(scripts.values())

    content_lower = content.lower()
    score = 0.0
    checks = [
        ("#!/bin/bash", 0.10),
        ("set -e", 0.10),
        ("script_dir", 0.10),
        ("print_success", 0.10),
        ("check_prerequisites", 0.10),
    ]
    for keyword, weight in checks:
        if keyword.lower() in content_lower:
            score += weight

    if re.search(r"\b(start|stop|restart|status|logs|build|cleanup|help)\b", content):
        score += 0.10

    if "./mvnw" in content or "make " in content:
        score += 0.10
    elif "docker compose" in content_lower:
        score += 0.05

    if "docker compose" in content_lower and "docker-compose" not in content_lower:
        score += 0.10
    elif "docker compose" in content_lower:
        score += 0.05

    if "trap " in content or "|| " in content:
        score += 0.10

    if "cleanup" in content_lower:
        score += 0.10

    return min(score, 1.0)


_DOCKER_PROGRESS_RE = re.compile(
    r"(Running \d+/\d+|Container\s+\S+\s+(Started|Running|Created)"
    r"|✔\s+Container|Creating\s+\S+\s+\.\.\.\s+done)",
    re.IGNORECASE,
)

_ERROR_PATTERN_RE = re.compile(
    r"\b(error|exception|fatal|traceback|failed to|panic|segfault)\b",
    re.IGNORECASE,
)


def deployment_progress_metric(
    example: EvaluationExample,
    trajectory: dict[str, Any],
) -> float:
    """How far deployment got before failing/succeeding.

    Provides a gradient even when deployment fails — a script that gets to
    the build step scores higher than one that crashes on prerequisites.
    Detects Docker Compose V2 output patterns and penalizes high error
    density in stderr.
    """
    deployment_phases = trajectory.get("deployment", [])
    if not deployment_phases:
        return 0.0

    score = 0.0
    deploy_executed = False
    deploy_produced_output = False
    prereqs_passed = False
    build_reached = False
    containers_created = False
    services_started = False
    deploy_succeeded = False
    total_stderr_lines = 0
    error_lines = 0

    for phase in deployment_phases:
        for msg in phase.get("messages", []):
            if msg.get("role") != "tool_call":
                continue
            args_str = str(msg.get("args", {})).lower()
            stdout = str(msg.get("stdout", "")).lower()
            stderr = str(msg.get("stderr", ""))
            exit_code = msg.get("exit_code")
            all_output = stdout + " " + stderr.lower()

            if stderr.strip():
                lines = stderr.strip().split("\n")
                total_stderr_lines += len(lines)
                error_lines += sum(1 for line in lines if _ERROR_PATTERN_RE.search(line))

            if "deploy" in args_str:
                deploy_executed = True
                if stdout.strip():
                    deploy_produced_output = True
                if ("prerequisit" in all_output and "fail" not in all_output) or "check_prerequisites" in all_output:
                    prereqs_passed = True
                if (
                    "build" in all_output
                    or "docker compose build" in all_output
                    or "compil" in all_output
                    or "pulling" in all_output
                ):
                    build_reached = True
                if _DOCKER_PROGRESS_RE.search(all_output):
                    containers_created = True
                if (
                    ("up" in all_output and "start" in all_output)
                    or "services started" in all_output
                    or "running" in all_output
                ):
                    services_started = True
                if exit_code == 0:
                    deploy_succeeded = True

    if deploy_executed:
        score += 0.10
    if deploy_produced_output:
        score += 0.10
    if prereqs_passed:
        score += 0.10
    if build_reached:
        score += 0.15
    if containers_created:
        score += 0.15
    if services_started:
        score += 0.15
    if deploy_succeeded:
        score += 0.25

    if total_stderr_lines > 0:
        error_density = error_lines / total_stderr_lines
        if error_density > 0.3:
            score -= min(error_density * 0.1, 0.10)

    return max(min(score, 1.0), 0.0)


_CONTAINER_HEALTH_RE = re.compile(
    r"\b(healthy|unhealthy)\b|docker\s+(ps|inspect)|STATUS\s+",
    re.IGNORECASE,
)

_PORT_CHECK_RE = re.compile(
    r"\b(LISTEN|listening)\b|port\s+\d+|nc\s+-z|netstat|ss\s+-",
    re.IGNORECASE,
)

_HTTP_RESPONSE_RE = re.compile(
    r"HTTP/\d|\b(200|201|301|302|404|500)\b|curl\s+|wget\s+|status_code",
    re.IGNORECASE,
)


def health_check_metric(
    example: EvaluationExample,
    trajectory: dict[str, Any],
) -> float:
    """Score the health check script generation and execution.

    Detects container health status, port checks, HTTP endpoint responses,
    and structured output in addition to basic generation/execution.
    """
    scripts = extract_generated_scripts(trajectory)
    score = 0.0

    has_health_script = any("health" in name.lower() for name in scripts)
    if has_health_script:
        score += 0.15

    deployment_phases = trajectory.get("deployment", [])
    health_executed = False
    container_health_detected = False
    port_checks_detected = False
    http_response_detected = False
    health_structured = False
    health_succeeded = False

    for phase in deployment_phases:
        for msg in phase.get("messages", []):
            if msg.get("role") != "tool_call":
                continue
            args_str = str(msg.get("args", {})).lower()
            stdout = str(msg.get("stdout", "")).lower()
            exit_code = msg.get("exit_code")

            if "health_check" in args_str or "health-check" in args_str:
                health_executed = True
                if _CONTAINER_HEALTH_RE.search(stdout):
                    container_health_detected = True
                if _PORT_CHECK_RE.search(stdout):
                    port_checks_detected = True
                if _HTTP_RESPONSE_RE.search(stdout):
                    http_response_detected = True
                if "pass" in stdout or "fail" in stdout or "warning" in stdout:
                    health_structured = True
                if exit_code == 0:
                    health_succeeded = True

    if health_executed:
        score += 0.15
    if container_health_detected:
        score += 0.15
    if port_checks_detected:
        score += 0.15
    if http_response_detected:
        score += 0.15
    if health_structured:
        score += 0.10
    if health_succeeded:
        score += 0.15

    return min(score, 1.0)


def fix_quality_metric(
    example: EvaluationExample,
    trajectory: dict[str, Any],
) -> float:
    """Score the quality of error fixes during deployment.

    Returns 1.0 if deployment succeeded on the first try (no fixes needed).
    """
    deployment_phases = trajectory.get("deployment", [])
    if len(deployment_phases) <= 1:
        return 1.0

    score = 0.0
    read_errors = False
    identified_platform = False
    made_targeted_changes = False
    post_fix_improved = False
    fix_resolved = False

    first_exit_code = None
    last_exit_code = None

    for i, phase in enumerate(deployment_phases):
        for msg in phase.get("messages", []):
            if msg.get("role") != "tool_call":
                continue
            args_str = str(msg.get("args", {})).lower()
            stdout = str(msg.get("stdout", "")).lower()
            tool = str(msg.get("tool", "")).lower()
            exit_code = msg.get("exit_code")

            if i == 0 and "deploy" in args_str and first_exit_code is None:
                first_exit_code = exit_code

            if i > 0:
                if "log" in args_str or "cat " in args_str or "read" in tool:
                    read_errors = True
                if "docker" in stdout or "compose" in stdout or "k8s" in stdout:
                    identified_platform = True
                if "write" in tool or "replace" in tool or "edit" in tool:
                    made_targeted_changes = True
                if "deploy" in args_str:
                    last_exit_code = exit_code

    if last_exit_code is not None and first_exit_code is not None:
        if last_exit_code == 0 and first_exit_code != 0:
            fix_resolved = True
            post_fix_improved = True
        elif first_exit_code != 0 and last_exit_code != first_exit_code:
            post_fix_improved = True

    if read_errors:
        score += 0.20
    if identified_platform:
        score += 0.20
    if made_targeted_changes:
        score += 0.20
    if post_fix_improved:
        score += 0.20
    if fix_resolved:
        score += 0.20

    return min(score, 1.0)


# == Monitor Metrics ==


def health_analysis_depth_metric(
    example: EvaluationExample,
    trajectory: dict[str, Any],
) -> float:
    """Depth and quality of health analysis output."""
    monitoring_phases = trajectory.get("monitoring", [])
    if not monitoring_phases:
        return 0.0

    all_content = ""
    for phase in monitoring_phases:
        for msg in phase.get("messages", []):
            all_content += str(msg.get("content", "")) + " "
    all_content_lower = all_content.lower()

    score = 0.0

    if "<exec_summary>" in all_content_lower or "exec_summary" in all_content_lower:
        score += 0.15
    if "critical" in all_content_lower:
        score += 0.15
    if "warning" in all_content_lower:
        score += 0.15
    if "recommend" in all_content_lower:
        score += 0.15
    if any(w in all_content_lower for w in ["cpu", "memory", "disk", "utilization"]):
        score += 0.15

    section_markers = ["##", "###", "---", "===", "**"]
    has_structure = sum(1 for m in section_markers if m in all_content) >= 2
    if has_structure:
        score += 0.25

    return min(score, 1.0)


def monitoring_coverage_metric(
    example: EvaluationExample,
    trajectory: dict[str, Any],
) -> float:
    """Coverage of health monitoring checks."""
    monitoring_phases = trajectory.get("monitoring", [])
    if not monitoring_phases:
        return 0.0

    score = 0.0
    health_executed = False
    multiple_aspects = 0
    perf_collected = False
    summary_generated = False

    for phase in monitoring_phases:
        for msg in phase.get("messages", []):
            content = str(msg.get("content", "")).lower()
            stdout = str(msg.get("stdout", "")).lower()
            all_text = content + " " + stdout

            if msg.get("role") == "tool_call":
                args_str = str(msg.get("args", {})).lower()
                if "health" in args_str:
                    health_executed = True

            if "container" in all_text or "docker" in all_text:
                multiple_aspects |= 1
            if "port" in all_text or "listen" in all_text:
                multiple_aspects |= 2
            if "endpoint" in all_text or "curl" in all_text or "http" in all_text:
                multiple_aspects |= 4

            if any(w in all_text for w in ["response time", "latency", "throughput", "cpu", "memory"]):
                perf_collected = True
            if "summary" in all_text or "report" in all_text or "overview" in all_text:
                summary_generated = True

    if health_executed:
        score += 0.25
    aspect_count = multiple_aspects.bit_count()
    if aspect_count >= 2:
        score += 0.25
    elif aspect_count == 1:
        score += 0.10
    if perf_collected:
        score += 0.25
    if summary_generated:
        score += 0.25

    return min(score, 1.0)


# == Code Analyzer Metrics ==


def analysis_completeness_metric(
    example: EvaluationExample,
    trajectory: dict[str, Any],
) -> float:
    """Completeness of code analysis output."""
    exploration_phases = trajectory.get("exploration", [])
    if not exploration_phases:
        return 0.0

    score = 0.0
    code_analysis_generated = False
    deployment_issues_generated = False
    multiple_services = False
    deps_mapped = False
    severity_categorized = False

    for phase in exploration_phases:
        for msg in phase.get("messages", []):
            content = str(msg.get("content", "")).lower()
            tool = str(msg.get("tool", "")).lower()
            args = msg.get("args", {})
            if isinstance(args, str):
                args_str = args.lower()
            else:
                args_str = str(args).lower()

            if "write" in tool and "code_analysis" in args_str:
                code_analysis_generated = True
            if "write" in tool and "deployment_issues" in args_str:
                deployment_issues_generated = True

            if content.count("service") >= 2 or content.count("microservice") >= 1:
                multiple_services = True
            if "depend" in content:
                deps_mapped = True
            if any(w in content for w in ["critical", "high", "medium", "low"]):
                severity_categorized = True

    if code_analysis_generated:
        score += 0.20
    if deployment_issues_generated:
        score += 0.20
    if multiple_services:
        score += 0.20
    if deps_mapped:
        score += 0.20
    if severity_categorized:
        score += 0.20

    return min(score, 1.0)


def analysis_accuracy_metric(
    example: EvaluationExample,
    trajectory: dict[str, Any],
) -> float:
    """Accuracy of code analysis findings."""
    exploration_phases = trajectory.get("exploration", [])
    if not exploration_phases:
        return 0.0

    score = 0.0
    all_content = ""
    for phase in exploration_phases:
        for msg in phase.get("messages", []):
            all_content += str(msg.get("content", "")) + " "
    all_content_lower = all_content.lower()

    if "docker compose" in all_content_lower or "kubernetes" in all_content_lower or "k8s" in all_content_lower:
        score += 0.25

    if any(w in all_content_lower for w in ["maven", "gradle", "make", "npm", "yarn", "mvnw", "go build"]):
        score += 0.25

    if re.search(r"\b\d{2,5}\b", all_content) and "port" in all_content_lower:
        score += 0.25

    if any(
        w in all_content_lower
        for w in ["mongodb", "mysql", "postgres", "redis", "memcached", "database", "mongo", "consul"]
    ):
        score += 0.25

    return min(score, 1.0)


# --- METRICS REGISTRY ---

METRICS_REGISTRY: dict[str, dict[str, Callable]] = {
    "deployer": {
        "script_completeness": script_completeness_metric,
        "deployment_progress": deployment_progress_metric,
        "health_check": health_check_metric,
        "fix_quality": fix_quality_metric,
    },
    "monitor": {
        "health_analysis_depth": health_analysis_depth_metric,
        "monitoring_coverage": monitoring_coverage_metric,
    },
    "code_analyzer": {
        "analysis_completeness": analysis_completeness_metric,
        "analysis_accuracy": analysis_accuracy_metric,
    },
}
