"""Test double implementations of CodingAgent for testing.

These test doubles provide controlled behavior for testing different
scenarios without requiring actual agent execution.
"""

import time
from typing import List, Dict, Any, Optional
from app_operator.cli_agent.base import CodingAgent


class StubAgent(CodingAgent):
    """Minimal agent that returns stub responses.

    Use this for tests that need an agent but don't care about its behavior.
    """

    def generate(self, prompt: str, **kwargs) -> str:
        """Return a stub response.

        Args:
            prompt: The prompt (ignored).
            **kwargs: Additional arguments (ignored).

        Returns:
            str: A simple stub response.
        """
        return "stub response"


class ErrorAgent(CodingAgent):
    """Agent that always raises errors.

    Use this to test error handling paths.
    """

    def __init__(self, error_message: str = "Simulated agent error"):
        """Initialize the error agent.

        Args:
            error_message: The error message to raise.
        """
        self.error_message = error_message

    def generate(self, prompt: str, **kwargs) -> str:
        """Raise a RuntimeError.

        Args:
            prompt: The prompt (ignored).
            **kwargs: Additional arguments (ignored).

        Raises:
            RuntimeError: Always raised with the configured error message.
        """
        raise RuntimeError(self.error_message)


class TimeoutAgent(CodingAgent):
    """Agent that simulates timeouts.

    Use this to test timeout handling.
    """

    def __init__(self, sleep_duration: float = 999999):
        """Initialize the timeout agent.

        Args:
            sleep_duration: How long to sleep before returning.
        """
        self.sleep_duration = sleep_duration

    def generate(self, prompt: str, timeout: int = 300, **kwargs) -> str:
        """Sleep longer than the timeout.

        Args:
            prompt: The prompt (ignored).
            timeout: The timeout value (used to sleep longer).
            **kwargs: Additional arguments (ignored).

        Returns:
            str: A response (but timeout should occur first).
        """
        time.sleep(self.sleep_duration)
        return "too late"


class TrackingAgent(CodingAgent):
    """Agent that tracks all calls for verification.

    Use this to verify agent interactions without relying on mocks.
    """

    def __init__(self, response: str = "tracking response"):
        """Initialize the tracking agent.

        Args:
            response: The response to return for all calls.
        """
        self.calls: List[Dict[str, Any]] = []
        self.fix_request_count = 0
        self.generation_count = 0
        self.response = response

    def generate(self, prompt: str, **kwargs) -> str:
        """Track the call and return a response.

        Args:
            prompt: The prompt to track.
            **kwargs: Additional arguments to track.

        Returns:
            str: The configured response.
        """
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        self.generation_count += 1

        if "fix" in prompt.lower():
            self.fix_request_count += 1

        return self.response

    def reset(self):
        """Reset all tracking state."""
        self.calls.clear()
        self.fix_request_count = 0
        self.generation_count = 0


class ConfigurableAgent(CodingAgent):
    """Agent with configurable responses for different scenarios.

    Use this for complex test scenarios that need different responses
    for different prompts.
    """

    def __init__(self):
        """Initialize the configurable agent."""
        self.responses: Dict[str, str] = {}
        self.default_response = "default response"
        self.calls: List[Dict[str, Any]] = []

    def set_response(self, keyword: str, response: str):
        """Configure a response for prompts containing a keyword.

        Args:
            keyword: Keyword to match in the prompt.
            response: Response to return when keyword is found.
        """
        self.responses[keyword.lower()] = response

    def set_default_response(self, response: str):
        """Set the default response for unmatched prompts.

        Args:
            response: The default response.
        """
        self.default_response = response

    def generate(self, prompt: str, **kwargs) -> str:
        """Return a configured response based on the prompt.

        Args:
            prompt: The prompt to analyze.
            **kwargs: Additional arguments.

        Returns:
            str: A response matching the prompt, or the default.
        """
        self.calls.append({"prompt": prompt, "kwargs": kwargs})

        # Check for matching keywords
        prompt_lower = prompt.lower()
        for keyword, response in self.responses.items():
            if keyword in prompt_lower:
                return response

        return self.default_response


class ScriptGeneratingAgent(CodingAgent):
    """Agent that simulates script generation.

    This agent creates actual script files when asked, useful for
    integration testing the deployment flow.
    """

    def __init__(self, generate_valid_scripts: bool = True):
        """Initialize the script generating agent.

        Args:
            generate_valid_scripts: If True, generate working scripts.
                                   If False, generate broken scripts.
        """
        self.generate_valid_scripts = generate_valid_scripts
        self.calls: List[str] = []

    def generate(self, prompt: str, cwd: Optional[str] = None, **kwargs) -> str:
        """Generate scripts based on the prompt.

        Args:
            prompt: The prompt requesting script generation.
            cwd: Working directory for script creation.
            **kwargs: Additional arguments.

        Returns:
            str: A response indicating script generation.
        """
        self.calls.append(prompt)

        if cwd is None:
            return "No working directory specified"

        from pathlib import Path

        cwd_path = Path(cwd)
        sds_dir = cwd_path / ".sds"

        # Create .sds directory if needed
        sds_dir.mkdir(exist_ok=True)

        if "deploy.sh" in prompt.lower():
            deploy_script = sds_dir / "deploy.sh"
            if self.generate_valid_scripts:
                deploy_script.write_text(
                    "#!/bin/bash\necho 'Deployment successful'\nexit 0\n"
                )
            else:
                deploy_script.write_text(
                    "#!/bin/bash\necho 'Deployment failed'\nexit 1\n"
                )
            deploy_script.chmod(0o755)

        if "health_check.sh" in prompt.lower():
            health_script = sds_dir / "health_check.sh"
            if self.generate_valid_scripts:
                health_script.write_text(
                    "#!/bin/bash\necho 'Health check passed'\nexit 0\n"
                )
            else:
                health_script.write_text(
                    "#!/bin/bash\necho 'Health check failed'\nexit 1\n"
                )
            health_script.chmod(0o755)

        return "Scripts generated successfully"
