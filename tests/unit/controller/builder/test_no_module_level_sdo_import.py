from __future__ import annotations

import ast
from pathlib import Path

import controller.builder

# The validator image copies only controller/, not sdo/. check_cli runs there
# (test / draft-test), so controller.builder must not import sdo at MODULE load
# or `check_cli test` crashes in the validator pod with ModuleNotFoundError.
#
# Seam 3's launcher cutover deliberately adds a FUNCTION-scoped import of the
# proto contracts inside check_cli._controller_once (which only ever runs in the
# runtime image, where sdo is present). This test enforces that the import stays
# function-scoped: it fails if anyone hoists an sdo import to module scope (or a
# class body, or a plain module-level if/try) where it would execute on import.
# Imports inside a function body or an `if TYPE_CHECKING:` block are allowed --
# neither executes when the validator image imports the module.

_BUILDER_DIR = Path(controller.builder.__file__).resolve().parent


def _is_type_checking_guard(node: ast.If) -> bool:
    test = node.test
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _module_level_sdo_imports(source: str) -> list[str]:
    """Return sdo imports that execute at module import time (i.e. not deferred).

    Deferred = lexically inside a function/method body or an ``if TYPE_CHECKING``
    block. Everything else (module top level, class body, module-level if/try)
    runs on import.
    """

    tree = ast.parse(source)
    offenders: list[str] = []

    def visit(node: ast.AST, *, deferred: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, deferred=True)
                continue
            if isinstance(child, ast.If) and _is_type_checking_guard(child):
                for guarded in child.body:
                    visit(guarded, deferred=True)
                for guarded in child.orelse:
                    visit(guarded, deferred=deferred)
                continue
            if not deferred and isinstance(child, ast.Import):
                offenders.extend(alias.name for alias in child.names if _is_sdo(alias.name))
            elif (
                not deferred
                and isinstance(child, ast.ImportFrom)
                and child.module is not None
                and _is_sdo(child.module)
            ):
                offenders.append(child.module)
            visit(child, deferred=deferred)

    visit(tree, deferred=False)
    return offenders


def _is_sdo(module: str) -> bool:
    return module == "sdo" or module.startswith("sdo.")


def test_controller_builder_has_no_module_level_sdo_import() -> None:
    offenders: dict[str, list[str]] = {}
    for path in sorted(_BUILDER_DIR.rglob("*.py")):
        imports = _module_level_sdo_imports(path.read_text(encoding="utf-8"))
        if imports:
            offenders[str(path.relative_to(_BUILDER_DIR))] = imports
    assert not offenders, (
        "controller.builder must not import sdo at module load (the validator image has no sdo). "
        f"Move these into a function body or an if TYPE_CHECKING block: {offenders}"
    )


def test_guard_flags_a_module_level_sdo_import() -> None:
    # The guard catches a hoisted import...
    assert _module_level_sdo_imports("import sdo.contracts.proto\n")
    assert _module_level_sdo_imports("from sdo.contracts import sdk_schema\n")
    # ...and allows a function-scoped or TYPE_CHECKING one.
    assert not _module_level_sdo_imports("def f():\n    from sdo.contracts import proto\n")
    assert not _module_level_sdo_imports(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import sdo.contracts.proto\n"
    )
    # Non-sdo imports never flagged.
    assert not _module_level_sdo_imports("import os\nfrom pathlib import Path\n")
