"""
AST-based architectural boundary tests.

These tests enforce three rules using static analysis of import statements:

  Rule 1 — Façade rule: cross-package imports must go through __init__.py.
            Code in package A must not import from app_operator.B.submodule;
            it must import from app_operator.B directly.

  Rule 2 — Private module rule: _-prefixed submodules are package-private.
            No file outside a package may import from a _-prefixed submodule.

  Rule 3 — Public API rule: every non-trivial subpackage __init__.py must
            declare __all__ to make its public surface explicit.

Layer-ordering rules (which package may import from which) are enforced
separately by import-linter contracts in pyproject.toml.

HOW TO ADD AN ALLOWLIST ENTRY
  Add a (relative_path, imported_module_prefix) tuple to _FACADE_ALLOWLIST.
  Always include a comment explaining WHY the exception is needed.
"""

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_APP_OPERATOR = _REPO_ROOT / "app_operator"
_LEGO_AGENT = _REPO_ROOT / "lego_agent"
_LIBS = _REPO_ROOT / "libs"

# Top-level subpackages (directories with __init__.py) inside app_operator.
_SUBPACKAGES: frozenset[str] = frozenset(
    p.name for p in _APP_OPERATOR.iterdir() if p.is_dir() and (p / "__init__.py").exists()
)

# ---------------------------------------------------------------------------
# Allowlist for Rule 1 (façade rule)
#
# Each entry is a 2-tuple: relative file path and imported module prefix.
#
# Entries here are accepted deviations; every entry must have a comment.
# ---------------------------------------------------------------------------

_FACADE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # ── prompts.* submodules ──────────────────────────────────────────────
        # prompts submodules (deployer.py, deployment_context.py, subagent.py,
        # rlm.py) each import `from app_operator.prompts import get_loader`.
        # Re-exporting those submodules from prompts/__init__.py would create a
        # circular import.  Direct submodule access is therefore intentional.
        (
            "app_operator/adk/operator.py",
            "app_operator.prompts.deployer",
        ),
        (
            "app_operator/adk/operator.py",
            "app_operator.prompts.deployment_context",
        ),
        (
            "app_operator/cli_agent/agents/deployer.py",
            "app_operator.prompts.deployer",
        ),
        (
            "app_operator/cli_agent/agents/deployer.py",
            "app_operator.prompts.deployment_context",
        ),
        (
            "app_operator/cli_agent/hybrid_agent.py",
            "app_operator.prompts.subagent",
        ),
        (
            "app_operator/cli_agent/rlm/recursive_agent.py",
            "app_operator.prompts.rlm",
        ),
        (
            "app_operator/cli_agent/subagent_agent.py",
            "app_operator.prompts.subagent",
        ),
        (
            "app_operator/langgraph/nodes/deployer.py",
            "app_operator.prompts.deployer",
        ),
        (
            "app_operator/langgraph/nodes/deployer.py",
            "app_operator.prompts.deployment_context",
        ),
        (
            "app_operator/langgraph/nodes/generator.py",
            "app_operator.prompts.deployer",
        ),
        (
            "app_operator/langgraph/nodes/generator.py",
            "app_operator.prompts.deployment_context",
        ),
        # ── dspy_integration heavy classes ────────────────────────────────────
        # optimizer.py, signatures.py, metrics_aggregator.py, eval_execute.py
        # all import `dspy` at module level.  Re-exporting them from
        # dspy_integration/__init__.py would cause `import dspy` to fire
        # whenever any code touches the package (e.g. cli_agent loading
        # DSPyConfig).  Direct submodule imports in commands/ preserve the
        # lazy-load behaviour: dspy is only imported when an optimise command
        # actually runs.
        (
            "app_operator/commands/analyze_prompts.py",
            "app_operator.dspy_integration.metrics_aggregator",
        ),
        (
            "app_operator/commands/e2e_optimize.py",
            "app_operator.dspy_integration.eval_execute",
        ),
        (
            "app_operator/commands/e2e_optimize.py",
            "app_operator.dspy_integration.config",
        ),
        (
            "app_operator/commands/optimize_prompts.py",
            "app_operator.dspy_integration.optimizer",
        ),
        (
            "app_operator/commands/optimize_prompts.py",
            "app_operator.dspy_integration.signatures",
        ),
    }
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_py_files(*roots: Path) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(sorted(root.rglob("*.py")))
    return files


