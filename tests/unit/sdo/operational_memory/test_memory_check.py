from __future__ import annotations

from typing import TYPE_CHECKING

from sdo.operational_memory.memory_check import main
from tests.unit.sdo.operational_memory.test_memory import _git, _init_repository, _write_memory

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_PLAYBOOK = """---
schema_version: 1
owner: responder
fault_class: failed-mount
originating_incident: "inc-1"
originating_commit: "outcome-sha"
---
# Failed mount

Restore `{placeholder}` and restart `<DEPLOYMENT>`.
"""


def _repository(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    app.mkdir()
    _write_memory(app)
    _init_repository(app)
    return app


def _propose_playbook(app: Path, *, placeholder: str = "<CONFIG_MAP>", link: bool = True) -> None:
    playbook = app / ".sdo" / "playbooks" / "failed-mount" / "README.md"
    playbook.parent.mkdir(parents=True)
    playbook.write_text(_PLAYBOOK.format(placeholder=placeholder), encoding="utf-8")
    if link:
        index = app / ".sdo" / "playbooks" / "README.md"
        index.write_text(
            index.read_text(encoding="utf-8") + "- [Failed mount](failed-mount/README.md)\n", encoding="utf-8"
        )


def test_memory_check_accepts_a_valid_uncommitted_proposal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    app = _repository(tmp_path)
    _propose_playbook(app)

    assert main(["--app", str(app), "--actor", "responder"]) == 0

    out = capsys.readouterr().out
    assert "OK" in out
    assert ".sdo/playbooks/failed-mount/README.md" in out


def test_memory_check_rejects_a_playbook_missing_from_the_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app = _repository(tmp_path)
    _propose_playbook(app, link=False)

    assert main(["--app", str(app), "--actor", "responder"]) == 1

    err = capsys.readouterr().err
    assert "playbook is missing from index" in err
    assert "failed-mount/README.md" in err
    # Actionable: the fix is spelled out.
    assert ".sdo/playbooks/README.md" in err


def test_memory_check_rejects_a_lowercase_placeholder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    app = _repository(tmp_path)
    _propose_playbook(app, placeholder="<config_map>")
    # The body's other placeholder would satisfy the rule; drop it.
    playbook = app / ".sdo" / "playbooks" / "failed-mount" / "README.md"
    playbook.write_text(playbook.read_text(encoding="utf-8").replace("<DEPLOYMENT>", "<deployment>"), encoding="utf-8")

    assert main(["--app", str(app), "--actor", "responder"]) == 1

    err = capsys.readouterr().err
    assert "role placeholder" in err
    assert "<NAMESPACE>" in err


def test_memory_check_rejects_edits_outside_the_actor_ownership(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app = _repository(tmp_path)
    goal = app / ".sdo" / "goal.md"
    goal.write_text(goal.read_text(encoding="utf-8") + "weakened\n", encoding="utf-8")

    assert main(["--app", str(app), "--actor", "responder"]) == 1
    assert "does not own .sdo/goal.md" in capsys.readouterr().err


def test_memory_check_uses_the_committed_baseline_for_manifest_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app = _repository(tmp_path)
    manifest = app / ".sdo" / "diagnostics" / "manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("originatingIncident: incident-seed", "originatingIncident: new"),
        encoding="utf-8",
    )

    assert main(["--app", str(app), "--actor", "responder"]) == 1
    assert "may not rewrite provenance" in capsys.readouterr().err


def test_memory_check_with_no_memory_changes_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    app = _repository(tmp_path)
    (app / "notes.txt").write_text("not memory\n", encoding="utf-8")

    assert main(["--app", str(app)]) == 0
    assert "no uncommitted .sdo changes" in capsys.readouterr().out


def test_memory_check_includes_commits_after_an_explicit_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app = _repository(tmp_path)
    baseline = _git(app, "rev-parse", "HEAD")
    _propose_playbook(app, link=False)
    _git(app, "add", ".sdo")
    _git(app, "commit", "-m", "committed invalid playbook")

    assert main(["--app", str(app)]) == 0
    capsys.readouterr()
    assert main(["--app", str(app), "--baseline", baseline]) == 1
    assert "playbook is missing from index" in capsys.readouterr().err


def test_memory_check_accepts_a_playbook_step_that_uses_kubectl_exec(tmp_path: Path) -> None:
    """The responder Role grants pods/exec (exec parity), so the check no longer rejects it."""
    app = _repository(tmp_path)
    _propose_playbook(app)
    playbook = app / ".sdo" / "playbooks" / "failed-mount" / "README.md"
    playbook.write_text(
        playbook.read_text(encoding="utf-8") + "\n    kubectl -n <NAMESPACE> exec deploy/<DEPLOYMENT> -- wget -qO- /\n",
        encoding="utf-8",
    )

    assert main(["--app", str(app), "--actor", "responder"]) == 0
