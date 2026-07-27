"""Checksum-aware registry and execution helpers for raw SQL migrations."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn, cast

import psycopg
from sqlalchemy.engine import URL, make_url
from sqlalchemy.engine import Connection as SQLAlchemyConnection

from alembic import op


class MigrationRegistryError(RuntimeError):
    """Base exception for migration registry failures."""


class DatabaseConfigurationError(MigrationRegistryError):
    """Raised when a synchronous PostgreSQL URL cannot be constructed."""


class RegistryOrderingError(MigrationRegistryError):
    """Raised when the migration registry is not a unique linear chain."""


class UnknownMigrationError(MigrationRegistryError):
    """Raised when a revision is not present in the migration registry."""


class MissingMigrationFileError(MigrationRegistryError):
    """Raised when a registered raw SQL migration is missing."""


class ChecksumMismatchError(MigrationRegistryError):
    """Raised when a migration's bytes do not match its registered digest."""


class MigrationExecutionError(MigrationRegistryError):
    """Raised when Alembic is not using the required synchronous driver."""


class UnsupportedDowngradeError(MigrationRegistryError):
    """Raised whenever a destructive downgrade is requested."""


class AdvisoryLockError(MigrationRegistryError):
    """Raised when the migration advisory lock cannot be acquired or released."""


@dataclass(frozen=True, slots=True)
class MigrationSpec:
    """Immutable metadata for one raw SQL Alembic revision."""

    revision: str
    down_revision: str | None
    relative_path: PurePosixPath
    sha256: str


REPOSITORY_ROOT = Path(__file__).resolve().parent

MIGRATIONS: tuple[MigrationSpec, ...] = (
    MigrationSpec(
        revision="0001",
        down_revision=None,
        relative_path=PurePosixPath(
            "db/postgresql/migrations/0001_core_schema.sql"
        ),
        sha256="cf3a9d3438c611d931aa26d8aeb14264259d1dbe2be731689eb2e42e12a8cc9a",
    ),
    MigrationSpec(
        revision="0002",
        down_revision="0001",
        relative_path=PurePosixPath(
            "db/postgresql/migrations/0002_technical_backtest_completion.sql"
        ),
        sha256="8a225a1b1cb3fd4ccdb6a61aaff88f17df8449ca859eeaa6e144dbd53be2445d",
    ),
    MigrationSpec(
        revision="0003",
        down_revision="0002",
        relative_path=PurePosixPath(
            "db/postgresql/migrations/0003_point_in_time_hardening.sql"
        ),
        sha256="04e6ab36f457de807202ddd0b619b813f01a627693582c19a2a1ac50a5331c3a",
    ),
)

_MIGRATIONS_BY_REVISION: Mapping[str, MigrationSpec] = {
    migration.revision: migration for migration in MIGRATIONS
}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED_POSTGRES_ENV = (
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
)


def build_database_url(environ: Mapping[str, str] | None = None) -> URL:
    """Return a synchronous psycopg SQLAlchemy URL from process settings."""

    source = os.environ if environ is None else environ
    configured_url = source.get("DATABASE_URL", "").strip()

    if configured_url:
        if configured_url.startswith("postgres://"):
            configured_url = "postgresql+psycopg://" + configured_url.removeprefix(
                "postgres://"
            )
        elif configured_url.startswith("postgresql://"):
            configured_url = "postgresql+psycopg://" + configured_url.removeprefix(
                "postgresql://"
            )

        try:
            url = make_url(configured_url)
        except Exception as exc:
            raise DatabaseConfigurationError("DATABASE_URL is not a valid URL.") from exc

        if url.drivername == "postgresql":
            return url.set(drivername="postgresql+psycopg")
        if url.drivername != "postgresql+psycopg":
            raise DatabaseConfigurationError(
                "DATABASE_URL must use PostgreSQL with the synchronous psycopg driver."
            )
        return url

    missing = [name for name in _REQUIRED_POSTGRES_ENV if not source.get(name)]
    if missing:
        names = ", ".join(missing)
        raise DatabaseConfigurationError(
            f"Missing required database environment variables: {names}."
        )

    raw_port = source["POSTGRES_PORT"]
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise DatabaseConfigurationError(
            f"POSTGRES_PORT must be an integer, received {raw_port!r}."
        ) from exc
    if not 1 <= port <= 65_535:
        raise DatabaseConfigurationError("POSTGRES_PORT must be between 1 and 65535.")

    return URL.create(
        drivername="postgresql+psycopg",
        username=source["POSTGRES_USER"],
        password=source["POSTGRES_PASSWORD"],
        host="localhost",
        port=port,
        database=source["POSTGRES_DB"],
    )