def _home_package(filepath: Path) -> str | None:
    """Return the top-level subpackage a file belongs to, or None."""
    try:
        rel = filepath.relative_to(_APP_OPERATOR)
    except ValueError:
        return None
    if len(rel.parts) > 1 and rel.parts[0] in _SUBPACKAGES:
        return rel.parts[0]
    return None


def _from_imports(filepath: Path) -> list[tuple[str, int]]:
    """Return (module_string, lineno) for every `from X import Y` in file."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    return [(node.module, node.lineno) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]


def _fmt(filepath: Path, lineno: int, module: str, reason: str) -> str:
    return f"  {filepath.relative_to(_REPO_ROOT)}:{lineno}  '{module}'  — {reason}"


# ---------------------------------------------------------------------------
# Rule 1: façade rule
# ---------------------------------------------------------------------------


def test_cross_package_imports_go_through_init():
    """
    From outside package X, import app_operator.X — not app_operator.X.submodule.
    The public API lives in __init__.py; internal submodules are an impl detail.
    Exceptions are documented in _FACADE_ALLOWLIST.
    """
    violations: list[str] = []

    for filepath in _iter_py_files(_APP_OPERATOR, _LEGO_AGENT, _LIBS):
        home = _home_package(filepath)
        rel_str = str(filepath.relative_to(_REPO_ROOT))

        for module, lineno in _from_imports(filepath):
            parts = module.split(".")
            # Only care about app_operator cross-package imports
            if parts[0] != "app_operator" or len(parts) < 3:
                continue
            target_pkg = parts[1]
            if target_pkg not in _SUBPACKAGES:
                continue
            # Same-package imports are always fine
            if target_pkg == home:
                continue

            key = (rel_str, module)
            if key not in _FACADE_ALLOWLIST:
                violations.append(
                    _fmt(
                        filepath,
                        lineno,
                        module,
                        f"bypass of {target_pkg}/__init__.py; "
                        f"use 'from app_operator.{target_pkg} import ...' instead, "
                        f"or add to _FACADE_ALLOWLIST with a justification comment",
                    )
                )

    assert not violations, f"{len(violations)} façade violation(s) found:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# Rule 2: private module rule
# ---------------------------------------------------------------------------


def test_no_private_submodule_imports_across_packages():
    """
    Modules named with a leading underscore are package-private.
    No file outside that package may import from them.
    """
    violations: list[str] = []

    for filepath in _iter_py_files(_APP_OPERATOR, _LEGO_AGENT, _LIBS):
        home = _home_package(filepath)

        for module, lineno in _from_imports(filepath):
            parts = module.split(".")
            if parts[0] != "app_operator":
                continue

            # Find the first _-prefixed segment after the root
            private_part = next((p for p in parts[1:] if p.startswith("_")), None)
            if private_part is None:
                continue

            # Determine which package owns the private symbol
            target_pkg = parts[1] if parts[1] in _SUBPACKAGES else None

            if target_pkg != home:
                violations.append(
                    _fmt(
                        filepath,
                        lineno,
                        module,
                        f"'{private_part}' is package-private to '{target_pkg}'",
                    )
                )

    assert not violations, f"{len(violations)} private-module violation(s) found:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# Rule 3: __all__ declaration
# ---------------------------------------------------------------------------

_ALL_EXEMPT: frozenset[str] = frozenset(
    {
        # Empty __init__.py files are fine — they declare no public API
        "app_operator/cli_agent/agents/__init__.py",
        "app_operator/cli_agent/rlm/__init__.py",
    }
)


def test_subpackages_declare_all():
    """
    Every non-trivial subpackage __init__.py must declare __all__ so the
    public surface is explicit and reviewable.
    """
    missing: list[str] = []

    for pkg in sorted(_SUBPACKAGES):
        init = _APP_OPERATOR / pkg / "__init__.py"
        if not init.exists():
            continue
        rel_str = str(init.relative_to(_REPO_ROOT))
        if rel_str in _ALL_EXEMPT:
            continue

        source = init.read_text(encoding="utf-8").strip()
        if not source:
            continue  # genuinely empty — no exports, no __all__ needed

        tree = ast.parse(source)
        has_all = any(
            isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            for node in ast.walk(tree)
        )
        if not has_all:
            missing.append(f"  {rel_str}")

    assert not missing, "__all__ missing from subpackage __init__.py:\n" + "\n".join(missing)
