"""Small operational CLI for configuration and PostgreSQL readiness checks."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, TextIO

from pydantic import ValidationError
from sqlalchemy.engine import Engine

from bisfin.calendar import load_calendar_manifest, validate_calendar_manifest
from bisfin.calendar.importer import TradingCalendarImportService
from bisfin.calendar.results import CalendarImportResult
from bisfin.catalog import load_catalog_manifest
from bisfin.catalog.bootstrap import CatalogBootstrapService, CatalogValidationMode
from bisfin.catalog.results import CatalogBootstrapResult
from bisfin.config import Settings, get_settings
from bisfin.db.engine import create_engine, dispose_engine
from bisfin.db.errors import BisfinError, redact_secrets
from bisfin.db.health import DatabaseHealthChecker, DatabaseHealthReport
from bisfin.domain.ingestion import IngestionBatchStatus
from bisfin.ingestion.results import DailyBarIngestionResult
from bisfin.ingestion.service import BrsApiDailyBarIngestionService
from bisfin.integrations.brsapi import (
    BrsApiClient,
    BrsApiConfigurationError,
    BrsApiError,
    FixtureBrsApiClient,
    FixtureBrsApiSymbolClient,
    HttpxBrsApiClient,
    HttpxBrsApiSymbolClient,
)
from bisfin.logging import configure_logging
from bisfin.repositories import create_unit_of_work_factory
from bisfin.snapshots.builder import SnapshotBuilder
from bisfin.snapshots.contracts import SnapshotBuildResult, SnapshotVerificationResult
from bisfin.snapshots.manifest import load_snapshot_manifest
from bisfin.snapshots.verifier import SnapshotVerifier

SettingsFactory = Callable[[], Settings]
EngineFactory = Callable[[Settings], Engine]


class HealthChecker(Protocol):
    def check(self) -> DatabaseHealthReport: ...


HealthCheckerFactory = Callable[[Engine], HealthChecker]
IngestionRunner = Callable[[argparse.Namespace, Settings], DailyBarIngestionResult]


def build_parser() -> argparse.ArgumentParser:
    """Build the parser without reading settings or connecting to PostgreSQL."""

    parser = argparse.ArgumentParser(prog="bisfin", description="Bisfin operational checks")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("config-check", help="validate and print non-secret settings")
    subcommands.add_parser("db-health", help="run PostgreSQL health checks")
    subcommands.add_parser("db-current", help="compare current and expected revisions")
    ingest = subcommands.add_parser("ingest", help="run one explicit ingestion operation")
    ingest_commands = ingest.add_subparsers(dest="ingest_command", required=True)
    daily = ingest_commands.add_parser(
        "brsapi-daily-bars",
        help="ingest BrsApi TSETMC unadjusted daily candles (type=2)",
    )
    daily.add_argument("--symbol", required=True, help="provider l18 identifier")
    daily.add_argument("--request-id", help="caller-owned idempotency key")
    daily.add_argument(
        "--fixture",
        type=Path,
        help="read exact response bytes locally and disable all network access",
    )
    daily.add_argument(
        "--output-format",
        choices=("human", "json"),
        default="human",
        help="bounded secret-free result format",
    )
    catalog = subcommands.add_parser("catalog", help="validate or bootstrap an explicit catalog")
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_validate = catalog_commands.add_parser("validate", help="validate a catalog JSON file")
    catalog_validate.add_argument("--manifest", type=Path, required=True)
    catalog_bootstrap = catalog_commands.add_parser(
        "bootstrap", help="bootstrap only the manifest-listed instruments"
    )
    catalog_bootstrap.add_argument("--manifest", type=Path, required=True)
    catalog_bootstrap.add_argument(
        "--validation-mode",
        choices=tuple(mode.value for mode in CatalogValidationMode),
        default=None,
    )
    catalog_bootstrap.add_argument("--symbol-fixture-dir", type=Path)
    catalog_bootstrap.add_argument("--request-id")
    catalog_bootstrap.add_argument("--output-format", choices=("human", "json"), default="human")

    calendar = subcommands.add_parser("calendar", help="validate or import an explicit calendar")
    calendar_commands = calendar.add_subparsers(dest="calendar_command", required=True)
    calendar_validate = calendar_commands.add_parser(
        "validate", help="validate a calendar JSON file"
    )
    calendar_validate.add_argument("--file", type=Path, required=True)
    calendar_import = calendar_commands.add_parser(
        "import", help="import an explicit calendar JSON"
    )
    calendar_import.add_argument("--file", type=Path, required=True)
    calendar_import.add_argument("--request-id")
    calendar_import.add_argument("--output-format", choices=("human", "json"), default="human")
    snapshot = subcommands.add_parser(
        "snapshot", help="validate, build, show, or verify a frozen snapshot"
    )
    snapshot_commands = snapshot.add_subparsers(dest="snapshot_command", required=True)
    snapshot_validate = snapshot_commands.add_parser(
        "validate", help="strictly validate a snapshot manifest"
    )
    snapshot_validate.add_argument("--manifest", type=Path, required=True)
    snapshot_build = snapshot_commands.add_parser(
        "build", help="build an immutable bar-revision snapshot"
    )
    snapshot_build.add_argument("--manifest", type=Path, required=True)
    snapshot_build.add_argument("--output-dir", type=Path, required=True)
    snapshot_build.add_argument("--output-format", choices=("human", "json"), default="human")
    snapshot_show = snapshot_commands.add_parser("show", help="show frozen snapshot metadata")
    snapshot_show.add_argument("--code", required=True)
    snapshot_show.add_argument("--output-format", choices=("human", "json"), default="human")
    snapshot_verify = snapshot_commands.add_parser(
        "verify", help="verify immutable snapshot artifacts"
    )
    snapshot_verify.add_argument("--code", required=True)
    snapshot_verify.add_argument("--against-db", action="store_true")
    snapshot_verify.add_argument("--output-format", choices=("human", "json"), default="human")
    return parser


def run(
    argv: Sequence[str],
    *,
    settings_factory: SettingsFactory = get_settings,
    engine_factory: EngineFactory = create_engine,
    health_checker_factory: HealthCheckerFactory = DatabaseHealthChecker,
    ingestion_runner: IngestionRunner | None = None,
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

    if arguments.command == "snapshot" and arguments.snapshot_command == "validate":
        try:
            snapshot_document = load_snapshot_manifest(arguments.manifest)
        except (OSError, ValueError, BisfinError) as error:
            print(f"snapshot: invalid ({redact_secrets(error)})", file=stderr)
            return 2
        _print_json_or_human(
            {
                "snapshot_code": snapshot_document.request.snapshot_code,
                "source_manifest_sha256": snapshot_document.source_manifest_sha256,
                "specification_sha256": snapshot_document.specification_sha256,
            },
            output_format="human",
            prefix="snapshot: valid",
            stdout=stdout,
        )
        return 0

    if arguments.command == "snapshot":
        snapshot_engine: Engine | None = None
        try:
            snapshot_engine = engine_factory(settings)
            from bisfin.repositories.snapshot_repository import SnapshotRecord

            snapshot_result: SnapshotBuildResult | SnapshotVerificationResult | SnapshotRecord
            if arguments.snapshot_command == "build":
                snapshot_result = SnapshotBuilder(snapshot_engine).build(
                    load_snapshot_manifest(arguments.manifest), output_dir=arguments.output_dir
                )
            elif arguments.snapshot_command == "verify":
                snapshot_result = SnapshotVerifier(snapshot_engine).verify(
                    arguments.code, against_db=arguments.against_db
                )
            elif arguments.snapshot_command == "show":
                from bisfin.db.transaction import TransactionManager
                from bisfin.repositories.snapshot_repository import SqlAlchemySnapshotRepository

                with TransactionManager(snapshot_engine).begin(read_only=True) as connection:
                    record = SqlAlchemySnapshotRepository(connection).get_by_code(arguments.code)
                    if record is None:
                        raise ValueError("snapshot code was not found")
                    snapshot_result = record
            else:
                raise AssertionError("unhandled snapshot command")
        except (BisfinError, ValueError, OSError) as error:
            print(f"snapshot: failed ({redact_secrets(error)})", file=stderr)
            return 4
        finally:
            if snapshot_engine is not None:
                dispose_engine(snapshot_engine)
        _print_json_or_human(
            snapshot_result.model_dump(mode="json"),
            output_format=arguments.output_format,
            prefix="snapshot: complete",
            stdout=stdout,
        )
        return 0 if getattr(snapshot_result, "verified", True) else 5

    if arguments.command == "catalog" and arguments.catalog_command == "validate":
        try:
            document = load_catalog_manifest(arguments.manifest)
        except (OSError, ValueError) as error:
            print(f"catalog: invalid ({redact_secrets(error)})", file=stderr)
            return 2
        _print_json_or_human(
            {
                "manifest_id": document.manifest.manifest_id,
                "payload_sha256": document.payload_sha256,
                "instruments": len(document.manifest.instruments),
            },
            output_format="human",
            prefix="catalog: valid",
            stdout=stdout,
        )
        return 0

    if arguments.command == "calendar" and arguments.calendar_command == "validate":
        try:
            validated = validate_calendar_manifest(load_calendar_manifest(arguments.file))
        except (OSError, ValueError) as error:
            print(f"calendar: invalid ({redact_secrets(error)})", file=stderr)
            return 2
        _print_json_or_human(
            {
                "calendar_id": validated.document.manifest.calendar_id,
                "payload_sha256": validated.document.payload_sha256,
                "sessions": len(validated.sessions),
            },
            output_format="human",
            prefix="calendar: valid",
            stdout=stdout,
        )
        return 0

    if arguments.command == "ingest":
        configure_logging(
            level=settings.log_level,
            log_format=settings.log_format,
            environment=settings.environment,
            application=settings.application,
        )
        runner = ingestion_runner or _run_brsapi_daily_ingestion
        try:
            result = runner(arguments, settings)
        except (BisfinError, BrsApiError, ValueError) as error:
            if arguments.output_format == "json":
                print(
                    json.dumps(
                        {
                            "error": "ingestion_failed",
                            "message": redact_secrets(error),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    file=stderr,
                )
            else:
                print(f"ingestion: failed ({redact_secrets(error)})", file=stderr)
            return 4

        _print_ingestion_result(result, output_format=arguments.output_format, stdout=stdout)
        if result.status is IngestionBatchStatus.SUCCEEDED:
            return 0
        if result.status is IngestionBatchStatus.PARTIAL:
            return 3
        return 4

    if arguments.command in {"catalog", "calendar"}:
        configure_logging(
            level=settings.log_level,
            log_format=settings.log_format,
            environment=settings.environment,
            application=settings.application,
        )
        operation_engine: Engine | None = None
        catalog_result: CatalogBootstrapResult | CalendarImportResult
        try:
            operation_engine = engine_factory(settings)
            factory = create_unit_of_work_factory(operation_engine)
            if arguments.command == "catalog":
                document = load_catalog_manifest(arguments.manifest)
                mode = CatalogValidationMode(
                    arguments.validation_mode or settings.catalog_default_validation_mode
                )
                client = _symbol_client(arguments, settings, mode)
                catalog_result = CatalogBootstrapService(
                    unit_of_work_factory=factory.create_temporal_write
                ).bootstrap(
                    document,
                    validation_mode=mode,
                    symbol_client=client,
                    request_id=arguments.request_id,
                )
            else:
                validated = validate_calendar_manifest(load_calendar_manifest(arguments.file))
                catalog_result = TradingCalendarImportService(
                    unit_of_work_factory=factory
                ).import_calendar(
                    validated,
                    request_id=arguments.request_id,
                )
        except (BisfinError, ValueError, OSError) as error:
            print(f"{arguments.command}: failed ({redact_secrets(error)})", file=stderr)
            return 4
        finally:
            if operation_engine is not None:
                dispose_engine(operation_engine)
        _print_json_or_human(
            catalog_result.model_dump(mode="json"),
            output_format=arguments.output_format,
            prefix=f"{arguments.command}: {catalog_result.status.value.lower()}",
            stdout=stdout,
        )
        return 0 if catalog_result.status is IngestionBatchStatus.SUCCEEDED else 3

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


def _run_brsapi_daily_ingestion(
    arguments: argparse.Namespace,
    settings: Settings,
) -> DailyBarIngestionResult:
    """Compose the one-shot service while guaranteeing Engine disposal."""

    if arguments.ingest_command != "brsapi-daily-bars":
        raise AssertionError(f"unhandled ingest command: {arguments.ingest_command}")

    client: BrsApiClient
    if arguments.fixture is not None:
        client = FixtureBrsApiClient(arguments.fixture)
    else:
        api_key = settings.brsapi_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise BrsApiConfigurationError("BRSAPI_API_KEY is required for live mode.")
        client = HttpxBrsApiClient(
            base_url=settings.brsapi_base_url,
            api_key=api_key,
            connect_timeout_seconds=settings.brsapi_connect_timeout_seconds,
            read_timeout_seconds=settings.brsapi_read_timeout_seconds,
            user_agent=settings.brsapi_user_agent,
        )

    engine: Engine | None = None
    try:
        engine = create_engine(settings)
        service = BrsApiDailyBarIngestionService(
            client=client,
            unit_of_work_factory=create_unit_of_work_factory(engine),
            settings=settings,
        )
        return service.ingest(
            symbol=arguments.symbol,
            request_id=arguments.request_id,
        )
    finally:
        if engine is not None:
            dispose_engine(engine)


def _symbol_client(
    arguments: argparse.Namespace,
    settings: Settings,
    mode: CatalogValidationMode,
) -> FixtureBrsApiSymbolClient | HttpxBrsApiSymbolClient | None:
    if mode is CatalogValidationMode.MANIFEST_ONLY:
        return None
    if mode is CatalogValidationMode.FIXTURE_VALIDATE:
        if arguments.symbol_fixture_dir is None:
            raise ValueError("fixture-validate requires --symbol-fixture-dir")
        return FixtureBrsApiSymbolClient(arguments.symbol_fixture_dir)
    if settings.brsapi_api_key is None or not settings.brsapi_api_key.get_secret_value().strip():
        raise BrsApiConfigurationError("BRSAPI_API_KEY is required for live mode.")
    return HttpxBrsApiSymbolClient(
        base_url=settings.brsapi_base_url,
        api_key=settings.brsapi_api_key,
        connect_timeout_seconds=settings.brsapi_connect_timeout_seconds,
        read_timeout_seconds=settings.brsapi_read_timeout_seconds,
        user_agent=settings.brsapi_user_agent,
    )


def _print_json_or_human(
    value: dict[str, object],
    *,
    output_format: str,
    prefix: str,
    stdout: TextIO,
) -> None:
    if output_format == "json":
        print(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            file=stdout,
        )
        return
    print(prefix, file=stdout)
    for key, item in value.items():
        print(f"{key}={item}", file=stdout)


def _print_ingestion_result(
    result: DailyBarIngestionResult,
    *,
    output_format: str,
    stdout: TextIO,
) -> None:
    if output_format == "json":
        print(
            json.dumps(
                result.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=stdout,
        )
        return
    print(
        f"ingestion: {result.status.value.lower()} "
        f"batch={result.ingestion_batch_id} symbol={result.symbol}",
        file=stdout,
    )
    print(
        f"rows received={result.received_count} accepted={result.accepted_count} "
        f"rejected={result.rejected_count} raw={result.raw_inserted_count}",
        file=stdout,
    )
    print(
        f"bars inserted={result.bar_inserted_count} corrected={result.bar_corrected_count} "
        f"unchanged={result.bar_unchanged_count}",
        file=stdout,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point."""

    return run(sys.argv[1:] if argv is None else argv)


__all__ = ["build_parser", "main", "run"]
