"""Mode-specific enumeration of all PIT-eligible bar-revision history."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import BigInteger, bindparam, text
from sqlalchemy.engine import Connection

from bisfin.db.errors import EntityNotFoundError, translate_database_errors
from bisfin.domain.market_data import ReplayMode
from bisfin.snapshots.errors import SnapshotBuildError

_SERIES_QUERY = text(
    """
    SELECT series.bar_series_id, series.feed_id, series.adjustment_set_id,
           adjustment.knowledge_cutoff_ts AS adjustment_knowledge_cutoff_ts
    FROM market.bar_series AS series
    LEFT JOIN catalog.adjustment_set AS adjustment
      ON adjustment.adjustment_set_id = series.adjustment_set_id
    WHERE series.bar_series_id = CAST(:bar_series_id AS BIGINT)
    """
).bindparams(bindparam("bar_series_id", type_=BigInteger))

_COMMON_COLUMNS = """
    bar_open_ts, bar_series_id, revision_no, available_at, system_available_at,
    bar_close_ts, trading_date, open_price, high_price, low_price, close_price,
    official_close_price, settlement_price, volume, quote_volume, trade_count,
    vwap, open_interest, is_final, quality_flags, ingestion_batch_id, recorded_at,
    previous_close_price
"""

_PUBLIC_QUERY = text(
    f"""
    SELECT {_COMMON_COLUMNS}, available_at AS effective_available_at
    FROM market.bar_revision
    WHERE bar_series_id = CAST(:bar_series_id AS BIGINT)
      AND bar_open_ts >= CAST(:event_from AS TIMESTAMPTZ)
      AND bar_open_ts < CAST(:event_to AS TIMESTAMPTZ)
      AND bar_close_ts <= CAST(:knowledge_cutoff_ts AS TIMESTAMPTZ)
      AND is_final = TRUE
      AND available_at <= CAST(:knowledge_cutoff_ts AS TIMESTAMPTZ)
    ORDER BY bar_open_ts ASC, bar_series_id ASC, revision_no ASC
    """
)

_ACTUAL_QUERY = text(
    f"""
    SELECT {_COMMON_COLUMNS}, system_available_at AS effective_available_at
    FROM market.bar_revision
    WHERE bar_series_id = CAST(:bar_series_id AS BIGINT)
      AND bar_open_ts >= CAST(:event_from AS TIMESTAMPTZ)
      AND bar_open_ts < CAST(:event_to AS TIMESTAMPTZ)
      AND bar_close_ts <= CAST(:knowledge_cutoff_ts AS TIMESTAMPTZ)
      AND is_final = TRUE
      AND system_available_at <= CAST(:knowledge_cutoff_ts AS TIMESTAMPTZ)
    ORDER BY bar_open_ts ASC, bar_series_id ASC, revision_no ASC
    """
)


class SqlAlchemySnapshotBarRepository:
    """Read all eligible revision candidates; deliberately never calls current_bar."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def resolve_series(self, bar_series_id: int, *, knowledge_cutoff_ts: datetime) -> int:
        with translate_database_errors(operation="resolve snapshot bar series"):
            row = (
                self._connection.execute(_SERIES_QUERY, {"bar_series_id": bar_series_id})
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise EntityNotFoundError(
                "The requested bar series does not exist.", operation="resolve snapshot bar series"
            )
        adjustment_cutoff = row["adjustment_knowledge_cutoff_ts"]
        if adjustment_cutoff is not None and adjustment_cutoff > knowledge_cutoff_ts:
            raise SnapshotBuildError("Adjusted series provenance is newer than snapshot cutoff.")
        return int(row["feed_id"])

    def eligible_revisions(
        self,
        *,
        bar_series_id: int,
        event_from: datetime,
        event_to: datetime,
        knowledge_cutoff_ts: datetime,
        availability_mode: ReplayMode,
    ) -> Sequence[dict[str, object]]:
        parameters = {
            "bar_series_id": bar_series_id,
            "event_from": event_from,
            "event_to": event_to,
            "knowledge_cutoff_ts": knowledge_cutoff_ts,
        }
        statement = (
            _PUBLIC_QUERY if availability_mode is ReplayMode.PUBLIC_REPLAY else _ACTUAL_QUERY
        )
        with translate_database_errors(operation="enumerate snapshot bar revisions"):
            rows = self._connection.execute(statement, parameters).mappings().all()
        return tuple(dict(row) for row in rows)


__all__ = ["SqlAlchemySnapshotBarRepository"]
