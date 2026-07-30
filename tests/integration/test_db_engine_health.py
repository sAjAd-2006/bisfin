"""Production engine settings and structured health against PostgreSQL 16."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from bisfin.config import Settings
from bisfin.db.health import REQUIRED_SCHEMAS, DatabaseHealthChecker


def test_engine_connectivity_and_session_configuration(
    db_engine: Engine,
    db_settings: Settings,
) -> None:
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
        assert (
            connection.execute(text("SHOW application_name")).scalar_one()
            == db_settings.database_application_name
        )
        assert connection.execute(text("SHOW transaction_isolation")).scalar_one() == (
            "read committed"
        )
        assert connection.execute(text("SHOW statement_timeout")).scalar_one() != "0"


def test_database_health_is_healthy_for_migrated_postgresql_16(db_engine: Engine) -> None:
    report = DatabaseHealthChecker(db_engine).check()

    assert report.healthy is True, report.summary()
    assert report.current_revision == "0003"
    assert report.expected_revision == "0003"
    assert report.postgresql_major_version == 16
    schemas = next(check for check in report.checks if check.name == "required_schemas")
    assert schemas.details["required_schemas"] == sorted(REQUIRED_SCHEMAS)


def test_database_health_detects_revision_mismatch(db_engine: Engine) -> None:
    report = DatabaseHealthChecker(db_engine, expected_revision="does-not-exist").check()
    revision = next(check for check in report.checks if check.name == "alembic_revision")

    assert report.healthy is False
    assert revision.healthy is False
    assert revision.details["current_revisions"] == ("0003",)
