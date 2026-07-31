"""Read-only access to pre-provisioned provider, feed, and calendar rows."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.engine import Connection, RowMapping

from bisfin.db.errors import EntityNotFoundError, translate_database_errors
from bisfin.db.tables import data_feed, data_provider, timeframe, trading_session
from bisfin.domain.catalog import DataFeed, Provider, Timeframe, TradingSession


def _required_row[T: BaseModel](
    row: RowMapping | None,
    model: type[T],
    *,
    operation: str,
) -> T:
    if row is None:
        raise EntityNotFoundError(
            "A required pre-provisioned catalog row does not exist.",
            operation=operation,
        )
    return model.model_validate(dict(row))


class SqlAlchemyDataFeedRepository:
    """Resolve catalog prerequisites through a caller-owned transaction."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_provider_by_code(self, provider_code: str) -> Provider:
        operation = "get data provider by code"
        statement = select(data_provider).where(data_provider.c.provider_code == provider_code)
        with translate_database_errors(operation=operation):
            row = self._connection.execute(statement).mappings().one_or_none()
        return _required_row(row, Provider, operation=operation)

    def get_feed_by_code(self, provider_id: int, feed_code: str) -> DataFeed:
        """Resolve a feed inside its provider-scoped database uniqueness key."""

        operation = "get provider-scoped data feed by code"
        statement = select(data_feed).where(
            data_feed.c.provider_id == provider_id,
            data_feed.c.feed_code == feed_code,
        )
        with translate_database_errors(operation=operation):
            row = self._connection.execute(statement).mappings().one_or_none()
        return _required_row(row, DataFeed, operation=operation)

    def get_timeframe_by_code(self, timeframe_code: str) -> Timeframe:
        operation = "get timeframe by code"
        statement = select(timeframe).where(timeframe.c.timeframe_code == timeframe_code)
        with translate_database_errors(operation=operation):
            row = self._connection.execute(statement).mappings().one_or_none()
        return _required_row(row, Timeframe, operation=operation)

    def get_regular_trading_session(
        self,
        venue_id: int,
        trading_date: date,
    ) -> TradingSession:
        operation = "get regular trading session"
        statement = select(trading_session).where(
            trading_session.c.venue_id == venue_id,
            trading_session.c.trading_date == trading_date,
            trading_session.c.session_code == "REGULAR",
        )
        with translate_database_errors(operation=operation):
            row = self._connection.execute(statement).mappings().one_or_none()
        return _required_row(row, TradingSession, operation=operation)


__all__ = ["SqlAlchemyDataFeedRepository"]
