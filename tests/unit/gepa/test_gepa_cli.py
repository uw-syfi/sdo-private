"""Tests for GEPA CLI (app_operator.gepa.cli)."""

import json

import pytest

from app_operator.gepa.cli import _apply_best, build_parser, main


class TestBuildParser:
    """Test CLI argument parser construction."""

    def test_default_values_are_none(self):
        """CLI args default to None so sds.toml values take precedence."""
        parser = build_parser()
        args = parser.parse_args(
            [
                "--agent-type",
                "deployer",
                "--test-repos",
                "apps/test",
            ]
        )
        assert args.max_steps is None
        assert args.num_candidates is None
        assert args.patience is None
        assert args.diversity is None
        assert args.reflection_provider is None
        assert args.reflection_model is None
        assert args.output_dir is None
        assert args.seed is None
        assert args.dry_run is False


class TestMainFunction:
    """Test the main() entry point."""

    def test_requires_agent_or_template(self):
        with pytest.raises(SystemExit):
            main(["--test-repos", "apps/test"])

    def test_apply_best_missing_dir(self):
        result = main(["--apply-best", "/tmp/nonexistent_gepa_run"])
        assert result == 1

    def test_resume_missing_dir(self):
        result = main(
            [
                "--resume",
                "/tmp/nonexistent_gepa_run",
                "--test-repos",
                "apps/test",
            ]
        )
        assert result == 1

    def test_dry_run_returns_zero(self):
        result = main(
            [
                "--agent-type",
                "deployer",
                "--test-repos",
                "apps/test",
                "--dry-run",
            ]
        )
        assert result == 0


class TestApplyBest:
    """Test _apply_best() success path."""

    def test_applies_valid_results_to_templates(self, tmp_path, monkeypatch):
        """Valid results file → template is written and returns 0."""
        run_dir = tmp_path / "gepa_run"
        run_dir.mkdir()

        results = {
            "template_name": "deployer/system.jinja2",
            "best_prompt": "Deploy on {{ platform }} optimized",
            "best_score": 0.95,
        }
        with open(run_dir / "results_deployer_system.jinja2.json", "w") as f:
            json.dump(results, f)

        templates_dir = tmp_path / "templates"
        (templates_dir / "deployer").mkdir(parents=True)
        (templates_dir / "deployer" / "system.jinja2").write_text("Deploy on {{ platform }}.")

        from app_operator.gepa.adapter import SDSPromptAdapter

        monkeypatch.setattr(
            SDSPromptAdapter,
            "__init__",
            lambda self: setattr(self, "templates_dir", templates_dir),
        )

        result = _apply_best(str(run_dir))
        assert result == 0
        written = (templates_dir / "deployer" / "system.jinja2").read_text()
        assert written == "Deploy on {{ platform }} optimized"
