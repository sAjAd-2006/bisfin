"""Immutable snapshot-code lifecycle and content-identity integration tests."""

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
    insert_revision,
    manifest_bytes,
    seed_snapshot_series,
    snapshot_status,
)

from bisfin.db.transaction import TransactionManager
from bisfin.domain.market_data import ReplayMode
from bisfin.repositories.snapshot_repository import SqlAlchemySnapshotRepository
from bisfin.snapshots.builder import SnapshotBuilder
from bisfin.snapshots.contracts import SnapshotStatus
from bisfin.snapshots.errors import SnapshotConflictError, SnapshotInProgressError
from bisfin.snapshots.manifest import SnapshotManifestDocument, parse_snapshot_manifest_bytes

_OPEN = datetime(2029, 3, 1, tzinfo=UTC)
_CUTOFF = _OPEN + timedelta(days=3)


def _document(code: str, series: SnapshotSeries) -> SnapshotManifestDocument:
    return parse_snapshot_manifest_bytes(
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


def _seed_eligible(engine: Engine) -> SnapshotSeries:
    series = seed_snapshot_series(engine)
    insert_revision(
        engine,
        series,
        bar_open_ts=_OPEN,
        revision_no=1,
        available_at=_OPEN + timedelta(days=1),
    )
    return series


def test_frozen_same_spec_is_idempotent_and_conflict_preserves_artifact(
    db_engine: Engine, snapshot_artifact_dir: Path
) -> None:
    series = _seed_eligible(db_engine)
    code = unique_code("SNAP_IDEMPOTENT")
    document = _document(code, series)
    builder = SnapshotBuilder(db_engine)
    initial = builder.build(document, output_dir=snapshot_artifact_dir)
    artifact = snapshot_artifact_dir / code / initial.components[0].relative_storage_path
    initial_bytes = artifact.read_bytes()
    replay = builder.build(document, output_dir=snapshot_artifact_dir)

    assert replay.idempotent_replay
    assert replay.data_snapshot_id == initial.data_snapshot_id
    assert replay.components[0].component_sha256 == initial.components[0].component_sha256
    assert artifact.read_bytes() == initial_bytes
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM catalog.data_snapshot WHERE snapshot_code = :code"),
                {"code": code},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text(
                    """
                SELECT count(*) FROM catalog.data_snapshot_component
                WHERE data_snapshot_id = :snapshot_id
                """
                ),
                {"snapshot_id": initial.data_snapshot_id},
            ).scalar_one()
            == 1
        )

    conflicting = parse_snapshot_manifest_bytes(
        manifest_bytes(
            snapshot_code=code,
            cutoff=_CUTOFF + timedelta(days=1),
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
    with pytest.raises(SnapshotConflictError):
        builder.build(conflicting, output_dir=snapshot_artifact_dir)
    assert artifact.read_bytes() == initial_bytes
    with db_engine.connect() as connection:
        record = snapshot_status(connection, code)
    assert record is not None and record["status"] == "FROZEN"


@pytest.mark.parametrize("status", [SnapshotStatus.FAILED, SnapshotStatus.DEPRECATED])
def test_failed_and_deprecated_codes_are_never_silently_reused(
    db_engine: Engine, snapshot_artifact_dir: Path, status: SnapshotStatus
) -> None:
    series = _seed_eligible(db_engine)
    code = unique_code(f"SNAP_{status.value}")
    document = _document(code, series)
    with TransactionManager(db_engine).begin() as connection:
        repository = SqlAlchemySnapshotRepository(connection)
        created = repository.create_building(
            snapshot_code=code,
            knowledge_cutoff_ts=document.request.knowledge_cutoff_ts,
            availability_mode=ReplayMode.PUBLIC_REPLAY,
            metadata={"specification_sha256": document.specification_sha256},
        )
        if status is SnapshotStatus.FAILED:
            repository.mark_failed(created.data_snapshot_id, metadata=created.metadata)
        else:
            connection.execute(
                text(
                    "UPDATE catalog.data_snapshot SET status = 'DEPRECATED' "
                    "WHERE data_snapshot_id = :snapshot_id"
                ),
                {"snapshot_id": created.data_snapshot_id},
            )

    with pytest.raises(SnapshotConflictError):
        SnapshotBuilder(db_engine).build(document, output_dir=snapshot_artifact_dir)
    with db_engine.connect() as connection:
        record = snapshot_status(connection, code)
    assert record is not None and record["status"] == status.value
    assert not (snapshot_artifact_dir / code).exists()


def test_building_code_is_rejected_without_artifact_overwrite(
    db_engine: Engine, snapshot_artifact_dir: Path
) -> None:
    series = _seed_eligible(db_engine)
    code = unique_code("SNAP_BUILDING")
    document = _document(code, series)
    with TransactionManager(db_engine).begin() as connection:
        SqlAlchemySnapshotRepository(connection).create_building(
            snapshot_code=code,
            knowledge_cutoff_ts=document.request.knowledge_cutoff_ts,
            availability_mode=ReplayMode.PUBLIC_REPLAY,
            metadata={"specification_sha256": document.specification_sha256},
        )

    with pytest.raises(SnapshotInProgressError):
        SnapshotBuilder(db_engine).build(document, output_dir=snapshot_artifact_dir)
    assert not (snapshot_artifact_dir / code).exists()


def test_different_codes_over_identical_data_have_identical_component_bytes(
    db_engine: Engine, snapshot_artifact_dir: Path
) -> None:
    series = _seed_eligible(db_engine)
    first = SnapshotBuilder(db_engine).build(
        _document(unique_code("SNAP_CONTENT_A"), series), output_dir=snapshot_artifact_dir
    )
    second = SnapshotBuilder(db_engine).build(
        _document(unique_code("SNAP_CONTENT_B"), series), output_dir=snapshot_artifact_dir
    )
    first_bytes = (
        snapshot_artifact_dir / first.snapshot_code / first.components[0].relative_storage_path
    ).read_bytes()
    second_bytes = (
        snapshot_artifact_dir / second.snapshot_code / second.components[0].relative_storage_path
    ).read_bytes()

    assert first.components[0].component_sha256 == second.components[0].component_sha256
    assert first.components[0].row_count == second.components[0].row_count
    assert first_bytes == second_bytes
    assert first.manifest_sha256 != second.manifest_sha256
