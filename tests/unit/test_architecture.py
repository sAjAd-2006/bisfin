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
    forbidden = (
        "sqlalchemy",
        "psycopg",
        "httpx",
        "bisfin.cli",
        "bisfin.logging",
        "bisfin.repositories",
    )
    for path in (PACKAGE_ROOT / "domain").glob("*.py"):
        for imported in _imports(path):
            assert not imported.startswith(forbidden), f"{path.name} imports {imported}"


def test_database_and_repository_layers_do_not_import_cli() -> None:
    for layer in ("db", "repositories"):
        for path in (PACKAGE_ROOT / layer).glob("*.py"):
            assert "bisfin.cli" not in _imports(path), f"{path.name} imports CLI"


def test_provider_layer_does_not_import_cli_or_database_implementation() -> None:
    provider_root = PACKAGE_ROOT / "integrations" / "brsapi"
    for path in provider_root.glob("*.py"):
        imported = _imports(path)
        assert "bisfin.cli" not in imported, f"{path.name} imports CLI"
        assert not any(name.startswith("sqlalchemy") for name in imported)
        assert not any(name.startswith("psycopg") for name in imported)


def test_repositories_never_own_commits() -> None:
    for path in (PACKAGE_ROOT / "repositories").glob("*.py"):
        assert ".commit(" not in path.read_text(encoding="utf-8"), path.name


def test_application_never_calls_metadata_create_all() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        assert ".create_all(" not in path.read_text(encoding="utf-8")


def test_historical_bar_repository_delegates_only_to_pit_function() -> None:
    source = (PACKAGE_ROOT / "repositories" / "bar_repository.py").read_text(encoding="utf-8")
    assert "market.bars_as_of" in source
    assert "market.current_bar" not in source
    assert "market.bar_revision" not in source


def test_bar_writer_is_append_only_and_never_reads_current_bar() -> None:
    source = (PACKAGE_ROOT / "repositories" / "bar_writer_repository.py").read_text(
        encoding="utf-8"
    )
    assert "market.current_bar" not in source
    assert ".update(" not in source
    assert ".delete(" not in source
