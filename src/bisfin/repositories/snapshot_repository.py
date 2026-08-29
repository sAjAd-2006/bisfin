"""SQLAlchemy Core persistence for the explicit snapshot lifecycle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, RowMapping

from bisfin.db.errors import InvalidStateTransitionError, translate_database_errors
from bisfin.db.tables import data_snapshot, data_snapshot_component
from bisfin.domain.common import ImmutableDTO
from bisfin.domain.market_data import ReplayMode
from bisfin.snapshots.contracts import SnapshotComponentResult, SnapshotStatus


class SnapshotRecord(ImmutableDTO):
    data_snapshot_id: int
    snapshot_code: str
    knowledge_cutoff_ts: datetime
    availability_mode: ReplayMode
    manifest_sha256: str | None = None
    status: SnapshotStatus
    created_at: datetime
    frozen_at: datetime | None = None
    metadata: dict[str, object]


def _record(row: RowMapping) -> SnapshotRecord:
    return SnapshotRecord.model_validate(dict(row))


class SqlAlchemySnapshotRepository:
    """Snapshot lifecycle operations; every method relies on caller transaction control."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_by_code(self, snapshot_code: str, *, for_update: bool = False) -> SnapshotRecord | None:
        statement = select(data_snapshot).where(data_snapshot.c.snapshot_code == snapshot_code)
        if for_update:
            statement = statement.with_for_update()
        with translate_database_errors(operation="get data snapshot by code"):
            row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else _record(row)

    def get_by_id(
        self, data_snapshot_id: int, *, for_update: bool = False
    ) -> SnapshotRecord | None:
        statement = select(data_snapshot).where(
            data_snapshot.c.data_snapshot_id == data_snapshot_id
        )
        if for_update:
            statement = statement.with_for_update()
        with translate_database_errors(operation="get data snapshot by id"):
            row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else _record(row)

    def create_building(
        self,
        *,
        snapshot_code: str,
        knowledge_cutoff_ts: datetime,
        availability_mode: ReplayMode,
        metadata: Mapping[str, object],
    ) -> SnapshotRecord:
        statement = (
            insert(data_snapshot)
            .values(
                snapshot_code=snapshot_code,
                knowledge_cutoff_ts=knowledge_cutoff_ts,
                availability_mode=availability_mode.value,
                status=SnapshotStatus.BUILDING.value,
                metadata=dict(metadata),
            )
            .returning(*data_snapshot.c)
        )
        with translate_database_errors(operation="create BUILDING data snapshot"):
            row = self._connection.execute(statement).mappings().one()
        return _record(row)

    def list_components(self, data_snapshot_id: int) -> tuple[dict[str, object], ...]:
        statement = (
            select(data_snapshot_component)
            .where(data_snapshot_component.c.data_snapshot_id == data_snapshot_id)
            .order_by(data_snapshot_component.c.component_key)
        )
        with translate_database_errors(operation="list data snapshot components"):
            rows = self._connection.execute(statement).mappings().all()
        return tuple(dict(row) for row in rows)

    def insert_components(
        self,
        data_snapshot_id: int,
        components: Sequence[SnapshotComponentResult],
    ) -> None:
        existing = self.get_by_id(data_snapshot_id, for_update=True)
        if existing is None or existing.status is not SnapshotStatus.BUILDING:
            raise InvalidStateTransitionError(
                "Snapshot components may only be inserted for BUILDING snapshots."
            )
        values = [
            {
                "data_snapshot_id": data_snapshot_id,
                "component_key": component.component_key,
                "feed_id": component.feed_id,
                "event_from": component.event_from,
                "event_to": component.event_to,
                "max_available_at": component.max_available_at,
                "max_system_available_at": component.max_system_available_at,
                "row_count": component.row_count,
                "component_sha256": component.component_sha256,
                "storage_uri": component.storage_uri,
            }
            for component in components
        ]
        if not values:
            return
        with translate_database_errors(operation="insert data snapshot components"):
            self._connection.execute(insert(data_snapshot_component), values)

    def freeze(
        self,
        data_snapshot_id: int,
        *,
        manifest_sha256: str,
        frozen_at: datetime,
        metadata: Mapping[str, object],
    ) -> SnapshotRecord:
        existing = self.get_by_id(data_snapshot_id, for_update=True)
        if existing is None or existing.status is not SnapshotStatus.BUILDING:
            raise InvalidStateTransitionError("Only a BUILDING snapshot may be frozen.")
        statement = (
            update(data_snapshot)
            .where(data_snapshot.c.data_snapshot_id == data_snapshot_id)
            .values(
                status=SnapshotStatus.FROZEN.value,
                manifest_sha256=manifest_sha256,
                frozen_at=frozen_at,
                metadata=dict(metadata),
            )
            .returning(*data_snapshot.c)
        )
        with translate_database_errors(operation="freeze data snapshot"):
            row = self._connection.execute(statement).mappings().one()
        return _record(row)

    def mark_failed(self, data_snapshot_id: int, *, metadata: Mapping[str, object]) -> None:
        existing = self.get_by_id(data_snapshot_id, for_update=True)
        if existing is None or existing.status is not SnapshotStatus.BUILDING:
            return
        statement = (
            update(data_snapshot)
            .where(data_snapshot.c.data_snapshot_id == data_snapshot_id)
            .values(status=SnapshotStatus.FAILED.value, metadata=dict(metadata))
        )
        with translate_database_errors(operation="mark data snapshot failed"):
            self._connection.execute(statement)


__all__ = ["SnapshotRecord", "SqlAlchemySnapshotRepository"]
