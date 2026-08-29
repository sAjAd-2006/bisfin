"""Snapshot failure lifecycle and empty-component tests against PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from tests.fixtures import unique_code
from tests.integration.snapshot_support import (
    SnapshotSeries,
    component,
    manifest_bytes,
    seed_snapshot_series,
    snapshot_status,
)

from bisfin.repositories.snapshot_repository import SqlAlchemySnapshotRepository
from bisfin.snapshots.artifacts import SnapshotArtifactWriter
from bisfin.snapshots.builder import SnapshotBuilder
from bisfin.snapshots.errors import SnapshotArtifactError, SnapshotBuildError, SnapshotManifestError
from bisfin.snapshots.manifest import SnapshotManifestDocument, parse_snapshot_manifest_bytes

_OPEN = datetime(2029, 2, 1, tzinfo=UTC)
_CUTOFF = _OPEN + timedelta(days=2)


def _empty_document(
    code: str, series: SnapshotSeries, *, allow_empty: bool
) -> SnapshotManifestDocument:
    return parse_snapshot_manifest_bytes(
        manifest_bytes(
            snapshot_code=code,
            cutoff=_CUTOFF,
            components=[
                component(
                    "empty",
                    series,
                    event_from=_OPEN,
                    event_to=_OPEN + timedelta(days=1),
                    allow_empty=allow_empty,
                )
            ],
        )
    )


def _record(engine: Engine, code: str) -> dict[str, object] | None:
    with engine.connect() as connection:
        return snapshot_status(connection, code)


def test_static_invalid_manifest_creates_no_snapshot_or_artifact(
    db_engine: Engine, snapshot_artifact_dir: Path
) -> None:
    code = ".unsafe-snapshot"
    payload = manifest_bytes(
        snapshot_code=code,
        cutoff=_CUTOFF,
        components=[],
    )

    with pytest.raises(SnapshotManifestError):
        parse_snapshot_manifest_bytes(payload)

    assert _record(db_engine, code) is None
    assert not (snapshot_artifact_dir / code).exists()


def test_unknown_series_becomes_failed_after_building(
    db_engine: Engine, snapshot_artifact_dir: Path
) -> None:
    code = unique_code("SNAP_UNKNOWN_SERIES")
    document = parse_snapshot_manifest_bytes(
        manifest_bytes(
            snapshot_code=code,
            cutoff=_CUTOFF,
            components=[
                {
                    "component_key": "missing",
                    "kind": "BAR_REVISION",
                    "bar_series_id": 9_999_999_999,
                    "event_from": _OPEN.isoformat().replace("+00:00", "Z"),
                    "event_to": (_OPEN + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                }
            ],
        )
    )

    with pytest.raises(SnapshotBuildError):
        SnapshotBuilder(db_engine).build(document, output_dir=snapshot_artifact_dir)

    record = _record(db_engine, code)
    assert record is not None
    assert record["status"] == "FAILED"
    assert record["manifest_sha256"] is None
    assert isinstance(record["metadata"], dict)
    assert record["metadata"]["failure"]["code"] == "EntityNotFoundError"
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    """
                SELECT count(*) FROM catalog.data_snapshot_component
                WHERE data_snapshot_id = :snapshot_id
                """
                ),
                {"snapshot_id": record["data_snapshot_id"]},
            ).scalar_one()
            == 0
        )


def test_empty_component_policy_and_deterministic_hash(
    db_engine: Engine, snapshot_artifact_dir: Path
) -> None:
    series = seed_snapshot_series(db_engine)
    rejected_code = unique_code("SNAP_EMPTY_REJECT")
    with pytest.raises(SnapshotBuildError, match="no eligible"):
        SnapshotBuilder(db_engine).build(
            _empty_document(rejected_code, series, allow_empty=False),
            output_dir=snapshot_artifact_dir,
        )
    rejected = _record(db_engine, rejected_code)
    assert rejected is not None and rejected["status"] == "FAILED"

    first = SnapshotBuilder(db_engine).build(
        _empty_document(unique_code("SNAP_EMPTY"), series, allow_empty=True),
        output_dir=snapshot_artifact_dir,
    )
    second = SnapshotBuilder(db_engine).build(
        _empty_document(unique_code("SNAP_EMPTY"), series, allow_empty=True),
        output_dir=snapshot_artifact_dir,
    )
    assert first.status.value == second.status.value == "FROZEN"
    assert first.components[0].row_count == second.components[0].row_count == 0
    assert first.components[0].component_sha256 == second.components[0].component_sha256


def test_artifact_write_failure_marks_failed_and_removes_staging(
    db_engine: Engine, snapshot_artifact_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    series = seed_snapshot_series(db_engine)
    code = unique_code("SNAP_ARTIFACT_FAILURE")

    def fail_write(self: SnapshotArtifactWriter, ordinal: int, key: str, payload: bytes) -> object:
        raise SnapshotArtifactError("controlled artifact write failure")

    monkeypatch.setattr(SnapshotArtifactWriter, "write_component", fail_write)
    with pytest.raises(SnapshotBuildError, match="controlled artifact"):
        SnapshotBuilder(db_engine).build(
            _empty_document(code, series, allow_empty=True), output_dir=snapshot_artifact_dir
        )

    record = _record(db_engine, code)
    assert record is not None and record["status"] == "FAILED"
    assert not (snapshot_artifact_dir / code).exists()
    assert not list(snapshot_artifact_dir.glob(f".{code}.staging-*"))


def test_freeze_failure_retains_published_artifact_and_marks_failed(
    db_engine: Engine, snapshot_artifact_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    series = seed_snapshot_series(db_engine)
    code = unique_code("SNAP_FREEZE_FAILURE")

    def fail_freeze(*args: object, **kwargs: object) -> object:
        raise SnapshotBuildError("controlled freeze failure")

    monkeypatch.setattr(SqlAlchemySnapshotRepository, "freeze", fail_freeze)
    with pytest.raises(SnapshotBuildError, match="controlled freeze"):
        SnapshotBuilder(db_engine).build(
            _empty_document(code, series, allow_empty=True), output_dir=snapshot_artifact_dir
        )

    record = _record(db_engine, code)
    assert record is not None and record["status"] == "FAILED"
    assert (snapshot_artifact_dir / code / "manifest.json").is_file()
