"""Small operational CLI for configuration and PostgreSQL readiness checks."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import Protocol, TextIO

from pydantic import ValidationError
from sqlalchemy.engine import Engine

from bisfin.config import Settings, get_settings
from bisfin.db.engine import create_engine, dispose_engine
from bisfin.db.errors import BisfinError, redact_secrets
from bisfin.db.health import DatabaseHealthChecker, DatabaseHealthReport
from bisfin.logging import configure_logging

SettingsFactory = Callable[[], Settings]
EngineFactory = Callable[[Settings], Engine]


class HealthChecker(Protocol):
    def check(self) -> DatabaseHealthReport: ...


HealthCheckerFactory = Callable[[Engine], HealthChecker]


def build_parser() -> argparse.ArgumentParser:
    """Build the parser without reading settings or connecting to PostgreSQL."""

    parser = argparse.ArgumentParser(prog="bisfin", description="Bisfin operational checks")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("config-check", help="validate and print non-secret settings")
    subcommands.add_parser("db-health", help="run PostgreSQL health checks")
    subcommands.add_parser("db-current", help="compare current and expected revisions")
    return parser


def run(
    argv: Sequence[str],
    *,
    settings_factory: SettingsFactory = get_settings,
    engine_factory: EngineFactory = create_engine,
    health_checker_factory: HealthCheckerFactory = DatabaseHealthChecker,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Execute one command with injectable boundaries for unit tests."""

    arguments = build_parser().parse_args(list(argv))
    try:
        settings = settings_factory()
        # Resolve the secret URL without retaining or printing it. This makes
        # config-check reject malformed explicit URLs before engine creation.
        settings.sqlalchemy_database_url
    except ValidationError as error:
        fields = sorted({str(item["loc"][0]) for item in error.errors() if item["loc"]})
        suffix = f" Invalid fields: {', '.join(fields)}." if fields else ""
        print(f"configuration: invalid.{suffix}", file=stderr)
        return 2
    except (ValueError, BisfinError):
        print("configuration: invalid.", file=stderr)
        return 2

    if arguments.command == "config-check":
        print("configuration: ok", file=stdout)
        for key, value in settings.safe_summary().items():
            print(f"{key}={value}", file=stdout)
        return 0

    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        environment=settings.environment,
        application=settings.application,
    )
    engine: Engine | None = None
    try:
        engine = engine_factory(settings)
        report = health_checker_factory(engine).check()
    except (BisfinError, ValueError) as error:
        print(f"database: unavailable ({redact_secrets(error)})", file=stderr)
        return 1
    finally:
        if engine is not None:
            dispose_engine(engine)

    if arguments.command == "db-health":
        print(report.summary(), file=stdout)
        for check in report.checks:
            status = "ok" if check.healthy else "failed"
            print(f"{check.name}={status}: {check.message}", file=stdout)
        return 0 if report.healthy else 1

    if arguments.command == "db-current":
        print(f"current_revision={report.current_revision or 'none'}", file=stdout)
        print(f"expected_revision={report.expected_revision}", file=stdout)
        return 0 if report.current_revision == report.expected_revision else 1

    raise AssertionError(f"unhandled command: {arguments.command}")


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point."""

    return run(sys.argv[1:] if argv is None else argv)


__all__ = ["build_parser", "main", "run"]
