# pyright: reportPrivateUsage=false
import json
from pathlib import Path

import pytest

from libs.agent_cli.gemini import GeminiGenerationSession


def test_gemini_metadata_uses_sdo_filename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chats_dir = tmp_path / ".gemini" / "tmp" / "application" / "chats"
    chats_dir.mkdir(parents=True)

    def mock_home(_path_type: type[Path]) -> Path:
        return tmp_path

    monkeypatch.setattr(Path, "home", classmethod(mock_home))

    session = object.__new__(GeminiGenerationSession)
    session.call_id = 7
    session.run_id = "experiment"
    session.cwd = "/workspace/application"

    session._write_call_metadata()

    metadata_file = chats_dir / "sdo_call_007.json"
    metadata = json.loads(metadata_file.read_text())
    assert metadata["call_id"] == 7
    assert metadata["run_id"] == "experiment"
    assert metadata["cwd"] == "/workspace/application"
