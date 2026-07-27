"""Validate the raw-SQL registry against the complete Alembic revision graph."""

from __future__ import annotations

import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError

from migration_registry import (
    MIGRATIONS,
    MigrationRegistryError,
    MigrationSpec,
    validate_registry,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class MigrationGraphError(RuntimeError):
    """Raised when Alembic history differs from the checksum registry."""


def _single_parent(value: object, revision: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise MigrationGraphError(
        f"Alembic revision {revision} has multiple parents {value!r}; "
        "the migration registry requires one linear chain."
    )


def _expected_graph(migrations: tuple[MigrationSpec, ...]) -> dict[str, str | None]:
    return {migration.revision: migration.down_revision for migration in migrations}


def _alembic_graph(script_directory: ScriptDirectory) -> dict[str, str | None]:
    graph: dict[str, str | None] = {}
    for script in script_directory.walk_revisions(base="base", head="heads"):
        revision = script.revision
        if revision in graph:
            raise MigrationGraphError(f"Duplicate Alembic revision: {revision}.")
        graph[revision] = _single_parent(script.down_revision, revision)
    return graph


def _graph_difference(
    expected: dict[str, str | None],
    actual: dict[str, str | None],
) -> str:
    missing = sorted(expected.keys() - actual.keys())
    unexpected = sorted(actual.keys() - expected.keys())
    changed_parents = sorted(
        revision
        for revision in expected.keys() & actual.keys()
        if expected[revision] != actual[revision]
    )
    details: list[str] = []
    if missing:
        details.append(f"missing Alembic revisions: {', '.join(missing)}")
    if unexpected:
        details.append(f"unregistered Alembic revisions: {', '.join(unexpected)}")
    if changed_parents:
        descriptions = [
            f"{revision} (expected {expected[revision]!r}, received {actual[revision]!r})"
            for revision in changed_parents
        ]
        details.append(f"down_revision mismatches: {', '.join(descriptions)}")
    return "; ".join(details) or "unknown graph mismatch"


def check_migrations(repository_root: Path = REPOSITORY_ROOT) -> tuple[MigrationSpec, ...]:
    """Validate file identity, registry order, and the exact Alembic graph."""

    registered = validate_registry(repository_root=repository_root, migrations=MIGRATIONS)

    config = Config(str(repository_root / "alembic.ini"))
    script_directory = ScriptDirectory.from_config(config)
    expected = _expected_graph(registered)
    actual = _alembic_graph(script_directory)

    if actual != expected:
        raise MigrationGraphError(
            "Alembic history does not exactly match the migration registry: "
            f"{_graph_difference(expected, actual)}."
        )

    expected_bases = tuple(
        sorted(revision for revision, parent in expected.items() if parent is None)
    )
    actual_bases = tuple(sorted(script_directory.get_bases()))
    if actual_bases != expected_bases:
        raise MigrationGraphError(
            f"Alembic bases {actual_bases!r} do not match registry bases "
            f"{expected_bases!r}."
        )

    referenced_revisions = {parent for parent in expected.values() if parent is not None}
    expected_heads = tuple(sorted(expected.keys() - referenced_revisions))
    actual_heads = tuple(sorted(script_directory.get_heads()))
    if actual_heads != expected_heads:
        raise MigrationGraphError(
            f"Alembic heads {actual_heads!r} do not match registry heads "
            f"{expected_heads!r}."
        )

    return registered


def main() -> int:
    try:
        registered = check_migrations()
    except (CommandError, MigrationGraphError, MigrationRegistryError) as exc:
        print(f"Migration check failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Validated {len(registered)} checksum-registered migration(s); "
        f"Alembic head is {registered[-1].revision}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
