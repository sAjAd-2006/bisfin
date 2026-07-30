"""SQLAlchemy Core market-series and database-authoritative PIT bar reads."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Date, Integer, Numeric, bindparam, select, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, VARCHAR
from sqlalchemy.engine import Connection

from bisfin.db.errors import translate_database_errors
from bisfin.db.tables import bar_series
from bisfin.domain.common import require_aware_datetime
from bisfin.domain.market_data import BarSeries, PointInTimeBar, ReplayMode

_PIT_QUERY = (
    text(
        """
        SELECT
            bar_open_ts,
            bar_series_id,
            revision_no,
            available_at,
            system_available_at,
            bar_close_ts,
            trading_date,
            open_price,
            high_price,
            low_price,
            close_price,
            official_close_price,
            settlement_price,
            volume,
            quote_volume,
            trade_count,
            vwap,
            open_interest,
            is_final,
            quality_flags,
            ingestion_batch_id,
            recorded_at,
            previous_close_price,
            effective_available_at
        FROM market.bars_as_of(
            CAST(:bar_series_id AS BIGINT),
            CAST(:from_ts AS TIMESTAMPTZ),
            CAST(:to_ts AS TIMESTAMPTZ),
            CAST(:knowledge_cutoff_ts AS TIMESTAMPTZ),
            CAST(:replay_mode AS VARCHAR)
        )
        ORDER BY bar_open_ts, bar_series_id
        """
    )
    .bindparams(
        bindparam("bar_series_id", type_=BigInteger),
        bindparam("from_ts", type_=TIMESTAMP(timezone=True)),
        bindparam("to_ts", type_=TIMESTAMP(timezone=True)),
        bindparam("knowledge_cutoff_ts", type_=TIMESTAMP(timezone=True)),
        bindparam("replay_mode", type_=VARCHAR),
    )
    .columns(
        bar_open_ts=TIMESTAMP(timezone=True, precision=6),
        bar_series_id=BigInteger,
        revision_no=Integer,
        available_at=TIMESTAMP(timezone=True, precision=6),
        system_available_at=TIMESTAMP(timezone=True, precision=6),
        bar_close_ts=TIMESTAMP(timezone=True, precision=6),
        trading_date=Date,
        open_price=Numeric(38, 18),
        high_price=Numeric(38, 18),
        low_price=Numeric(38, 18),
        close_price=Numeric(38, 18),
        official_close_price=Numeric(38, 18),
        settlement_price=Numeric(38, 18),
        volume=Numeric(38, 18),
        quote_volume=Numeric(38, 18),
        trade_count=BigInteger,
        vwap=Numeric(38, 18),
        open_interest=Numeric(38, 18),
        is_final=Boolean,
        quality_flags=Integer,
        ingestion_batch_id=BigInteger,
        recorded_at=TIMESTAMP(timezone=True, precision=6),
        previous_close_price=Numeric(38, 18),
        effective_available_at=TIMESTAMP(timezone=True, precision=6),
    )
)


class SqlAlchemyBarRepository:
    """Read series metadata and call PostgreSQL's single audited PIT algorithm."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_series_by_id(self, bar_series_id: int) -> BarSeries | None:
        statement = select(bar_series).where(bar_series.c.bar_series_id == bar_series_id)
        with translate_database_errors(operation="get bar series by id"):
            row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else BarSeries.model_validate(dict(row))

    def get_bars_as_of(
        self,
        bar_series_id: int,
        from_ts: datetime,
        to_ts: datetime,
        knowledge_cutoff_ts: datetime,
        replay_mode: ReplayMode,
    ) -> tuple[PointInTimeBar, ...]:
        require_aware_datetime(from_ts)
        require_aware_datetime(to_ts)
        require_aware_datetime(knowledge_cutoff_ts)
        if not isinstance(replay_mode, ReplayMode):
            raise ValueError("replay_mode must be a ReplayMode")

        parameters = {
            "bar_series_id": bar_series_id,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "knowledge_cutoff_ts": knowledge_cutoff_ts,
            "replay_mode": replay_mode.value,
        }
        with translate_database_errors(operation="get Point-in-Time bars"):
            rows = self._connection.execute(_PIT_QUERY, parameters).mappings().all()
        return tuple(PointInTimeBar.model_validate(dict(row)) for row in rows)


__all__ = ["SqlAlchemyBarRepository"]
