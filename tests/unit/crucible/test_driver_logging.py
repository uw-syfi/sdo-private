"""Regression tests for driver module logging side-effects at import time.

See GitLab issue #95: importing sregym_agents.crucible.driver must not mutate
the root logger or the httpx logger as a side effect. Global logging
configuration belongs inside main().
"""

from __future__ import annotations

import importlib
import logging
import sys


def _reimport_driver():
    """Force a fresh import of driver so module-top-level code re-runs."""
    name = "sregym_agents.crucible.driver"
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


def test_import_does_not_touch_root_logger():
    """Importing the driver module must not call basicConfig or change root level."""
    root = logging.getLogger()
    # Snapshot pre-import state. We cannot assume the test runner leaves the
    # root logger pristine, so we compare before/after rather than to NOTSET.
    original_level = root.level
    original_handlers = list(root.handlers)

    _reimport_driver()

    assert root.level == original_level, (
        f"Importing driver must not change the root logger level (was {original_level}, now {root.level})."
    )
    assert root.handlers == original_handlers, "Importing driver must not add or remove root logger handlers."


def test_import_does_not_silence_httpx_logger():
    """Importing the driver module must not set httpx logger to WARNING."""
    httpx_logger = logging.getLogger("httpx")
    original_level = httpx_logger.level

    _reimport_driver()

    assert httpx_logger.level == original_level, (
        f"Importing driver must not mutate the httpx logger level (was {original_level}, now {httpx_logger.level})."
    )


def test_module_logger_still_exposed():
    """The module-level `logger = getLogger(__name__)` must remain accessible."""
    driver = _reimport_driver()
    assert hasattr(driver, "logger")
    assert driver.logger.name == "sregym_agents.crucible.driver"


def test_setup_logging_helper_configures_logging():
    """_setup_logging() must apply the INFO-level basicConfig + httpx=WARNING."""
    driver = _reimport_driver()
    assert hasattr(driver, "_setup_logging"), "driver must expose a _setup_logging() helper that main() calls."

    root = logging.getLogger()
    httpx_logger = logging.getLogger("httpx")
    original_root_level = root.level
    original_root_handlers = list(root.handlers)
    original_httpx_level = httpx_logger.level

    try:
        driver._setup_logging()
        assert httpx_logger.level == logging.WARNING
        # basicConfig is a no-op if the root logger already has handlers, so
        # we can only assert that httpx was silenced and that _setup_logging
        # runs without raising. The handler-install behaviour is validated
        # indirectly via the --help smoke check in the MR description.
    finally:
        # Restore prior state so other tests aren't affected.
        root.setLevel(original_root_level)
        root.handlers = original_root_handlers
        httpx_logger.setLevel(original_httpx_level)
