"""
AST-based architectural boundary tests.

These tests enforce five rules using static analysis of import statements:

  Rule 1 — Façade rule: cross-package imports must go through curated
            façade modules. Code outside a façade package must not import
            from its deeper implementation modules.

  Rule 2 — Private module rule: _-prefixed submodules are package-private.
            No file outside a package's directory may import from a
            _-prefixed submodule defined within it.

  Rule 3 — Public API rule: every non-trivial package __init__.py must
            declare __all__ to make its public surface explicit.

  Rule 4 — Clean exports rule: __all__ must not contain _-prefixed names.
            Private names are implementation details, not public API.

  Rule 5 — Stable façade rule: selected small library facades must keep an
            explicit, reviewed export set to prevent helper creep.

Layer-ordering rules (which package may import from which) are enforced
separately by import-linter contracts in pyproject.toml.
"""

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Project package registry — single source of truth for all rules.
# Maps the importable top-level name to its source directory.
# Add a new entry here whenever a new top-level project package is introduced.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent

_PROJECT_PACKAGES: dict[str, Path] = {
    "app_operator": _REPO_ROOT / "app_operator",
    "app_operator_dspy": _REPO_ROOT / "app_operator_dspy",
    "lego_agent": _REPO_ROOT / "lego_agent",
    "libs": _REPO_ROOT / "libs",
    "sregym_agents": _REPO_ROOT / "sregym_agents",
}

_APP_OPERATOR = _PROJECT_PACKAGES["app_operator"]
_APP_OPERATOR_SUBPACKAGES: frozenset[str] = frozenset(
    p.name for p in _APP_OPERATOR.iterdir() if p.is_dir() and (p / "__init__.py").exists()
)
_APP_OPERATOR_FACADES: frozenset[str] = frozenset(f"app_operator.{pkg}" for pkg in _APP_OPERATOR_SUBPACKAGES)

_SREGYM_AGENT_ROOTS: frozenset[str] = frozenset({"cli_agent", "crucible", "fault_verifier"})
_SREGYM_AGENT_FACADES: frozenset[str] = frozenset(f"sregym_agents.{pkg}" for pkg in _SREGYM_AGENT_ROOTS)

# Additional packages that already behave like explicit public facades and can
# realistically be kept import-clean today.
_EXPLICIT_FACADES: frozenset[str] = frozenset(
    {
        "libs.agent_mw",
        "libs.llm_rt",
        "libs.llm_rt.litellm",
        "libs.model_config",
        "libs.pydantic_agent",
    }
)
_FACADE_MODULES: frozenset[str] = _APP_OPERATOR_FACADES | _SREGYM_AGENT_FACADES | _EXPLICIT_FACADES

_ALL_EXEMPT: frozenset[str] = frozenset(
    {
        # Explicitly internal-only package markers.
        "app_operator/cli_agent/agents/__init__.py",
        "app_operator/cli_agent/rlm/__init__.py",
    }
)

