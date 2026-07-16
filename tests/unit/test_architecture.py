"""
AST-based architectural boundary tests.

These tests enforce four rules using static analysis of import statements:

  Rule 1 — Façade rule: cross-package imports must go through __init__.py.
            Code in package A must not import from app_operator.B.submodule;
            it must import from app_operator.B directly.

  Rule 2 — Private module rule: _-prefixed submodules are package-private.
            No file outside a package's directory may import from a
            _-prefixed submodule defined within it.

  Rule 3 — Public API rule: every non-trivial subpackage __init__.py must
            declare __all__ to make its public surface explicit.

  Rule 4 — Clean exports rule: __all__ must not contain _-prefixed names.
            Private names are implementation details, not public API.

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
    "libs": _REPO_ROOT / "libs",
    "sregym_agents": _REPO_ROOT / "sregym_agents",
}

# Top-level subpackages inside app_operator (used by Rules 1 and 3).
_APP_OPERATOR = _PROJECT_PACKAGES["app_operator"]
_SUBPACKAGES: frozenset[str] = frozenset(
    p.name for p in _APP_OPERATOR.iterdir() if p.is_dir() and (p / "__init__.py").exists()
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
    """Return the app_operator subpackage a file belongs to, or None."""
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


# ---------------------------------------------------------------------------
# Rule 1: façade rule
# ---------------------------------------------------------------------------


def test_cross_package_imports_go_through_init():
    """
    From outside package X, import app_operator.X — not app_operator.X.submodule.
    The public API lives in __init__.py; internal submodules are an impl detail.
    """
    violations: list[str] = []

    for filepath in _iter_py_files(*_PROJECT_PACKAGES.values()):
        home = _home_package(filepath)

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

            violations.append(
                _fmt(
                    filepath,
                    lineno,
                    module,
                    f"bypass of {target_pkg}/__init__.py; use 'from app_operator.{target_pkg} import ...' instead",
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

            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
                    and isinstance(node.value, ast.List)
                ):
                    continue
                violations.extend(
                    f"  {init.relative_to(_REPO_ROOT)}:{elt.lineno}  '{elt.value}'  — private name in __all__"
                    for elt in node.value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str) and elt.value.startswith("_")
                )

    assert not violations, f"{len(violations)} private-export violation(s) found:\n" + "\n".join(violations)
