import time
from pathlib import Path


class AgentflowStorage:
    """Handles storage of generated scripts."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def write_script(self, script_text: str) -> Path:
        """Write the script to a timestamped directory."""
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        run_dir = self.base_dir / timestamp
        run_dir.mkdir(parents=True, exist_ok=True)

        script_path = run_dir / "agentflow.py"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_text)

        return script_path
