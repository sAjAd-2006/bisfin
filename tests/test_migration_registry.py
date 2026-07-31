"""Focused tests for checksum-aware raw SQL migration infrastructure."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import cast

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from migration_registry import (
    MIGRATIONS,
    ChecksumMismatchError,
    MigrationSpec,
    MissingMigrationFileError,
    RegistryOrderingError,
    UnsupportedDowngradeError,
    build_database_url,
    read_verified_sql,
    validate_registry,
)

from bisfin.schema_contract import ALEMBIC_HEAD_REVISION

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_schema_contract_matches_registered_head() -> None:
    assert MIGRATIONS[-1].revision == ALEMBIC_HEAD_REVISION


def test_missing_migration_file_is_rejected(tmp_path: Path) -> None:
    migration = MigrationSpec(
        revision="missing",
        down_revision=None,
        relative_path=PurePosixPath("db/postgresql/migrations/missing.sql"),
        sha256="0" * 64,
    )

    with pytest.raises(MissingMigrationFileError) as error:
        read_verified_sql(migration, repository_root=tmp_path)

    message = str(error.value)
    assert "missing" in message
    assert migration.relative_path.as_posix() in message


def test_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    relative_path = PurePosixPath("db/postgresql/migrations/tampered.sql")
    migration_path = tmp_path / Path(relative_path)
    migration_path.parent.mkdir(parents=True)
    migration_path.write_bytes(b"tampered migration bytes\n")
    expected_checksum = hashlib.sha256(b"canonical migration bytes\n").hexdigest()
    actual_checksum = hashlib.sha256(migration_path.read_bytes()).hexdigest()
    migration = MigrationSpec(
        revision="tampered",
        down_revision=None,
        relative_path=relative_path,
        sha256=expected_checksum,
    )

    with pytest.raises(ChecksumMismatchError) as error:
        read_verified_sql(migration, repository_root=tmp_path)

    message = str(error.value)
    assert expected_checksum in message
    assert actual_checksum in message


def test_database_url_takes_priority_and_uses_sync_psycopg() -> None:
    url = build_database_url(
        {
            "DATABASE_URL": (
                "postgresql://encoded%40user:p%40ss%2Fword@db.example:6432/market"
            ),
            # An invalid fallback proves DATABASE_URL has priority.
            "POSTGRES_PORT": "not-an-integer",
        }
    )

    assert url.drivername == "postgresql+psycopg"
    assert url.username == "encoded@user"
    assert url.password == "p@ss/word"
    assert url.host == "db.example"
    assert url.port == 6432
    assert url.database == "market"


def test_database_url_fallback_preserves_special_credentials() -> None:
    username = "local:user@example"
    password = "p@ss/word%with spaces"
    url = build_database_url(
        {
            "POSTGRES_DB": "bisfin-test",
            "POSTGRES_USER": username,
            "POSTGRES_PASSWORD": password,
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": "55432",
        }
    )

    assert url.drivername == "postgresql+psycopg"
    assert url.username == username
    assert url.password == password
    assert url.host == "127.0.0.1"
    assert url.port == 55432
    assert url.database == "bisfin-test"

    rendered = url.render_as_string(hide_password=False)
    reparsed = make_url(rendered)
    assert reparsed.username == username
    assert reparsed.password == password


def test_registry_has_the_exact_canonical_order() -> None:
    registered = validate_registry(repository_root=REPOSITORY_ROOT)

    assert registered == MIGRATIONS
    assert tuple(
        (
            migration.revision,
            migration.down_revision,
            migration.relative_path.as_posix(),
            migration.sha256,
        )
        for migration in registered
    ) == (
        (
            "0001",
            None,
            "db/postgresql/migrations/0001_core_schema.sql",
            "cf3a9d3438c611d931aa26d8aeb14264259d1dbe2be731689eb2e42e12a8cc9a",
        ),
        (
            "0002",
            "0001",
            "db/postgresql/migrations/0002_technical_backtest_completion.sql",
            "8a225a1b1cb3fd4ccdb6a61aaff88f17df8449ca859eeaa6e144dbd53be2445d",
        ),
        (
            "0003",
            "0002",
            "db/postgresql/migrations/0003_point_in_time_hardening.sql",
            "04e6ab36f457de807202ddd0b619b813f01a627693582c19a2a1ac50a5331c3a",
        ),
        (
            "0004",
            "0003",
            "db/postgresql/migrations/0004_ingestion_runtime_support.sql",
            "188080740e805ed9d58de2f4c72a3007b6c46a45e3b253e7f5226d8538a417b7",
        ),
    )


def test_registry_rejects_out_of_order_migrations() -> None:
    with pytest.raises(RegistryOrderingError, match="down_revision"):
        validate_registry(
            repository_root=REPOSITORY_ROOT,
            migrations=tuple(reversed(MIGRATIONS)),
        )


@pytest.mark.parametrize("revision", ("0001", "0002", "0003", "0004"))
def test_each_revision_rejects_downgrade(revision: str) -> None:
    config = Config(str(REPOSITORY_ROOT / "alembic.ini"))
    script_directory = ScriptDirectory.from_config(config)
    script = script_directory.get_revision(revision)
    assert script is not None
    downgrade = cast("Callable[[], None]", getattr(script.module, "downgrade"))

    with pytest.raises(UnsupportedDowngradeError) as error:
        downgrade()

    message = str(error.value).lower()
    assert "intentionally unsupported" in message
    assert "forward migration" in message
