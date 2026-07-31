"""Unit contracts for provider-scoped feed and canonical-session lookups."""

from datetime import UTC, date, datetime
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Connection

from bisfin.db.errors import EntityNotFoundError
from bisfin.repositories.data_feed_repository import SqlAlchemyDataFeedRepository


def _connection() -> tuple[Connection, MagicMock]:
    mock = MagicMock(spec=Connection)
    return cast(Connection, mock), mock


def test_feed_lookup_is_provider_scoped_and_never_commits() -> None:
    connection, mock = _connection()
    mock.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "feed_id": 12,
        "provider_id": 4,
        "feed_code": "DAILY",
        "display_name": "Daily",
        "data_kind": "BAR",
        "native_timezone": "Asia/Tehran",
        "parser_version": "v1",
        "active_from": None,
        "active_to": None,
        "metadata": {},
    }

    feed = SqlAlchemyDataFeedRepository(connection).get_feed_by_code(4, "DAILY")

    assert feed.provider_id == 4
    rendered = str(mock.execute.call_args.args[0])
    assert "data_feed.provider_id" in rendered
    assert "data_feed.feed_code" in rendered
    mock.commit.assert_not_called()


def test_regular_session_preserves_canonical_timestamps_for_service_validation() -> None:
    connection, mock = _connection()
    opened = datetime(2026, 7, 1, 5, 30, tzinfo=UTC)
    closed = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    mock.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "venue_id": 7,
        "trading_date": date(2026, 7, 1),
        "session_code": "REGULAR",
        "is_trading_day": True,
        "session_open_ts": opened,
        "session_close_ts": closed,
        "settlement_date": date(2026, 7, 3),
        "metadata": {},
    }

    session = SqlAlchemyDataFeedRepository(connection).get_regular_trading_session(
        7, date(2026, 7, 1)
    )

    assert session.session_open_ts == opened
    assert session.session_close_ts == closed
    assert session.session_code == "REGULAR"


def test_missing_catalog_prerequisite_raises_entity_not_found() -> None:
    connection, mock = _connection()
    mock.execute.return_value.mappings.return_value.one_or_none.return_value = None

    with pytest.raises(EntityNotFoundError):
        SqlAlchemyDataFeedRepository(connection).get_provider_by_code("MISSING")
