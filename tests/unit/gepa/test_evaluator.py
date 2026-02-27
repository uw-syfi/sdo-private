"""Tests for SDSEvaluator, metric functions, and helper utilities."""

from pathlib import Path

import pytest

from app_operator.gepa.evaluator import (
    EvaluationExample,
    METRICS_REGISTRY,
    SDSEvaluator,
    extract_generated_scripts,
    analysis_accuracy_metric,
    analysis_completeness_metric,
    deployment_progress_metric,
    extract_efficiency_metrics,
    fix_quality_metric,
    health_analysis_depth_metric,
    health_check_metric,
    monitoring_coverage_metric,
    script_completeness_metric,
)
from app_operator.gepa.reflector import ExecutionTrace


def _example(agent_type="deployer"):
    """Helper to create a minimal EvaluationExample."""
    return EvaluationExample(
        repo_path=Path("/tmp/test"),
        expected_outcome={},
        agent_type=agent_type,
        description="test",
    )


class _StubSDSEvaluator(SDSEvaluator):
    """Evaluator subclass that returns canned trajectory data."""

    def __init__(self, metrics, trajectory_data):
        super().__init__(
            metrics=metrics,
            agent_factory=lambda: None,
            templates_dir=Path("/tmp"),
        )
        self._trajectory_data = trajectory_data

    @staticmethod
    def _check_repos_clean(examples):
        pass  # Skip git check for stub repos

    def _run_single_example(self, prompt_text, template_name, example):
        phase_key = self._agent_type_to_phase(example.agent_type)
        messages = []
        for conv in self._trajectory_data.get(phase_key, []):
            messages.extend(conv.get("messages", []))

        trace = ExecutionTrace(
            prompt_used=prompt_text,
            agent_type=example.agent_type,
            phase=phase_key,
            messages=messages,
            evaluation_result={},
            success=self._check_success(self._trajectory_data),
        )
        return self._trajectory_data, trace


# --- Helper Tests ---


class TestExtractGeneratedScripts:
    """Test extract_generated_scripts helper."""

    def test_extracts_write_file_sh(self):
        trajectory = {
            "script_generation": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "tool": "write_file",
                            "args": {
                                "path": "/tmp/deploy.sh",
                                "content": "#!/bin/bash\necho hello",
                            },
                        }
                    ]
                }
            ]
        }
        scripts = extract_generated_scripts(trajectory)
        assert "deploy.sh" in scripts
        assert "#!/bin/bash" in scripts["deploy.sh"]


# --- Deployer Metric Tests ---


class TestScriptCompletenessMetric:
    """Test script_completeness_metric."""

    def test_high_quality_script_from_write_file(self):
        content = """#!/bin/bash
set -eo pipefail
SCRIPT_DIR=$(dirname "$0")
print_success() { echo "OK: $1"; }
check_prerequisites() { docker compose version; }
start() { docker compose up -d; }
stop() { docker compose down; }
./mvnw clean package || true
trap cleanup EXIT
cleanup() { docker compose down; }"""
        trajectory = {
            "script_generation": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "tool": "write_file",
                            "args": {
                                "path": "/tmp/deploy.sh",
                                "content": content,
                            },
                        }
                    ]
                }
            ]
        }
        score = script_completeness_metric(_example(), trajectory)
        assert score >= 0.8


class TestDeploymentProgressMetric:
    """Test deployment_progress_metric."""

    def test_full_success(self):
        trajectory = {
            "deployment": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "args": {"script": "deploy.sh start"},
                            "stdout": "check_prerequisites passed\n"
                            "Building...\n"
                            "Container myapp-web-1 Started\n"
                            "Services started up and running",
                            "stderr": "",
                            "exit_code": 0,
                        }
                    ]
                }
            ]
        }
        score = deployment_progress_metric(_example(), trajectory)
        assert score == pytest.approx(1.0)

    def test_deploy_executed_only(self):
        trajectory = {
            "deployment": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "args": {"script": "deploy.sh"},
                            "stdout": "",
                            "stderr": "crashed",
                            "exit_code": 1,
                        }
                    ]
                }
            ]
        }
        score = deployment_progress_metric(_example(), trajectory)
        assert score == pytest.approx(0.10)

    def test_non_deploy_tool_call_ignored(self):
        trajectory = {
            "deployment": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "args": {"script": "ls -la"},
                            "stdout": "files",
                            "exit_code": 0,
                        }
                    ]
                }
            ]
        }
        assert deployment_progress_metric(_example(), trajectory) == 0.0


