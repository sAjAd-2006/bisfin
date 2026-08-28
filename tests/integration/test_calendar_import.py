"""PostgreSQL persistence checks for explicit open and closed sessions."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine

from bisfin.calendar import load_calendar_manifest, validate_calendar_manifest
from bisfin.calendar.importer import TradingCalendarImportService
from bisfin.catalog import load_catalog_manifest
from bisfin.catalog.bootstrap import CatalogBootstrapService
from bisfin.domain.ingestion import IngestionBatchStatus
from bisfin.repositories import create_unit_of_work_factory

_CATALOG = Path("tests/fixtures/catalog/catalog_bootstrap_success.json")


def test_open_closed_sessions_and_identical_rerun(db_engine: Engine, tmp_path: Path) -> None:
    factory = create_unit_of_work_factory(db_engine)
    CatalogBootstrapService(unit_of_work_factory=factory.create_temporal_write).bootstrap(
        load_catalog_manifest(_CATALOG), request_id=f"calendar-persistence-prereq-{uuid4()}"
    )
    path = tmp_path / "calendar.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "calendar_id": f"calendar-persistence-{uuid4()}",
                "venue_code": "TSE",
                "timezone": "Asia/Tehran",
                "date_from": "2025-07-10",
                "date_to": "2025-07-11",
                "sessions": [
                    {
                        "trading_date": "2025-07-10",
                        "session_code": "REGULAR",
                        "is_trading_day": True,
                        "open_local_time": "09:00:00",
                        "close_local_time": "12:30:00",
                        "source_status": "DECLARED_OPEN",
                    },
                    {
                        "trading_date": "2025-07-11",
                        "session_code": "REGULAR",
                        "is_trading_day": False,
                        "open_local_time": None,
                        "close_local_time": None,
                        "source_status": "DECLARED_CLOSED",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    document = validate_calendar_manifest(load_calendar_manifest(path))
    service = TradingCalendarImportService(unit_of_work_factory=factory)
    first = service.import_calendar(document, request_id=f"calendar-persistence-{uuid4()}")
    second = service.import_calendar(document, request_id=f"calendar-persistence-rerun-{uuid4()}")
    assert first.status is second.status is IngestionBatchStatus.SUCCEEDED
    assert second.sessions_unchanged == 2
    with db_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT trading_date, is_trading_day, session_open_ts, session_close_ts "
                    "FROM catalog.trading_session WHERE trading_date BETWEEN "
                    "DATE '2025-07-10' AND DATE '2025-07-11' ORDER BY trading_date"
                )
            )
            .mappings()
            .all()
        )
    assert rows[0]["session_open_ts"].isoformat() == "2025-07-10T05:30:00+00:00"
    assert rows[0]["session_close_ts"].isoformat() == "2025-07-10T09:00:00+00:00"
    assert rows[1]["is_trading_day"] is False
    assert rows[1]["session_open_ts"] is None
    assert rows[1]["session_close_ts"] is None
