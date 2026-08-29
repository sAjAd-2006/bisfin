"""Proof that the snapshot read path uses one read-only REPEATABLE READ view."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Engine
from tests.integration.snapshot_support import insert_revision, seed_snapshot_series

from bisfin.db.transaction import TransactionManager
from bisfin.domain.market_data import ReplayMode
from bisfin.repositories.snapshot_bar_repository import SqlAlchemySnapshotBarRepository

_OPEN = datetime(2029, 4, 1, tzinfo=UTC)
_CUTOFF = _OPEN + timedelta(days=3)


def test_snapshot_component_reads_share_repeatable_read_read_only_view(db_engine: Engine) -> None:
    first = seed_snapshot_series(db_engine)
    second = seed_snapshot_series(db_engine)
    for series in (first, second):
        insert_revision(
            db_engine,
            series,
            bar_open_ts=_OPEN,
            revision_no=1,
            available_at=_OPEN + timedelta(days=1),
        )

    with TransactionManager(db_engine).begin(
        isolation_level="REPEATABLE READ", read_only=True
    ) as connection:
        assert (
            connection.execute(text("SHOW transaction_isolation")).scalar_one() == "repeatable read"
        )
        assert connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        repository = SqlAlchemySnapshotBarRepository(connection)
        first_rows = repository.eligible_revisions(
            bar_series_id=first.bar_series_id,
            event_from=_OPEN,
            event_to=_OPEN + timedelta(days=1),
            knowledge_cutoff_ts=_CUTOFF,
            availability_mode=ReplayMode.PUBLIC_REPLAY,
        )
        insert_revision(
            db_engine,
            second,
            bar_open_ts=_OPEN,
            revision_no=2,
            available_at=_OPEN + timedelta(days=2),
            close_price=11,
        )
        second_rows = repository.eligible_revisions(
            bar_series_id=second.bar_series_id,
            event_from=_OPEN,
            event_to=_OPEN + timedelta(days=1),
            knowledge_cutoff_ts=_CUTOFF,
            availability_mode=ReplayMode.PUBLIC_REPLAY,
        )

    assert [row["revision_no"] for row in first_rows] == [1]
    assert [row["revision_no"] for row in second_rows] == [1]
