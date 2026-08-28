"""Immutable-registry persistence for explicit REGULAR trading sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import insert, select, text
from sqlalchemy.engine import Connection

from bisfin.calendar.errors import CalendarConflictError
from bisfin.calendar.manifest import ValidatedCalendarSession
from bisfin.db.errors import translate_database_errors
from bisfin.db.tables import trading_session
from bisfin.domain.catalog import TradingSession


@dataclass(frozen=True, slots=True)
class CalendarSessionWriteResult:
    session: TradingSession
    created: bool
    unchanged: bool


class SqlAlchemyTradingCalendarRepository:
    """Insert only missing exact sessions; existing differences always conflict."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def acquire_session_locks(
        self, venue_id: int, sessions: tuple[ValidatedCalendarSession, ...]
    ) -> None:
        for session in sorted(sessions, key=lambda value: (value.trading_date, value.session_code)):
            self._connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {
                    "key": (
                        f"bisfin|calendar-session|{venue_id}|{session.trading_date.isoformat()}|"
                        f"{session.session_code}"
                    )
                },
            )

    def get_session(
        self, venue_id: int, trading_date: date, session_code: str = "REGULAR"
    ) -> TradingSession | None:
        statement = select(trading_session).where(
            trading_session.c.venue_id == venue_id,
            trading_session.c.trading_date == trading_date,
            trading_session.c.session_code == session_code,
        )
        with translate_database_errors(operation="get trading session"):
            row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else TradingSession.model_validate(dict(row))

    def list_sessions(
        self, venue_id: int, date_from: date, date_to: date, session_code: str = "REGULAR"
    ) -> tuple[TradingSession, ...]:
        statement = (
            select(trading_session)
            .where(
                trading_session.c.venue_id == venue_id,
                trading_session.c.trading_date >= date_from,
                trading_session.c.trading_date <= date_to,
                trading_session.c.session_code == session_code,
            )
            .order_by(trading_session.c.trading_date)
        )
        with translate_database_errors(operation="list trading sessions"):
            rows = self._connection.execute(statement).mappings().all()
        return tuple(TradingSession.model_validate(dict(row)) for row in rows)

    def ensure_session(
        self, venue_id: int, candidate: ValidatedCalendarSession
    ) -> CalendarSessionWriteResult:
        existing = self.get_session(venue_id, candidate.trading_date, candidate.session_code)
        expected = {
            "venue_id": venue_id,
            "trading_date": candidate.trading_date,
            "session_code": candidate.session_code,
            "is_trading_day": candidate.is_trading_day,
            "session_open_ts": candidate.session_open_ts,
            "session_close_ts": candidate.session_close_ts,
            "settlement_date": candidate.settlement_date,
            "metadata": {**candidate.metadata, "source_status": candidate.source_status},
        }
        if existing is not None:
            actual = existing.model_dump()
            differences = [name for name, value in expected.items() if actual.get(name) != value]
            if differences:
                raise CalendarConflictError(
                    "Conflicting existing session "
                    f"{candidate.trading_date}: {', '.join(differences)}"
                )
            return CalendarSessionWriteResult(existing, False, True)
        statement = insert(trading_session).values(**expected).returning(*trading_session.c)
        with translate_database_errors(operation="insert trading session"):
            row = self._connection.execute(statement).mappings().one()
        return CalendarSessionWriteResult(TradingSession.model_validate(dict(row)), True, False)


__all__ = ["CalendarSessionWriteResult", "SqlAlchemyTradingCalendarRepository"]
