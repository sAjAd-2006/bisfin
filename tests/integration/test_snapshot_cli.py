"""CLI acceptance for Snapshot validate/build/show/verify commands."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

from sqlalchemy.engine import Engine
from tests.fixtures import unique_code
from tests.integration.snapshot_support import (
    component,
    insert_revision,
    manifest_bytes,
    seed_snapshot_series,
)

from bisfin.cli import run
from bisfin.config import Settings

_OPEN = datetime(2029, 5, 1, tzinfo=UTC)
_CUTOFF = _OPEN + timedelta(days=3)


def _invoke(arguments: list[str], settings: Settings) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = run(
        arguments,
        settings_factory=lambda: settings,
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_snapshot_cli_build_show_verify_drift_and_corruption(
    db_engine: Engine, db_settings: Settings, tmp_path: Path, snapshot_artifact_dir: Path
) -> None:
    series = seed_snapshot_series(db_engine)
    insert_revision(
        db_engine,
        series,
        bar_open_ts=_OPEN,
        revision_no=1,
        available_at=_OPEN + timedelta(days=1),
    )
    code = unique_code("SNAP_CLI")
    manifest = tmp_path / "snapshot.json"
    manifest.write_bytes(
        manifest_bytes(
            snapshot_code=code,
            cutoff=_CUTOFF,
            components=[
                component(
                    "daily",
                    series,
                    event_from=_OPEN,
                    event_to=_OPEN + timedelta(days=1),
                )
            ],
        )
    )
    output_dir = snapshot_artifact_dir

    validate_code, validate_output, validate_error = _invoke(
        ["snapshot", "validate", "--manifest", str(manifest)], db_settings
    )
    build_code, build_output, build_error = _invoke(
        ["snapshot", "build", "--manifest", str(manifest), "--output-dir", str(output_dir)],
        db_settings,
    )
    show_code, show_output, show_error = _invoke(["snapshot", "show", "--code", code], db_settings)
    verify_code, verify_output, verify_error = _invoke(
        ["snapshot", "verify", "--code", code, "--against-db"], db_settings
    )

    assert (validate_code, build_code, show_code, verify_code) == (0, 0, 0, 0)
    assert "snapshot: valid" in validate_output and validate_error == ""
    assert "status=FROZEN" in build_output and build_error == ""
    assert "snapshot_code=" in show_output and show_error == ""
    assert "verified=True" in verify_output and verify_error == ""

    insert_revision(
        db_engine,
        series,
        bar_open_ts=_OPEN,
        revision_no=2,
        available_at=_OPEN + timedelta(days=2),
        close_price=11,
    )
    drift_code, _, drift_error = _invoke(
        ["snapshot", "verify", "--code", code, "--against-db"], db_settings
    )
    assert drift_code == 5
    assert drift_error == ""

    artifact = next((output_dir / code / "components").glob("*.jsonl"))
    artifact.write_bytes(artifact.read_bytes() + b'{"tampered":true}\n')
    corrupt_code, _, corrupt_error = _invoke(["snapshot", "verify", "--code", code], db_settings)
    assert corrupt_code == 5
    assert corrupt_error == ""
