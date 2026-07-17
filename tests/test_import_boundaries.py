"""Import boundary test for the Ledgercore 0.5.x adapter.

Only :mod:`releaseledger.ledgercore_backend` may import detailed Ledgercore
manifest, layout, binding, validation, migration, and storage-path APIs.
Domain code may import generic utility modules (atomic, frontmatter,
ids, io, jsonio, jsonl, refs, yamlio) but not the structured storage
API. The test walks the ``releaseledger`` package, parses each Python
source file, and asserts the rule.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "releaseledger"
ADAPTER_MODULE = "releaseledger.ledgercore_backend"

# Detailed Ledgercore storage APIs that must stay inside the adapter.
FORBIDDEN_SUBMODULES: frozenset[str] = frozenset(
    {
        "ledgercore.manifest",
        "ledgercore.layout",
        "ledgercore.storage_binding",
        "ledgercore.storage_paths",
        "ledgercore.tomlio",
        "ledgercore.migration",
        "ledgercore.overrides",
    }
)

# Generic utility modules that the wider codebase may still import.
ALLOWED_SUBMODULES: frozenset[str] = frozenset(
    {
        "ledgercore.atomic",
        "ledgercore.frontmatter",
        "ledgercore.ids",
        "ledgercore.io",
        "ledgercore.jsonio",
        "ledgercore.jsonl",
        "ledgercore.refs",
        "ledgercore.yamlio",
    }
)


def _iter_python_files() -> Iterable[Path]:
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if path.name == "__pycache__":
            continue
        yield path


def _is_forbidden_import(module: str) -> bool:
    if module in FORBIDDEN_SUBMODULES:
        return True
    for forbidden in FORBIDDEN_SUBMODULES:
        if module.startswith(f"{forbidden}."):
            return True
    return False


def _iter_imports(path: Path) -> Iterable[tuple[int, str]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield (node.lineno, alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            for alias in node.names:
                full = f"{node.module}.{alias.name}" if alias.name else node.module
                yield (node.lineno, full)


def test_no_detailed_ledgercore_imports_outside_adapter() -> None:
    """Only the adapter may import detailed Ledgercore storage APIs."""

    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_python_files():
        module = _module_name_for(path)
        if module == ADAPTER_MODULE:
            continue
        for lineno, imported in _iter_imports(path):
            root = imported.split(".", 1)[0]
            if root != "ledgercore":
                continue
            if imported in ALLOWED_SUBMODULES:
                continue
            if _is_forbidden_import(imported):
                offenders.append((path, lineno, imported))
    assert not offenders, (
        "Detailed Ledgercore storage imports must stay inside "
        f"{ADAPTER_MODULE}. Found:\n"
        + "\n".join(
            f"  {path.relative_to(PACKAGE_ROOT.parent)}:{lineno} {module}"
            for path, lineno, module in offenders
        )
    )


def test_adapter_may_import_detailed_apis() -> None:
    """The adapter module exists and is allowed to import storage APIs."""

    adapter_path = PACKAGE_ROOT / "ledgercore_backend.py"
    assert adapter_path.is_file(), f"missing adapter at {adapter_path}"
    saw_forbidden = False
    for _lineno, imported in _iter_imports(adapter_path):
        if _is_forbidden_import(imported):
            saw_forbidden = True
            break
    assert saw_forbidden, (
        "the adapter should import at least one detailed Ledgercore "
        "storage API; otherwise the rule below has nothing to enforce"
    )


def test_no_stale_forbidden_imports() -> None:
    """Forbidden module names match the Ledgercore 0.5.x surface.

    The list is the contract. If Ledgercore renames or moves a module,
    this test fails so the forbidden list is updated together with the
    adapter in a single change set.
    """

    expected = {
        "ledgercore.manifest",
        "ledgercore.layout",
        "ledgercore.storage_binding",
        "ledgercore.storage_paths",
        "ledgercore.tomlio",
        "ledgercore.migration",
        "ledgercore.overrides",
    }
    assert FORBIDDEN_SUBMODULES == expected


def _module_name_for(path: Path) -> str:
    rel = path.relative_to(PACKAGE_ROOT.parent).with_suffix("")
    return ".".join(rel.parts)
