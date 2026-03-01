"""Test fixtures and utilities for SDS Operator tests."""

from types import MethodType


def bind_method(obj, name, func):
    """Bind *func* as an instance method on *obj* under *name*.

    Useful for replacing methods on test doubles without full mocking.
    """
    setattr(obj, name, MethodType(func, obj))