def _resolve_migration_path(
    migration: MigrationSpec,
    repository_root: Path,
) -> Path:
    root = repository_root.resolve()
    migration_path = (root / Path(migration.relative_path)).resolve()
    try:
        migration_path.relative_to(root)
    except ValueError as exc:
        raise MigrationRegistryError(
            f"Migration path escapes the repository root: {migration.relative_path}."
        ) from exc
    return migration_path


def _migration_for_revision(revision: str) -> MigrationSpec:
    try:
        return _MIGRATIONS_BY_REVISION[revision]
    except KeyError as exc:
        raise UnknownMigrationError(
            f"Revision {revision!r} is not registered."
        ) from exc


def read_verified_sql(
    migration: MigrationSpec | str,
    repository_root: Path | None = None,
) -> str:
    """Read, checksum, and UTF-8 decode one migration without altering bytes."""

    spec = _migration_for_revision(migration) if isinstance(migration, str) else migration
    root = REPOSITORY_ROOT if repository_root is None else repository_root
    migration_path = _resolve_migration_path(spec, root)

    if not migration_path.is_file():
        raise MissingMigrationFileError(
            f"Migration SQL file is missing for revision {spec.revision}: "
            f"{spec.relative_path}."
        )

    sql_bytes = migration_path.read_bytes()
    actual_sha256 = hashlib.sha256(sql_bytes).hexdigest()
    if actual_sha256 != spec.sha256:
        raise ChecksumMismatchError(
            f"Checksum mismatch for revision {spec.revision} "
            f"({spec.relative_path}): expected {spec.sha256}, "
            f"received {actual_sha256}."
        )

    try:
        return sql_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationRegistryError(
            f"Migration {spec.relative_path} is not valid UTF-8."
        ) from exc


def validate_registry(
    repository_root: Path | None = None,
    migrations: Sequence[MigrationSpec] = MIGRATIONS,
) -> tuple[MigrationSpec, ...]:
    """Validate ordering, uniqueness, paths, and checksums for all migrations."""

    registered = tuple(migrations)
    if not registered:
        raise RegistryOrderingError("The migration registry cannot be empty.")

    seen_revisions: set[str] = set()
    seen_paths: set[PurePosixPath] = set()
    expected_down_revision: str | None = None
    root = REPOSITORY_ROOT if repository_root is None else repository_root

    for migration in registered:
        if migration.revision in seen_revisions:
            raise RegistryOrderingError(
                f"Duplicate migration revision: {migration.revision}."
            )
        if migration.relative_path in seen_paths:
            raise RegistryOrderingError(
                f"Duplicate migration path: {migration.relative_path}."
            )
        if migration.down_revision != expected_down_revision:
            raise RegistryOrderingError(
                f"Revision {migration.revision} has down_revision "
                f"{migration.down_revision!r}; expected {expected_down_revision!r}."
            )
        if len(migration.revision) > 32:
            raise RegistryOrderingError(
                f"Revision {migration.revision!r} exceeds Alembic's 32-character limit."
            )
        if _SHA256_PATTERN.fullmatch(migration.sha256) is None:
            raise RegistryOrderingError(
                f"Revision {migration.revision} has an invalid SHA-256 digest."
            )

        read_verified_sql(migration, root)
        seen_revisions.add(migration.revision)
        seen_paths.add(migration.relative_path)
        expected_down_revision = migration.revision

    return registered


def _synchronous_psycopg_connection(
    bind: SQLAlchemyConnection,
) -> psycopg.Connection[tuple[object, ...]]:
    driver_connection = bind.connection.driver_connection
    if not isinstance(driver_connection, psycopg.Connection):
        raise MigrationExecutionError(
            "Alembic migrations require a synchronous psycopg connection."
        )
    return cast("psycopg.Connection[tuple[object, ...]]", driver_connection)


def apply_registered_migration(revision: str) -> None:
    """Execute one verified raw SQL migration under its own transaction control."""

    sql_text = read_verified_sql(revision)
    migration_context = op.get_context()

    with migration_context.autocommit_block():
        raw_connection = _synchronous_psycopg_connection(op.get_bind())
        if not raw_connection.autocommit:
            raise MigrationExecutionError(
                "The raw migration must run inside Alembic's autocommit block."
            )
        try:
            with raw_connection.cursor() as cursor:
                cursor.execute(sql_text)
        except Exception:
            raw_connection.rollback()
            raise


def unsupported_downgrade(revision: str) -> NoReturn:
    """Reject destructive rollback and direct callers to a forward migration."""

    raise UnsupportedDowngradeError(
        f"Downgrade from revision {revision} is intentionally unsupported. "
        "Create a forward migration to correct the schema state."
    )
