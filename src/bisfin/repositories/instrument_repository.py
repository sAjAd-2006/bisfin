"""SQLAlchemy Core implementation of historical instrument catalog reads."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func, or_, select
from sqlalchemy.engine import Connection, RowMapping

from bisfin.db.errors import IntegrityViolationError, translate_database_errors
from bisfin.db.tables import instrument, instrument_identifier, instrument_spec_version
from bisfin.domain.catalog import (
    Instrument,
    InstrumentIdentifier,
    InstrumentSpecification,
    ResolvedInstrument,
)
from bisfin.domain.common import require_aware_datetime


def _instrument_from_row(row: RowMapping) -> Instrument:
    return Instrument.model_validate({column.name: row[column.name] for column in instrument.c})


class SqlAlchemyInstrumentRepository:
    """Read catalog state through a caller-owned connection and transaction."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_by_id(self, instrument_id: int) -> Instrument | None:
        statement = select(instrument).where(instrument.c.instrument_id == instrument_id)
        with translate_database_errors(operation="get instrument by id"):
            row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else _instrument_from_row(row)

    def find_by_identifier(
        self,
        provider_id: int,
        identifier_type: str,
        identifier_value: str,
        as_of: datetime,
    ) -> ResolvedInstrument | None:
        """Resolve one provider identifier using half-open ``[from, to)`` time."""

        require_aware_datetime(as_of)
        identifier_columns = [
            (
                case(
                    (func.isfinite(column), column),
                    else_=None,
                ).label("identifier_valid_from")
                if column.name == "valid_from"
                else column.label(f"identifier_{column.name}")
            )
            for column in instrument_identifier.c
        ]
        statement = (
            select(*instrument.c, *identifier_columns)
            .select_from(
                instrument_identifier.join(
                    instrument,
                    instrument.c.instrument_id == instrument_identifier.c.instrument_id,
                )
            )
            .where(
                instrument_identifier.c.provider_id == provider_id,
                instrument_identifier.c.identifier_type == identifier_type,
                instrument_identifier.c.identifier_value == identifier_value,
                instrument_identifier.c.valid_from <= as_of,
                or_(
                    instrument_identifier.c.valid_to.is_(None),
                    instrument_identifier.c.valid_to > as_of,
                ),
            )
            .order_by(
                instrument_identifier.c.valid_from.desc(),
                instrument_identifier.c.instrument_id.asc(),
            )
            .limit(2)
        )

        with translate_database_errors(operation="resolve historical instrument identifier"):
            rows = self._connection.execute(statement).mappings().all()
        if len(rows) > 1:
            raise IntegrityViolationError(
                "Multiple active instrument identifiers violate the temporal catalog contract.",
                operation="resolve historical instrument identifier",
            )
        if not rows:
            return None

        row = rows[0]
        identifier = InstrumentIdentifier.model_validate(
            {column.name: row[f"identifier_{column.name}"] for column in instrument_identifier.c}
        )
        return ResolvedInstrument(
            instrument=_instrument_from_row(row),
            identifier=identifier,
        )

    def get_active_spec(
        self,
        instrument_id: int,
        as_of: datetime,
    ) -> InstrumentSpecification | None:
        """Return the unique specification active at ``as_of``, if any."""

        require_aware_datetime(as_of)
        statement = (
            select(instrument_spec_version)
            .where(
                instrument_spec_version.c.instrument_id == instrument_id,
                instrument_spec_version.c.effective_from <= as_of,
                or_(
                    instrument_spec_version.c.effective_to.is_(None),
                    instrument_spec_version.c.effective_to > as_of,
                ),
            )
            .order_by(instrument_spec_version.c.effective_from.desc())
            .limit(2)
        )
        with translate_database_errors(operation="get active instrument specification"):
            rows = self._connection.execute(statement).mappings().all()
        if len(rows) > 1:
            raise IntegrityViolationError(
                "Multiple active instrument specifications violate the temporal catalog contract.",
                operation="get active instrument specification",
            )
        if not rows:
            return None
        return InstrumentSpecification.model_validate(dict(rows[0]))


__all__ = ["SqlAlchemyInstrumentRepository"]
