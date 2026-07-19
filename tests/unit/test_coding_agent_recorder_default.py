from libs.agent_cli.base import CodingAgent
from libs.agent_cli.trajectory import NullTrajectoryRecorder


class _ConcreteAgent(CodingAgent):
    def generate(
        self,
        prompt: str,
        cwd: str | None = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        del prompt, cwd, timeout, silent
        return ""


def test_recorder_default_without_assignment():
    agent = _ConcreteAgent()
    assert isinstance(agent.recorder, NullTrajectoryRecorder)
