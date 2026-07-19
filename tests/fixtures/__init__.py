"""Test fixtures and utilities for SDO tests."""

from collections.abc import Callable
from types import MethodType
from typing import Any


def bind_method(obj: object, name: str, func: Callable[..., Any]) -> None:
    """Bind *func* as an instance method on *obj* under *name*.

    Useful for replacing methods on test doubles without full mocking.
    """
    setattr(obj, name, MethodType(func, obj))
