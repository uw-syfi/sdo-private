"""Tests for structured output models."""

from app_operator.pydantic_ai._responses import FixSummaryResponse, HealthVerdictResponse


def test_fix_summary_response():
    resp = FixSummaryResponse(summary="Fixed port config")
    assert resp.summary == "Fixed port config"


def test_health_verdict_response_healthy():
    resp = HealthVerdictResponse(
        healthy=True,
        assessment="All services running",
        diagnosis="",
        script_was_fixed=False,
    )
    assert resp.healthy is True
    assert resp.diagnosis == ""


def test_health_verdict_response_unhealthy():
    resp = HealthVerdictResponse(
        healthy=False,
        assessment="Service down",
        diagnosis="Port 8080 not listening",
        script_was_fixed=True,
    )
    assert resp.healthy is False
    assert resp.script_was_fixed is True


def test_health_verdict_response_model_dump():
    resp = HealthVerdictResponse(
        healthy=True,
        assessment="ok",
        diagnosis="",
        script_was_fixed=False,
    )
    d = resp.model_dump()
    assert d == {
        "healthy": True,
        "assessment": "ok",
        "diagnosis": "",
        "script_was_fixed": False,
    }
