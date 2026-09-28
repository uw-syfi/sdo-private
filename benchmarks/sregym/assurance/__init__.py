"""No-LLM assurance of SDO's incident path: a scripted Codex CLI, scripted scenarios, chaos and soak.

``scripted_codex`` is a deterministic stand-in for the ``codex`` binary. It is
installed only into test images (``Dockerfile.scripted``), in place of the real
CLI, so every layer above the model runs for real: agentshim, SDO's structured
turns and usage accounting, the responder Job, the Go controller and its
closure gate, the commit broker, reflection, validation, receipts and the fast
loop's run records. Production code never imports this package.
"""