class TestHealthCheckMetric:
    """Test health_check_metric."""

    def test_health_script_generated(self):
        trajectory = {
            "script_generation": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "tool": "write_file",
                            "args": {
                                "path": "/tmp/health_check.sh",
                                "content": "#!/bin/bash\ncurl localhost",
                            },
                        }
                    ]
                }
            ],
            "deployment": [],
        }
        score = health_check_metric(_example(), trajectory)
        assert score == pytest.approx(0.15)

    def test_health_check_executed_and_passed(self):
        trajectory = {
            "script_generation": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "tool": "write_file",
                            "args": {
                                "path": "/tmp/health_check.sh",
                                "content": "#!/bin/bash",
                            },
                        }
                    ]
                }
            ],
            "deployment": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "args": {"script": "health_check.sh"},
                            "stdout": "web-1: healthy\n"
                            "port 8080 LISTEN\n"
                            "curl localhost:8080 HTTP/1.1 200\n"
                            "All checks passed",
                            "exit_code": 0,
                        }
                    ]
                }
            ],
        }
        score = health_check_metric(_example(), trajectory)
        assert score == pytest.approx(1.0)


class TestFixQualityMetric:
    """Test fix_quality_metric."""

    def test_first_try_success_returns_one(self):
        """A prompt that succeeds first try should not be penalized."""
        trajectory = {
            "deployment": [
                {"messages": [{"role": "tool_call", "args": "deploy", "exit_code": 0}]}
            ]
        }
        assert fix_quality_metric(_example(), trajectory) == 1.0

    def test_fix_with_read_and_write(self):
        trajectory = {
            "deployment": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "args": {"script": "deploy.sh"},
                            "stdout": "",
                            "exit_code": 1,
                        }
                    ]
                },
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "tool": "read_file",
                            "args": {"path": "logs/error.log"},
                            "stdout": "docker compose error",
                            "exit_code": 0,
                        },
                        {
                            "role": "tool_call",
                            "tool": "write_file",
                            "args": {"path": "deploy.sh"},
                            "exit_code": 0,
                        },
                        {
                            "role": "tool_call",
                            "args": {"script": "deploy.sh"},
                            "stdout": "success",
                            "exit_code": 0,
                        },
                    ]
                },
            ]
        }
        score = fix_quality_metric(_example(), trajectory)
        assert score >= 0.60


# --- Monitor Metric Tests ---


class TestHealthAnalysisDepthMetric:
    """Test health_analysis_depth_metric."""

    def test_comprehensive_analysis(self):
        content = """## exec_summary
All services healthy.
### Critical Issues
No critical issues found.
### Warnings
warning: High memory usage on service-a.
### Recommendations
recommend scaling up service-a.
CPU utilization at 80%.
"""
        trajectory = {
            "monitoring": [
                {
                    "messages": [
                        {"role": "assistant", "content": content}
                    ]
                }
            ]
        }
        score = health_analysis_depth_metric(
            _example("monitor"), trajectory
        )
        assert score == pytest.approx(1.0)


class TestMonitoringCoverageMetric:
    """Test monitoring_coverage_metric."""

    def test_comprehensive_monitoring(self):
        trajectory = {
            "monitoring": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "args": {"script": "health_check.sh"},
                            "stdout": "Checking containers... port 8080 listening. "
                            "endpoint http://localhost OK. "
                            "response time 50ms. summary: all good.",
                            "exit_code": 0,
                        }
                    ]
                }
            ]
        }
        score = monitoring_coverage_metric(_example("monitor"), trajectory)
        assert score >= 0.75


# --- Code Analyzer Metric Tests ---


class TestAnalysisCompletenessMetric:
    """Test analysis_completeness_metric."""

    def test_complete_analysis(self):
        trajectory = {
            "exploration": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "tool": "write_file",
                            "args": {"path": ".sds/code_analysis.md", "content": "..."},
                        },
                        {
                            "role": "tool_call",
                            "tool": "write_file",
                            "args": {"path": ".sds/deployment_issues.md", "content": "..."},
                        },
                        {
                            "role": "assistant",
                            "content": "Found service A and service B microservice. "
                            "Dependencies include redis. "
                            "Critical: missing config. Medium: slow queries.",
                        },
                    ]
                }
            ]
        }
        score = analysis_completeness_metric(
            _example("code_analyzer"), trajectory
        )
        assert score == pytest.approx(1.0)


class TestAnalysisAccuracyMetric:
    """Test analysis_accuracy_metric."""

    def test_accurate_analysis(self):
        trajectory = {
            "exploration": [
                {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "Uses docker compose for deployment. "
                            "Build system: maven (./mvnw). "
                            "Port 8080 exposed. "
                            "MongoDB database dependency.",
                        }
                    ]
                }
            ]
        }
        score = analysis_accuracy_metric(
            _example("code_analyzer"), trajectory
        )
        assert score == pytest.approx(1.0)


# --- Evaluator Tests ---


