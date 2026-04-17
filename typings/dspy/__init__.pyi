"""Pyright stub for dspy.

DSPy 3.x ships no `py.typed` marker and most of its public API is
`**kwargs`-shaped, so pyright produces a flood of `Unknown` errors in
strict mode. This stub treats the entire `dspy` namespace as `Any`
via PEP 562 `__getattr__`, suppressing the cascade at a single point.

If/when DSPy ships proper types, delete this file.
"""

from typing import Any

def __getattr__(name: str) -> Any: ...
