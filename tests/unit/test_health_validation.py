from app_operator.health_validation import validate_health_check_result
from tests.fixtures.agents import StubAgent


def test_validate_health_heuristic_rejects_contradictory_exit_zero(tmp_path):
    agent = StubAgent(response='{"verdict":"healthy","confidence":0.9,"reason":"looks good"}')
    result = validate_health_check_result(
        health_result={
            "success": True,
            "exit_code": 0,
            "stdout": "[ FAIL ] profile service is not running",
            "stderr": "",
        },
        agent=agent,
        repo_path=tmp_path,
        check_context="unit-test",
        timeout=30,
    )

    assert result.is_healthy is False
    assert result.source == "heuristic"
    assert result.agent_verdict == "skipped"
    assert len(agent.calls) == 0


def test_validate_health_ignores_agent_unhealthy_when_heuristics_pass(tmp_path):
    agent = StubAgent(response='{"verdict":"unhealthy","confidence":0.82,"reason":"compose warning indicates risk"}')
    result = validate_health_check_result(
        health_result={
            "success": True,
            "exit_code": 0,
            "stdout": "All checks passed",
            "stderr": (
                'time="2026-03-08T20:52:29Z" level=warning msg='
                '"/tmp/docker-compose.yml: the attribute `version` is obsolete"'
            ),
        },
        agent=agent,
        repo_path=tmp_path,
        check_context="unit-test",
        timeout=30,
    )

    assert result.is_healthy is True
    assert result.source == "heuristic_override"
    assert result.agent_verdict == "unhealthy"
    assert result.confidence == 0.82
    assert len(agent.calls) == 1


def test_validate_health_falls_back_to_heuristic_on_unparseable_agent_response(tmp_path):
    agent = StubAgent(response="definitely healthy")
    result = validate_health_check_result(
        health_result={
            "success": True,
            "exit_code": 0,
            "stdout": "All checks passed",
            "stderr": "",
        },
        agent=agent,
        repo_path=tmp_path,
        check_context="unit-test",
        timeout=30,
    )

    assert result.is_healthy is True
    assert result.source == "heuristic_fallback"
    assert result.agent_verdict is None
    assert len(agent.calls) == 1
