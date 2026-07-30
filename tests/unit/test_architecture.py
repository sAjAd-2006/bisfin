"""Lightweight dependency-boundary checks without an architecture dependency."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "bisfin"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_domain_layer_has_no_infrastructure_dependencies() -> None:
    forbidden = ("sqlalchemy", "psycopg", "bisfin.cli", "bisfin.logging", "bisfin.repositories")
    for path in (PACKAGE_ROOT / "domain").glob("*.py"):
        for imported in _imports(path):
            assert not imported.startswith(forbidden), f"{path.name} imports {imported}"


def test_database_and_repository_layers_do_not_import_cli() -> None:
    for layer in ("db", "repositories"):
        for path in (PACKAGE_ROOT / layer).glob("*.py"):
            assert "bisfin.cli" not in _imports(path), f"{path.name} imports CLI"


def test_application_never_calls_metadata_create_all() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        assert ".create_all(" not in path.read_text(encoding="utf-8")


def test_historical_bar_repository_delegates_only_to_pit_function() -> None:
    source = (PACKAGE_ROOT / "repositories" / "bar_repository.py").read_text(encoding="utf-8")
    assert "market.bars_as_of" in source
    assert "market.current_bar" not in source
    assert "market.bar_revision" not in source
