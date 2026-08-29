from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.engine import Connection
from tests.fixtures import unique_code

from bisfin.db.errors import InvalidStateTransitionError
from bisfin.domain.market_data import ReplayMode
from bisfin.repositories.snapshot_repository import SqlAlchemySnapshotRepository
from bisfin.snapshots.contracts import SnapshotStatus


def test_snapshot_repository_freezes_building_row_once(db_connection: Connection) -> None:
    repository = SqlAlchemySnapshotRepository(db_connection)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    created = repository.create_building(
        snapshot_code=unique_code("SNAPSHOT", max_length=128),
        knowledge_cutoff_ts=now,
        availability_mode=ReplayMode.PUBLIC_REPLAY,
        metadata={"source_manifest_sha256": "a" * 64, "specification_sha256": "b" * 64},
    )

    frozen = repository.freeze(
        created.data_snapshot_id,
        manifest_sha256="c" * 64,
        frozen_at=now,
        metadata=created.metadata,
    )

    assert frozen.status is SnapshotStatus.FROZEN
    with pytest.raises(InvalidStateTransitionError):
        repository.freeze(
            created.data_snapshot_id,
            manifest_sha256="d" * 64,
            frozen_at=now,
            metadata=created.metadata,
        )