class TestSDSEvaluatorEvaluate:
    """Test the evaluate() method with a stub evaluator."""

    def test_evaluate_returns_averaged_scores(self):
        trajectory = {
            "deployment": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "args": {"script": "deploy.sh start"},
                            "stdout": "check_prerequisites\nBuilding\n"
                            "Services started up and running",
                            "stderr": "",
                            "exit_code": 0,
                        }
                    ]
                }
            ]
        }
        evaluator = _StubSDSEvaluator(
            metrics={"deployment_progress": deployment_progress_metric},
            trajectory_data=trajectory,
        )
        result = evaluator.evaluate(
            "prompt", "deployer/system.jinja2", [_example()], "c1"
        )
        assert result.candidate_id == "c1"
        assert result.scores["deployment_progress"] > 0
        assert result.overall_score > 0
        assert len(result.traces) == 1

    def test_evaluate_empty_examples(self):
        evaluator = _StubSDSEvaluator(
            metrics={"deployment_progress": deployment_progress_metric},
            trajectory_data={},
        )
        result = evaluator.evaluate(
            "prompt", "deployer/system.jinja2", []
        )
        assert result.overall_score == 0.0
        assert result.traces == []


# --- Efficiency Metrics Tests ---


class TestExtractEfficiencyMetrics:
    """Test extract_efficiency_metrics function."""

    def test_counts_characters_from_all_sources(self):
        """Characters from content, stdout, and stderr are all counted."""
        trajectory = {
            "deployment": [
                {
                    "messages": [
                        {"role": "assistant", "content": "a" * 100},
                        {
                            "role": "tool_call",
                            "stdout": "x" * 40,
                            "stderr": "y" * 60,
                        },
                    ]
                }
            ]
        }
        m = extract_efficiency_metrics(trajectory)
        assert m.total_characters == 200
        assert m.estimated_tokens == 50
        assert m.tool_call_count == 1

    def test_counts_turns_across_all_phases(self):
        """Turns are counted across all phases, not just deployment."""
        trajectory = {
            "deployment": [
                {"messages": [{"role": "assistant", "content": "turn1"}]},
                {"messages": [{"role": "assistant", "content": "turn2"}]},
            ],
            "script_generation": [
                {"messages": [{"role": "assistant", "content": "gen"}]},
            ],
        }
        m = extract_efficiency_metrics(trajectory)
        assert m.turn_count == 3

    @pytest.mark.parametrize(
        "metadata,expected_seconds",
        [
            (
                {"start_time": "2026-01-01 10:00:00", "end_time": "2026-01-01 10:05:30"},
                330.0,
            ),
            ({}, 0.0),
        ],
    )
    def test_wall_clock_parsing(self, metadata, expected_seconds):
        trajectory = {"metadata": metadata}
        m = extract_efficiency_metrics(trajectory)
        assert m.wall_clock_seconds == pytest.approx(expected_seconds)


# --- Improved Deployment Progress Metric Tests ---


class TestDeploymentProgressMetricDockerV2:
    """Test Docker V2 pattern detection in deployment_progress_metric."""

    def test_docker_running_pattern(self):
        """Docker Compose V2 'Running X/Y' output should trigger containers_created."""
        trajectory = {
            "deployment": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "args": {"script": "deploy.sh"},
                            "stdout": "Running 3/5 containers",
                            "stderr": "",
                            "exit_code": 1,
                        }
                    ]
                }
            ]
        }
        score = deployment_progress_metric(_example(), trajectory)
        assert score >= 0.35

    def test_error_density_penalty(self):
        """High error density in stderr should reduce score."""
        clean_trajectory = {
            "deployment": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "args": {"script": "deploy.sh"},
                            "stdout": "Building...\nServices started up and running",
                            "stderr": "",
                            "exit_code": 0,
                        }
                    ]
                }
            ]
        }
        error_trajectory = {
            "deployment": [
                {
                    "messages": [
                        {
                            "role": "tool_call",
                            "args": {"script": "deploy.sh"},
                            "stdout": "Building...\nServices started up and running",
                            "stderr": "error: something\n" * 20,
                            "exit_code": 0,
                        }
                    ]
                }
            ]
        }
        clean_score = deployment_progress_metric(_example(), clean_trajectory)
        error_score = deployment_progress_metric(_example(), error_trajectory)
        assert clean_score > error_score


# --- Metrics Registry Tests ---


class TestMetricsRegistry:
    """Test METRICS_REGISTRY completeness."""

    def test_all_agent_types_registered_with_expected_metrics(self):
        assert set(METRICS_REGISTRY.keys()) == {
            "deployer", "monitor", "code_analyzer"
        }
        assert set(METRICS_REGISTRY["deployer"].keys()) == {
            "script_completeness", "deployment_progress",
            "health_check", "fix_quality",
        }
        assert set(METRICS_REGISTRY["monitor"].keys()) == {
            "health_analysis_depth", "monitoring_coverage",
        }
        assert set(METRICS_REGISTRY["code_analyzer"].keys()) == {
            "analysis_completeness", "analysis_accuracy",
        }
