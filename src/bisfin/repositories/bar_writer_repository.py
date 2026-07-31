"""Concurrency-safe RAW daily series creation and append-only bar revisions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, Final

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Connection, RowMapping

from bisfin.db.errors import (
    EntityNotFoundError,
    IntegrityViolationError,
    translate_database_errors,
)
from bisfin.db.tables import bar_revision, bar_series, timeframe
from bisfin.domain.market_data import (
    BarRevision,
    BarRevisionCandidate,
    BarRevisionWriteResult,
    BarRevisionWriteStatus,
    BarSeries,
)

_COMPARISON_FIELDS: Final[tuple[str, ...]] = (
    "bar_close_ts",
    "trading_date",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "volume",
    "official_close_price",
    "settlement_price",
    "quote_volume",
    "trade_count",
    "vwap",
    "open_interest",
    "is_final",
    "quality_flags",
    "previous_close_price",
)

_PARTITION_LOCK = text(
    """
    SELECT pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            pg_catalog.jsonb_build_array(
                'market.bar_revision.partition',
                CAST(:month AS DATE)
            )::TEXT,
            0
        )
    )
    """
).bindparams(bindparam("month"))

_CREATE_PARTITION = text(
    "SELECT market.create_bar_month_partition(CAST(:month AS DATE), 0)"
).bindparams(bindparam("month"))

_BAR_LOCK = text(
    """
    SELECT pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            pg_catalog.jsonb_build_array(
                'market.bar_revision',
                CAST(:bar_series_id AS BIGINT),
                CAST(:bar_open_ts AS TIMESTAMPTZ)
            )::TEXT,
            0
        )
    )
    """
).bindparams(bindparam("bar_series_id"), bindparam("bar_open_ts"))


def _bar_series_from_row(row: RowMapping) -> BarSeries:
    return BarSeries.model_validate(dict(row))


def _bar_revision_from_row(row: RowMapping) -> BarRevision:
    return BarRevision.model_validate(dict(row))


class SqlAlchemyBarWriterRepository:
    """Write canonical bars without updating or deleting prior revisions."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def ensure_month_partitions(self, trading_dates: Iterable[date]) -> None:
        """Lock and create each UTC month in a stable deadlock-safe order."""

        months = sorted({value.replace(day=1) for value in trading_dates})
        with translate_database_errors(operation="ensure bar month partitions"):
            for month in months:
                self._connection.execute(_PARTITION_LOCK, {"month": month})
                self._connection.execute(_CREATE_PARTITION, {"month": month})

    def get_or_create_daily_raw_series(
        self,
        *,
        feed_id: int,
        instrument_id: int,
        timeframe_id: int,
        metadata: Mapping[str, Any] | None = None,
    ) -> BarSeries:
        """Resolve the exact RAW/1d/LAST_TRADE/REGULAR series identity."""

        operation = "get or create daily RAW bar series"
        with translate_database_errors(operation=operation):
            resolved_timeframe = self._connection.execute(
                select(timeframe.c.timeframe_id).where(
                    timeframe.c.timeframe_id == timeframe_id,
                    timeframe.c.timeframe_code == "1d",
                    timeframe.c.calendar_unit == "SESSION",
                    timeframe.c.session_aligned.is_(True),
                )
            ).scalar_one_or_none()
            if resolved_timeframe is None:
                raise EntityNotFoundError(
                    "The required session-aligned 1d timeframe does not exist.",
                    operation=operation,
                )

            values: dict[str, object] = {
                "feed_id": feed_id,
                "instrument_id": instrument_id,
                "timeframe_id": timeframe_id,
                "price_basis": "RAW",
                "adjustment_set_id": None,
                "close_semantics": "LAST_TRADE",
                "session_code": "REGULAR",
            }
            if metadata is not None:
                values["metadata"] = dict(metadata)
            inserted = (
                self._connection.execute(
                    postgresql_insert(bar_series)
                    .values(**values)
                    .on_conflict_do_nothing()
                    .returning(*bar_series.c)
                )
                .mappings()
                .one_or_none()
            )
            if inserted is not None:
                return _bar_series_from_row(inserted)

            existing = (
                self._connection.execute(
                    select(bar_series).where(
                        bar_series.c.feed_id == feed_id,
                        bar_series.c.instrument_id == instrument_id,
                        bar_series.c.timeframe_id == timeframe_id,
                        bar_series.c.price_basis == "RAW",
                        bar_series.c.adjustment_set_id.is_(None),
                        bar_series.c.close_semantics == "LAST_TRADE",
                        bar_series.c.session_code == "REGULAR",
                    )
                )
                .mappings()
                .one_or_none()
            )
        if existing is None:
            raise IntegrityViolationError(
                "A bar-series insert conflicted without the canonical identity being visible.",
                operation=operation,
            )
        return _bar_series_from_row(existing)

    def append_revision_if_changed(
        self,
        candidate: BarRevisionCandidate,
    ) -> BarRevisionWriteResult:
        if candidate.system_available_at < candidate.available_at:
            raise ValueError("system_available_at must not precede available_at")
        if candidate.is_final and candidate.available_at < candidate.bar_close_ts:
            raise ValueError("a final bar cannot be available before its canonical close")

        parameters = {
            "bar_series_id": candidate.bar_series_id,
            "bar_open_ts": candidate.bar_open_ts,
        }
        operation = "append bar revision if canonical values changed"
        with translate_database_errors(operation=operation):
            self._connection.execute(_BAR_LOCK, parameters)
            latest_row = (
                self._connection.execute(
                    select(bar_revision)
                    .where(
                        bar_revision.c.bar_series_id == candidate.bar_series_id,
                        bar_revision.c.bar_open_ts == candidate.bar_open_ts,
                    )
                    .order_by(bar_revision.c.revision_no.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )

            if latest_row is not None and all(
                latest_row[field] == getattr(candidate, field) for field in _COMPARISON_FIELDS
            ):
                return BarRevisionWriteResult(
                    status=BarRevisionWriteStatus.UNCHANGED,
                    revision=_bar_revision_from_row(latest_row),
                )

            revision_no = 1 if latest_row is None else int(latest_row["revision_no"]) + 1
            values = candidate.model_dump()
            values["revision_no"] = revision_no
            inserted = (
                self._connection.execute(
                    postgresql_insert(bar_revision).values(**values).returning(*bar_revision.c)
                )
                .mappings()
                .one()
            )

        return BarRevisionWriteResult(
            status=(
                BarRevisionWriteStatus.INSERTED
                if latest_row is None
                else BarRevisionWriteStatus.CORRECTED
            ),
            revision=_bar_revision_from_row(inserted),
        )


__all__ = ["SqlAlchemyBarWriterRepository"]
