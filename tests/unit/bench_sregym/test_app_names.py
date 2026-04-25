from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_ROOT = REPO_ROOT / "bench" / "sregym"
APP_NAMES_PATH = BENCH_ROOT / "sregym" / "service" / "apps" / "app_names.py"

if not APP_NAMES_PATH.exists():
    pytest.skip(
        "bench/sregym submodule not checked out — skipping app_names tests",
        allow_module_level=True,
    )

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

app_names = importlib.import_module("sregym.service.apps.app_names")


def test_resolve_cli_app_name_supports_train_ticket_alias():
    assert app_names.resolve_cli_app_name("train_ticket") == "Train Ticket"
