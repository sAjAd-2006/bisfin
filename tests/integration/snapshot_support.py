"""Isolated real-PostgreSQL fixtures for Point-in-Time snapshot tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from tests.fixtures import unique_code


@dataclass(frozen=True)
class SnapshotSeries:
    """Exact test-owned catalog and series identity; never select global rows."""

    provider_id: int
    feed_id: int
    instrument_id: int
    bar_series_id: int
    ingestion_batch_id: int


def seed_snapshot_series(
    engine: Engine,
    *,
    adjusted_cutoff: datetime | None = None,
) -> SnapshotSeries:
    """Create one independent provider/feed/instrument/series ownership chain."""

    provider_code = unique_code("SNAP_PROVIDER")
    feed_code = unique_code("SNAP_FEED")
    symbol = unique_code("SNAP_SYMBOL", max_length=128)
    with engine.begin() as connection:
        provider_id = int(
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.data_provider (provider_code, display_name)
                    VALUES (:code, :name) RETURNING provider_id
                    """
                ),
                {"code": provider_code, "name": provider_code},
            ).scalar_one()
        )
        feed_id = int(
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.data_feed (provider_id, feed_code, display_name, data_kind)
                    VALUES (:provider_id, :code, :name, 'BAR') RETURNING feed_id
                    """
                ),
                {"provider_id": provider_id, "code": feed_code, "name": feed_code},
            ).scalar_one()
        )
        instrument_id = int(
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.instrument (
                        asset_type_code, quote_currency_code, canonical_symbol, display_name
                    ) VALUES ('EQUITY', 'IRR', :symbol, :name)
                    RETURNING instrument_id
                    """
                ),
                {"symbol": symbol, "name": symbol},
            ).scalar_one()
        )
        timeframe_id = int(
            connection.execute(
                text("SELECT timeframe_id FROM catalog.timeframe WHERE timeframe_code = '1d'")
            ).scalar_one()
        )
        adjustment_set_id: int | None = None
        price_basis = "RAW"
        if adjusted_cutoff is not None:
            price_basis = "SPLIT_ADJUSTED"
            adjustment_set_id = int(
                connection.execute(
                    text(
                        """
                        INSERT INTO catalog.adjustment_set (
                            instrument_id, method_code, version_no, knowledge_cutoff_ts
                        ) VALUES (:instrument_id, 'SPLIT_ADJUSTED', 1, :cutoff)
                        RETURNING adjustment_set_id
                        """
                    ),
                    {"instrument_id": instrument_id, "cutoff": adjusted_cutoff},
                ).scalar_one()
            )
        bar_series_id = int(
            connection.execute(
                text(
                    """
                    INSERT INTO market.bar_series (
                        feed_id, instrument_id, timeframe_id, price_basis,
                        adjustment_set_id, close_semantics, session_code
                    ) VALUES (
                        :feed_id, :instrument_id, :timeframe_id, :price_basis,
                        :adjustment_set_id, 'LAST_TRADE', 'REGULAR'
                    ) RETURNING bar_series_id
                    """
                ),
                {
                    "feed_id": feed_id,
                    "instrument_id": instrument_id,
                    "timeframe_id": timeframe_id,
                    "price_basis": price_basis,
                    "adjustment_set_id": adjustment_set_id,
                },
            ).scalar_one()
        )
        batch_id = int(
            connection.execute(
                text(
                    """
                    INSERT INTO ingest.ingestion_batch (feed_id, parser_version, status)
                    VALUES (:feed_id, 'snapshot-test', 'SUCCEEDED')
                    RETURNING ingestion_batch_id
                    """
                ),
                {"feed_id": feed_id},
            ).scalar_one()
        )
    return SnapshotSeries(provider_id, feed_id, instrument_id, bar_series_id, batch_id)


def insert_revision(
    engine: Engine,
    series: SnapshotSeries,
    *,
    bar_open_ts: datetime,
    revision_no: int,
    available_at: datetime,
    system_available_at: datetime | None = None,
    bar_close_ts: datetime | None = None,
    is_final: bool = True,
    close_price: int = 10,
) -> None:
    """Insert an exact test-owned revision after ensuring its UTC month partition exists."""

    close_at = bar_close_ts or bar_open_ts + timedelta(hours=1)
    system_at = system_available_at or available_at
    with engine.begin() as connection:
        connection.execute(
            text("SELECT market.create_bar_month_partition(:day)"),
            {"day": date(bar_open_ts.year, bar_open_ts.month, 1)},
        )
        connection.execute(
            text(
                """
                INSERT INTO market.bar_revision (
                    bar_open_ts, bar_series_id, revision_no, available_at,
                    system_available_at, bar_close_ts, trading_date, open_price,
                    high_price, low_price, close_price, is_final, quality_flags,
                    ingestion_batch_id, recorded_at
                ) VALUES (
                    :bar_open_ts, :bar_series_id, :revision_no, :available_at,
                    :system_available_at, :bar_close_ts, :trading_date, 10, 20, 1,
                    :close_price, :is_final, 0, :ingestion_batch_id, :recorded_at
                )
                """
            ),
            {
                "bar_open_ts": bar_open_ts,
                "bar_series_id": series.bar_series_id,
                "revision_no": revision_no,
                "available_at": available_at,
                "system_available_at": system_at,
                "bar_close_ts": close_at,
                "trading_date": bar_open_ts.date(),
                "close_price": close_price,
                "is_final": is_final,
                "ingestion_batch_id": series.ingestion_batch_id,
                "recorded_at": available_at,
            },
        )


def manifest_bytes(
    *,
    snapshot_code: str,
    cutoff: datetime,
    components: list[dict[str, object]],
    mode: str = "PUBLIC_REPLAY",
) -> bytes:
    """Produce strict manifest bytes without relying on sequence-owned IDs."""

    return json.dumps(
        {
            "schema_version": 1,
            "snapshot_code": snapshot_code,
            "knowledge_cutoff_ts": cutoff.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "availability_mode": mode,
            "components": components,
        }
    ).encode("utf-8")


def component(
    key: str,
    series: SnapshotSeries,
    *,
    event_from: datetime,
    event_to: datetime,
    allow_empty: bool = False,
) -> dict[str, object]:
    return {
        "component_key": key,
        "kind": "BAR_REVISION",
        "bar_series_id": series.bar_series_id,
        "event_from": event_from.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "event_to": event_to.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "allow_empty": allow_empty,
    }


def snapshot_status(connection: Connection, snapshot_code: str) -> dict[str, object] | None:
    row = (
        connection.execute(
            text("SELECT * FROM catalog.data_snapshot WHERE snapshot_code = :snapshot_code"),
            {"snapshot_code": snapshot_code},
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else dict(row)
