"""Real-PostgreSQL PIT eligibility coverage using test-owned bar series."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from tests.fixtures import unique_code
from tests.integration.snapshot_support import (
    SnapshotSeries,
    component,
    insert_revision,
    manifest_bytes,
    seed_snapshot_series,
)

from bisfin.snapshots.builder import SnapshotBuilder
from bisfin.snapshots.contracts import SnapshotBuildResult
from bisfin.snapshots.errors import SnapshotBuildError
from bisfin.snapshots.manifest import SnapshotManifestDocument, parse_snapshot_manifest_bytes

_OPEN = datetime(2029, 1, 1, tzinfo=UTC)
_CUTOFF = datetime(2029, 1, 5, tzinfo=UTC)


def _document(
    code: str,
    series: SnapshotSeries,
    *,
    mode: str = "PUBLIC_REPLAY",
    allow_empty: bool = False,
) -> SnapshotManifestDocument:
    return parse_snapshot_manifest_bytes(
        manifest_bytes(
            snapshot_code=code,
            cutoff=_CUTOFF,
            mode=mode,
            components=[
                component(
                    "daily",
                    series,
                    event_from=_OPEN,
                    event_to=_OPEN + timedelta(days=2),
                    allow_empty=allow_empty,
                )
            ],
        )
    )


def _revision_numbers(result: SnapshotBuildResult, tmp_path: Path) -> list[int]:
    component_result = result.components[0]
    artifact = tmp_path / result.snapshot_code / component_result.relative_storage_path
    return [
        json.loads(line)["revision_no"]
        for line in artifact.read_text(encoding="utf-8").splitlines()
    ]


def test_snapshot_excludes_non_final_and_bar_close_after_cutoff(
    db_engine: Engine, snapshot_artifact_dir: Path
) -> None:
    series = seed_snapshot_series(db_engine)
    insert_revision(
        db_engine,
        series,
        bar_open_ts=_OPEN,
        revision_no=1,
        available_at=_OPEN + timedelta(days=1),
    )
    insert_revision(
        db_engine,
        series,
        bar_open_ts=_OPEN,
        revision_no=2,
        available_at=_OPEN + timedelta(days=1),
        is_final=False,
    )
    # The schema forbids a final row from becoming available before it closes;
    # this is the first anti-lookahead barrier, ahead of the snapshot SQL filter.
    with pytest.raises(IntegrityError):
        insert_revision(
            db_engine,
            series,
            bar_open_ts=_OPEN + timedelta(days=1),
            revision_no=1,
            available_at=_OPEN + timedelta(days=1),
            bar_close_ts=_CUTOFF + timedelta(seconds=1),
        )

    result = SnapshotBuilder(db_engine).build(
        _document(unique_code("SNAP_ELIGIBILITY"), series), output_dir=snapshot_artifact_dir
    )

    assert result.components[0].row_count == 1
    assert _revision_numbers(result, snapshot_artifact_dir) == [1]


def test_snapshot_public_and_actual_system_replay_preserve_correction_history(
    db_engine: Engine, snapshot_artifact_dir: Path
) -> None:
    series = seed_snapshot_series(db_engine)
    insert_revision(
        db_engine,
        series,
        bar_open_ts=_OPEN,
        revision_no=1,
        available_at=_OPEN + timedelta(days=1),
    )
    insert_revision(
        db_engine,
        series,
        bar_open_ts=_OPEN,
        revision_no=2,
        available_at=_OPEN + timedelta(days=2),
        system_available_at=_CUTOFF + timedelta(days=1),
        close_price=11,
    )
    builder = SnapshotBuilder(db_engine)
    public = builder.build(
        _document(unique_code("SNAP_PUBLIC"), series), output_dir=snapshot_artifact_dir
    )
    actual = builder.build(
        _document(
            unique_code("SNAP_ACTUAL"),
            series,
            mode="ACTUAL_SYSTEM_REPLAY",
        ),
        output_dir=snapshot_artifact_dir,
    )

    assert public.components[0].row_count == 2
    assert actual.components[0].row_count == 1
    assert _revision_numbers(public, snapshot_artifact_dir) == [1, 2]
    assert _revision_numbers(actual, snapshot_artifact_dir) == [1]


def test_adjusted_series_requires_provenance_not_newer_than_snapshot_cutoff(
    db_engine: Engine, snapshot_artifact_dir: Path
) -> None:
    future_series = seed_snapshot_series(db_engine, adjusted_cutoff=_CUTOFF + timedelta(days=1))
    insert_revision(
        db_engine,
        future_series,
        bar_open_ts=_OPEN,
        revision_no=1,
        available_at=_OPEN + timedelta(days=1),
    )
    future_code = unique_code("SNAP_ADJUSTED_FUTURE")

    with pytest.raises(SnapshotBuildError, match="provenance"):
        SnapshotBuilder(db_engine).build(
            _document(future_code, future_series), output_dir=snapshot_artifact_dir
        )

    valid_series = seed_snapshot_series(db_engine, adjusted_cutoff=_CUTOFF)
    insert_revision(
        db_engine,
        valid_series,
        bar_open_ts=_OPEN,
        revision_no=1,
        available_at=_OPEN + timedelta(days=1),
    )
    valid = SnapshotBuilder(db_engine).build(
        _document(unique_code("SNAP_ADJUSTED_VALID"), valid_series),
        output_dir=snapshot_artifact_dir,
    )

    assert valid.status.value == "FROZEN"
