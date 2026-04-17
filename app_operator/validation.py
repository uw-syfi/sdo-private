"""Shared validation utilities for config dataclasses.

Provides reusable functions for the isinstance -> TypeError,
value check -> ValueError pattern used across all config dataclasses.
"""

from collections.abc import Collection
from dataclasses import fields
from typing import Any

from app_operator.exceptions import UnrecognizedFieldError


def validate_type(
    value: Any,
    name: str,
    expected_type: type | tuple[type, ...],
    *,
    nullable: bool = False,
    type_label: str | None = None,
) -> None:
    """Validate that *value* is an instance of *expected_type*.

    Args:
        value: The value to check.
        name: Human-readable field name (used in error messages).
        expected_type: One or more types that are acceptable.
        nullable: If ``True``, ``None`` is also accepted.
        type_label: Custom label for the expected type in the error message.
            If ``None``, a label is derived from the type names.

    Raises:
        TypeError: If the value does not match.
    """
    if nullable and value is None:
        return

    if isinstance(value, expected_type):
        return

    # Build a friendly type label
    if type_label is not None:
        label = type_label
    elif isinstance(expected_type, tuple):
        label = "/".join(t.__name__ for t in expected_type)
    else:
        label = expected_type.__name__

    if nullable:
        label = f"{label} or None"

    raise TypeError(f"{name} must be {label}, got {type(value).__name__}")


def validate_positive(value: Any, name: str) -> None:
    """Raise ``ValueError`` if *value* is not positive (> 0)."""
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")


def validate_non_negative(value: Any, name: str) -> None:
    """Raise ``ValueError`` if *value* is negative (< 0)."""
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")


def validate_range(
    value: Any,
    name: str,
    *,
    min_val: float | None = None,
    max_val: float | None = None,
    min_exclusive: bool = False,
    max_exclusive: bool = False,
) -> None:
    """Validate that *value* falls within [min_val, max_val].

    Use *min_exclusive* / *max_exclusive* for open boundaries.
    """
    # Determine if the value is out of range
    below = False
    above = False

    if min_val is not None:
        if min_exclusive:
            below = value <= min_val
        else:
            below = value < min_val

    if max_val is not None:
        if max_exclusive:
            above = value >= max_val
        else:
            above = value > max_val

    if not below and not above:
        return

    # Build the error message
    if min_val is not None and max_val is not None:
        left = "(" if min_exclusive else "["
        right = ")" if max_exclusive else "]"
        raise ValueError(f"{name} must be in range {left}{min_val}, {max_val}{right}, got {value}")
    elif min_val is not None:
        op = ">" if min_exclusive else ">="
        raise ValueError(f"{name} must be {op} {min_val}, got {value}")
    else:
        op = "<" if max_exclusive else "<="
        raise ValueError(f"{name} must be {op} {max_val}, got {value}")


def validate_in(
    value: Any,
    name: str,
    valid_values: Collection,
) -> None:
    """Raise ``ValueError`` if *value* is not in *valid_values*."""
    if value not in valid_values:
        raise ValueError(f"Invalid {name}: '{value}'. Valid {name}s: {', '.join(sorted(str(v) for v in valid_values))}")


def validate_non_empty_str(value: Any, name: str) -> None:
    """Raise ``ValueError`` if *value* is not a non-empty (stripped) string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got '{value}'")


def validate_field(
    value: Any,
    name: str,
    expected_type: type | tuple[type, ...],
    *,
    nullable: bool = False,
    positive: bool = False,
    non_negative: bool = False,
    min_val: float | None = None,
    max_val: float | None = None,
    min_exclusive: bool = False,
    max_exclusive: bool = False,
    valid_values: Collection | None = None,
    non_empty_str: bool = False,
) -> None:
    """All-in-one field validator combining type + value checks.

    This is the main entry point for simple fields.  For more complex
    validation (e.g. metric_weights dict) use the lower-level helpers.
    """
    validate_type(value, name, expected_type, nullable=nullable)

    if nullable and value is None:
        return

    if non_empty_str:
        validate_non_empty_str(value, name)

    if valid_values is not None:
        validate_in(value, name, valid_values)

    if positive:
        validate_positive(value, name)

    if non_negative:
        validate_non_negative(value, name)

    if min_val is not None or max_val is not None:
        validate_range(
            value,
            name,
            min_val=min_val,
            max_val=max_val,
            min_exclusive=min_exclusive,
            max_exclusive=max_exclusive,
        )


def validate_dataclass_fields(
    section_data: dict,
    section_name: str,
    config_class: type,
) -> None:
    """Check that all keys in *section_data* are recognised dataclass fields.

    Raises:
        UnrecognizedFieldError: when unknown keys are found.
    """
    if not section_data:
        return

    recognized = {f.name for f in fields(config_class) if f.init}
    unrecognized = set(section_data.keys()) - recognized

    if unrecognized:
        raise UnrecognizedFieldError(
            f"Unrecognized field(s) in [{section_name}] section: "
            f"{', '.join(sorted(unrecognized))}. "
            f"Recognized fields are: {', '.join(sorted(recognized))}"
        )
