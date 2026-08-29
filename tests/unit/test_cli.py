"""CLI behavior without external services, plus the installed entry-point smoke test."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import create_engine as create_sqlalchemy_engine

from bisfin.cli import _run_brsapi_daily_ingestion, build_parser, run
from bisfin.config import Settings
from bisfin.db.health import DatabaseHealthReport, HealthCheckResult
from bisfin.domain.ingestion import IngestionBatchStatus
from bisfin.ingestion.results import DailyBarIngestionResult
from bisfin.integrations.brsapi import BrsApiConfigurationError


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "postgres_password": "cli-super-secret",
            "database_url": "postgresql://user:url-super-secret@db.example/bisfin",
        }
    )


def _report(*, healthy: bool = True, current: str | None = "0004") -> DatabaseHealthReport:
    return DatabaseHealthReport(
        checked_at=datetime.now(UTC),
        expected_revision="0004",
        current_revision=current,
        postgresql_major_version=16,
        checks=(HealthCheckResult("connectivity", healthy, "test result"),),
    )


def _run_database_command(command: str, report: DatabaseHealthReport) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    engine = create_sqlalchemy_engine("sqlite://")
    code = run(
        [command],
        settings_factory=_settings,
        engine_factory=lambda _settings: engine,
        health_checker_factory=lambda _engine: _FakeHealthChecker(report),
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


class _FakeHealthChecker:
    def __init__(self, report: DatabaseHealthReport) -> None:
        self._report = report

    def check(self) -> DatabaseHealthReport:
        return self._report


def test_config_check_prints_only_non_secret_settings() -> None:
    stdout = StringIO()
    stderr = StringIO()

    code = run(["config-check"], settings_factory=_settings, stdout=stdout, stderr=stderr)

    assert code == 0
    assert "configuration: ok" in stdout.getvalue()
    assert "cli-super-secret" not in stdout.getvalue()
    assert "url-super-secret" not in stdout.getvalue()
    assert "postgresql" not in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_config_check_returns_two_without_echoing_invalid_input() -> None:
    stdout = StringIO()
    stderr = StringIO()

    code = run(
        ["config-check"],
        settings_factory=lambda: Settings(postgres_port=70_000),
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert "postgres_port" in stderr.getvalue()
    assert "70000" not in stderr.getvalue()


def test_config_check_rejects_malformed_database_url_without_echoing_secret() -> None:
    stdout = StringIO()
    stderr = StringIO()
    secret = "cli-malformed-url-secret"

    code = run(
        ["config-check"],
        settings_factory=lambda: Settings.model_validate(
            {"database_url": f"postgresql://worker:{secret}@[bad/db"}
        ),
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert "configuration: invalid" in stderr.getvalue()
    assert secret not in stderr.getvalue()


def test_db_health_exit_code_follows_structured_health() -> None:
    healthy_code, healthy_output, _ = _run_database_command("db-health", _report())
    failed_code, failed_output, _ = _run_database_command("db-health", _report(healthy=False))

    assert healthy_code == 0
    assert "connectivity=ok" in healthy_output
    assert failed_code == 1
    assert "connectivity=failed" in failed_output


def test_db_current_reports_and_rejects_revision_mismatch() -> None:
    code, output, _ = _run_database_command("db-current", _report(current="0002"))

    assert code == 1
    assert "current_revision=0002" in output
    assert "expected_revision=0004" in output


def test_installed_console_entry_point_config_check() -> None:
    executable = shutil.which("bisfin")
    if executable is None:
        executable_name = "bisfin.exe" if os.name == "nt" else "bisfin"
        installed_script = Path(sys.executable).resolve().parent / executable_name
        executable = str(installed_script) if installed_script.is_file() else None
    assert executable is not None, "uv sync must install the bisfin console script"
    environment = os.environ.copy()
    environment.update(
        {
            "BISFIN_ENV": "test",
            "POSTGRES_PASSWORD": "subprocess-super-secret",
            "DATABASE_URL": "",
        }
    )

    completed = subprocess.run(
        [executable, "config-check"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env=environment,
    )

    assert completed.returncode == 0
    assert "configuration: ok" in completed.stdout
    assert "subprocess-super-secret" not in completed.stdout
    assert completed.stderr == ""


def _ingestion_result(status: IngestionBatchStatus) -> DailyBarIngestionResult:
    timestamp = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
    return DailyBarIngestionResult(
        ingestion_batch_id=17,
        status=status,
        provider_code="BRSAPI",
        feed_code="TSETMC_CANDLE_DAILY_RAW",
        symbol="فملی",
        received_count=3,
        accepted_count=2,
        rejected_count=1,
        raw_inserted_count=3,
        bar_inserted_count=1,
        bar_corrected_count=0,
        bar_unchanged_count=1,
        source_watermark="2025-03-01",
        payload_sha256="a" * 64,
        started_at=timestamp,
        finished_at=timestamp,
    )


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        (IngestionBatchStatus.SUCCEEDED, 0),
        (IngestionBatchStatus.PARTIAL, 3),
        (IngestionBatchStatus.FAILED, 4),
        (IngestionBatchStatus.QUARANTINED, 4),
    ],
)
def test_ingestion_exit_codes_and_fixture_arguments(
    status: IngestionBatchStatus,
    expected_code: int,
) -> None:
    stdout = StringIO()
    stderr = StringIO()
    captured: dict[str, object] = {}

    def runner(arguments: object, settings: Settings) -> DailyBarIngestionResult:
        captured["arguments"] = arguments
        captured["settings"] = settings
        return _ingestion_result(status)

    code = run(
        [
            "ingest",
            "brsapi-daily-bars",
            "--symbol",
            "فملی",
            "--request-id",
            "request-17",
            "--fixture",
            "tests/fixtures/brsapi/candlestick_type2_success.json",
        ],
        settings_factory=_settings,
        ingestion_runner=runner,
        stdout=stdout,
        stderr=stderr,
    )

    arguments = captured["arguments"]
    assert code == expected_code
    assert getattr(arguments, "symbol") == "فملی"
    assert getattr(arguments, "request_id") == "request-17"
    assert isinstance(getattr(arguments, "fixture"), Path)
    assert "batch=17" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_ingestion_json_output_is_secret_free_and_machine_readable() -> None:
    stdout = StringIO()
    stderr = StringIO()

    code = run(
        [
            "ingest",
            "brsapi-daily-bars",
            "--symbol",
            "فملی",
            "--fixture",
            "fixture.json",
            "--output-format",
            "json",
        ],
        settings_factory=_settings,
        ingestion_runner=lambda _arguments, _settings: _ingestion_result(
            IngestionBatchStatus.SUCCEEDED
        ),
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert '"status":"SUCCEEDED"' in stdout.getvalue()
    assert '"symbol":"فملی"' in stdout.getvalue()
    assert "cli-super-secret" not in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_live_ingestion_requires_api_key_before_engine_creation() -> None:
    arguments = build_parser().parse_args(["ingest", "brsapi-daily-bars", "--symbol", "فملی"])
    settings = _settings()

    with pytest.raises(BrsApiConfigurationError, match="required for live mode"):
        _run_brsapi_daily_ingestion(arguments, settings)


def test_snapshot_validate_is_database_independent_and_reports_hashes(tmp_path: Path) -> None:
    manifest = tmp_path / "snapshot.json"
    manifest.write_text(
        """
        {"schema_version":1,"snapshot_code":"cli-snapshot","knowledge_cutoff_ts":"2026-01-02T00:00:00Z","availability_mode":"PUBLIC_REPLAY","components":[{"component_key":"daily","kind":"BAR_REVISION","bar_series_id":1,"event_from":"2026-01-01T00:00:00Z","event_to":"2026-01-02T00:00:00Z","allow_empty":true}]}
        """,
        encoding="utf-8",
    )
    stdout = StringIO()
    stderr = StringIO()

    code = run(
        ["snapshot", "validate", "--manifest", str(manifest)],
        settings_factory=_settings,
        engine_factory=lambda _settings: pytest.fail("validate must not create an engine"),
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert "snapshot: valid" in stdout.getvalue()
    assert "source_manifest_sha256=" in stdout.getvalue()
    assert stderr.getvalue() == ""