_STABLE_FACADE_EXPORTS: dict[str, frozenset[str]] = {
    "libs/agent_mw/__init__.py": frozenset(
        {
            "FixedPathProvider",
            "LoopDetectionMiddleware",
            "RetryMiddleware",
            "SearchPriorMitigationsReminderMiddleware",
            "SoftLimitExtension",
            "StallDetectionMiddleware",
            "ThinkingRepetitionMiddleware",
            "TimeoutMiddleware",
            "TrajectoryMiddleware",
            "TrajectoryPathProvider",
            "TurnLoggingMiddleware",
        }
    ),
    "libs/llm_rt/__init__.py": frozenset({"LiteLLMClient", "litellm_call_with_retry"}),
    "libs/model_config/__init__.py": frozenset(
        {"ModelConfig", "from_provider_and_model", "from_string", "normalize_provider"}
    ),
    "libs/pydantic_agent/__init__.py": frozenset(
        {"AgentMiddleware", "BaseAgent", "InlineAgent", "TokenUsage", "UsageCollector", "thinking_settings"}
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_py_files(*roots: Path) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(sorted(root.rglob("*.py")))
    return files


def _from_imports(filepath: Path) -> list[tuple[str, int]]:
    """Return (module_string, lineno) for every `from X import Y` in file."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    return [(node.module, node.lineno) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]


def _fmt(filepath: Path, lineno: int, module: str, reason: str) -> str:
    return f"  {filepath.relative_to(_REPO_ROOT)}:{lineno}  '{module}'  — {reason}"


def _module_dir(module: str) -> Path | None:
    parts = module.split(".")
    root = _PROJECT_PACKAGES.get(parts[0])
    if root is None:
        return None
    return root.joinpath(*parts[1:])


def _matching_facade(module: str) -> str | None:
    if module in _FACADE_MODULES:
        return None
    matches = sorted((facade for facade in _FACADE_MODULES if module.startswith(f"{facade}.")), key=len, reverse=True)
    return matches[0] if matches else None


def _private_owner_dir(module: str) -> Path | None:
    """
    Return the directory that owns the first _-prefixed segment in a dotted
    module path, or None if the module is not a project module or has no
    private segment.

    The owner is the immediate parent directory of the _-prefixed segment:
      "libs.agent_mw._turn_logger"  →  <root>/libs/agent_mw/
      "app_operator.commands._foo"  →  <root>/app_operator/commands/
      "sregym_agents.crucible._bar" →  <root>/sregym_agents/crucible/
    """
    parts = module.split(".")
    root = _PROJECT_PACKAGES.get(parts[0])
    if root is None:
        return None
    for i, part in enumerate(parts[1:], 1):
        if part.startswith("_"):
            return root.joinpath(*parts[1:i]) if i > 1 else root
    return None


def _has_all(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
        for node in ast.walk(tree)
    )


def _literal_all_values(tree: ast.AST) -> set[str] | None:
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            continue
        values: set[str] = set()
        for elt in node.value.elts:
            if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
                return None
            values.add(elt.value)
        return values
    return None


def _is_trivial_init(tree: ast.Module) -> bool:
    body = tree.body
    return not body or (
        len(body) == 1
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )


# ---------------------------------------------------------------------------
# Rule 1: façade rule
# ---------------------------------------------------------------------------


def test_cross_package_imports_go_through_init():
    """
    From outside a curated façade package, import the façade — not deeper
    implementation modules. The public API lives in __init__.py.
    """
    violations: list[str] = []

    for filepath in _iter_py_files(*_PROJECT_PACKAGES.values()):
        for module, lineno in _from_imports(filepath):
            facade = _matching_facade(module)
            if facade is None:
                continue
            owner_dir = _module_dir(facade)
            if owner_dir is None:
                continue

            try:
                filepath.relative_to(owner_dir)
                continue
            except ValueError:
                pass

            violations.append(
                _fmt(
                    filepath,
                    lineno,
                    module,
                    f"bypass of {facade}/__init__.py; use 'from {facade} import ...' instead",
                )
            )

    assert not violations, f"{len(violations)} façade violation(s) found:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# Rule 2: private module rule
# ---------------------------------------------------------------------------


def test_no_private_submodule_imports_across_packages():
    """
    Modules named with a leading underscore are private to their parent directory.
    No file outside that directory may import from them.
    """
    violations: list[str] = []

    for filepath in _iter_py_files(*_PROJECT_PACKAGES.values()):
        for module, lineno in _from_imports(filepath):
            owner_dir = _private_owner_dir(module)
            if owner_dir is None:
                continue

            try:
                filepath.relative_to(owner_dir)
            except ValueError:
                parts = module.split(".")
                private_part = next(p for p in parts[1:] if p.startswith("_"))
                violations.append(
                    _fmt(
                        filepath,
                        lineno,
                        module,
                        f"'{private_part}' is private to '{owner_dir.relative_to(_REPO_ROOT)}'",
                    )
                )

    assert not violations, f"{len(violations)} private-module violation(s) found:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# Rule 3: __all__ declaration
# ---------------------------------------------------------------------------


def test_subpackages_declare_all():
    """
    Every non-trivial package __init__.py must declare __all__ so the
    public surface is explicit and reviewable.
    """
    missing: list[str] = []

    for root in _PROJECT_PACKAGES.values():
        for init in sorted(root.rglob("__init__.py")):
            rel_str = str(init.relative_to(_REPO_ROOT))
            if rel_str in _ALL_EXEMPT:
                continue

            tree = ast.parse(init.read_text(encoding="utf-8"))
            if _is_trivial_init(tree):
                continue
            if not _has_all(tree):
                missing.append(f"  {rel_str}")

    assert not missing, "__all__ missing from package __init__.py:\n" + "\n".join(missing)


# ---------------------------------------------------------------------------
# Rule 4: no private names in __all__
# ---------------------------------------------------------------------------


def test_all_does_not_export_private_names():
    """
    __all__ must not contain names starting with '_'.
    Private names are implementation details and should not be part of the
    public API surface.
    """
    violations: list[str] = []

    for root in _PROJECT_PACKAGES.values():
        for init in sorted(root.rglob("__init__.py")):
            try:
                tree = ast.parse(init.read_text(encoding="utf-8"))
            except SyntaxError:
                continue

            all_values = _literal_all_values(tree)
            if all_values is None:
                continue
            violations.extend(
                f"  {init.relative_to(_REPO_ROOT)}  '{name}'  — private name in __all__"
                for name in sorted(all_values)
                if name.startswith("_")
            )

    assert not violations, f"{len(violations)} private-export violation(s) found:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# Rule 5: stable façade exports
# ---------------------------------------------------------------------------


def test_stable_facades_keep_reviewed_export_sets():
    """
    Small shared-library facades should keep an explicit, reviewed export set.
    This prevents helper functions from quietly becoming public API.
    """
    mismatches: list[str] = []

    for rel_path, expected in sorted(_STABLE_FACADE_EXPORTS.items()):
        init = _REPO_ROOT / rel_path
        tree = ast.parse(init.read_text(encoding="utf-8"))
        actual = _literal_all_values(tree)
        if actual is None:
            mismatches.append(f"  {rel_path}  — __all__ must be a literal list/tuple of strings")
            continue
        if actual != expected:
            mismatches.append(f"  {rel_path}  — expected {sorted(expected)!r}, found {sorted(actual)!r}")

    assert not mismatches, "stable façade export set mismatch(es):\n" + "\n".join(mismatches)
