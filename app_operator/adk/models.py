import os

from google import genai

from app_operator.config import Config


def build_adk_model(config: Config) -> str:
    """Return an ADK-compatible model handle for LlmAgent.

    For ADK, this is currently just the model name string for Gemini models.
    """
    if not config.agent.model:
        raise ValueError("agent.model must be set for ADK runtime")

    provider = config.agent.provider.lower()

    # Only Gemini/Vertex providers are supported by ADK runtime
    if provider not in ("gemini", "vertex"):
        raise ValueError(f"ADK runtime only supports 'gemini' or 'vertex' providers, got '{provider}'")

    if provider == "vertex":
        # Configure genai.Client to use Vertex by default via monkeypatching
        # because ADK doesn't expose a way to configure the client yet.
        original_init = genai.Client.__init__

        # Check if already patched to avoid recursion/duplication
        if not getattr(genai.Client, "_sds_patched", False):

            def new_init(self, *args, **kwargs):
                if "vertexai" not in kwargs:
                    kwargs["vertexai"] = True
                if "project" not in kwargs and "GOOGLE_CLOUD_PROJECT" in os.environ:
                    kwargs["project"] = os.environ["GOOGLE_CLOUD_PROJECT"]
                if "location" not in kwargs:
                    kwargs["location"] = config.agent.location or "global"
                original_init(self, *args, **kwargs)

            genai.Client.__init__ = new_init
            genai.Client._sds_patched = True  # type: ignore[reportAttributeAccessIssue]

    # Return the model name directly
    return config.agent.model
