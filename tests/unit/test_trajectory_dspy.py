"""Tests for trajectory DSPy integration."""

import json

from app_operator.trajectory import Phase, TrajectoryRecorder


class TestPromptVersionTracking:
    """Tests for prompt version tracking in trajectory."""

    def test_set_prompt_version(self, tmp_path):
        """Should record prompt version."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)

        recorder.set_prompt_version("dspy_v1")

        assert recorder._current_prompt_version == "dspy_v1"

    def test_prompt_version_in_conversation(self, tmp_path):
        """Prompt version should be included in conversation entry."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.add_user_message("Deploy the app")
        recorder.set_prompt_version("dspy_v1")
        recorder.end_phase("success")

        # Check conversation entry
        deployment = recorder.trajectory["deployment"]
        assert len(deployment) == 1
        assert deployment[0]["prompt_version"] == "dspy_v1"

    def test_prompt_version_in_call_record(self, tmp_path):
        """Prompt version should be in call record."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.set_prompt_version("dspy_v1")
        recorder.end_phase("success")

        # Check call record
        calls = recorder.trajectory["calls"]
        assert len(calls) == 1
        assert calls[0]["prompt_version"] == "dspy_v1"

    def test_jinja2_version_tracking(self, tmp_path):
        """Should track jinja2 version."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.set_prompt_version("jinja2")
        recorder.end_phase("success")

        deployment = recorder.trajectory["deployment"]
        assert deployment[0]["prompt_version"] == "jinja2"

    def test_no_version_set(self, tmp_path):
        """If no version set, should not include field."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.add_user_message("Deploy")
        recorder.end_phase("success")

        deployment = recorder.trajectory["deployment"]
        assert "prompt_version" not in deployment[0]


class TestFallbackTracking:
    """Tests for fallback event tracking."""

    def test_record_fallback(self, tmp_path):
        """Should record fallback flag."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)

        recorder.record_fallback()

        assert recorder._fallback_occurred is True

    def test_fallback_in_conversation(self, tmp_path):
        """Fallback should be included in conversation entry."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.record_fallback()
        recorder.end_phase("success")

        deployment = recorder.trajectory["deployment"]
        assert len(deployment) == 1
        assert deployment[0]["fallback_occurred"] is True

    def test_fallback_in_call_record(self, tmp_path):
        """Fallback should be in call record."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.record_fallback()
        recorder.end_phase("success")

        calls = recorder.trajectory["calls"]
        assert len(calls) == 1
        assert calls[0]["fallback_occurred"] is True

    def test_no_fallback(self, tmp_path):
        """If no fallback, should not include field."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.end_phase("success")

        deployment = recorder.trajectory["deployment"]
        assert "fallback_occurred" not in deployment[0]


class TestCombinedTracking:
    """Tests for combined prompt version and fallback tracking."""

    def test_dspy_with_fallback(self, tmp_path):
        """Should record both DSPy attempt and fallback."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.set_prompt_version("jinja2")  # Fell back to jinja2
        recorder.record_fallback()
        recorder.end_phase("success")

        deployment = recorder.trajectory["deployment"]
        assert deployment[0]["prompt_version"] == "jinja2"
        assert deployment[0]["fallback_occurred"] is True

    def test_multiple_phases_different_versions(self, tmp_path):
        """Should track different versions across phases."""
        recorder = TrajectoryRecorder(tmp_path)

        # Phase 1: Use DSPy
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.set_prompt_version("dspy_v1")
        recorder.end_phase("success")

        # Phase 2: Fallback to Jinja2
        recorder.start_phase(Phase.MONITORING)
        recorder.set_prompt_version("jinja2")
        recorder.record_fallback()
        recorder.end_phase("success")

        # Check both phases
        deployment = recorder.trajectory["deployment"]
        monitoring = recorder.trajectory["monitoring"]

        assert deployment[0]["prompt_version"] == "dspy_v1"
        assert "fallback_occurred" not in deployment[0]

        assert monitoring[0]["prompt_version"] == "jinja2"
        assert monitoring[0]["fallback_occurred"] is True

    def test_reset_between_phases(self, tmp_path):
        """Prompt metadata should reset between phases."""
        recorder = TrajectoryRecorder(tmp_path)

        # Phase 1: Use DSPy with fallback
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.set_prompt_version("dspy_v1")
        recorder.record_fallback()
        recorder.end_phase("success")

        # Phase 2: Clean phase (no version set)
        recorder.start_phase(Phase.MONITORING)
        recorder.end_phase("success")

        # Phase 2 should not inherit from Phase 1
        monitoring = recorder.trajectory["monitoring"]
        assert "prompt_version" not in monitoring[0]
        assert "fallback_occurred" not in monitoring[0]


class TestTrajectoryPersistence:
    """Tests for prompt metadata persistence to JSON."""

    def test_version_persisted_to_file(self, tmp_path):
        """Prompt version should persist to trajectory file."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.set_prompt_version("dspy_v2")
        recorder.end_phase("success")

        # Read from file
        trajectory_file = recorder.trajectory_file
        with open(trajectory_file) as f:
            data = json.load(f)

        assert data["deployment"][0]["prompt_version"] == "dspy_v2"
        assert data["calls"][0]["prompt_version"] == "dspy_v2"

    def test_fallback_persisted_to_file(self, tmp_path):
        """Fallback flag should persist to trajectory file."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.record_fallback()
        recorder.end_phase("success")

        # Read from file
        trajectory_file = recorder.trajectory_file
        with open(trajectory_file) as f:
            data = json.load(f)

        assert data["deployment"][0]["fallback_occurred"] is True
        assert data["calls"][0]["fallback_occurred"] is True

    def test_finalize_preserves_metadata(self, tmp_path):
        """Finalize should preserve prompt metadata."""
        recorder = TrajectoryRecorder(tmp_path)
        recorder.start_phase(Phase.DEPLOYMENT)
        recorder.set_prompt_version("dspy_v1")
        recorder.record_fallback()
        recorder.end_phase("success")

        recorder.finalize("completed")

        # Read finalized file
        trajectory_file = recorder.trajectory_file
        with open(trajectory_file) as f:
            data = json.load(f)

        assert data["deployment"][0]["prompt_version"] == "dspy_v1"
        assert data["deployment"][0]["fallback_occurred"] is True
