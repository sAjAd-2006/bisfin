"""Decimal-safe persistence for immutable, partitioned provider raw events."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import Text, bindparam, insert, select, text, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, RowMapping

from bisfin.db.errors import EntityNotFoundError, translate_database_errors
from bisfin.db.tables import raw_event
from bisfin.domain.common import require_aware_datetime
from bisfin.domain.ingestion import (
    RawEvent,
    RawEventIdentity,
    RawEventValidationStatus,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _json_value(value: object) -> str:
    """Encode JSON without ever converting a Decimal through binary float."""

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("raw JSON Decimal values must be finite")
        return str(value)
    if isinstance(value, float):
        raise TypeError("raw JSON must be parsed with Decimal rather than float")
    if isinstance(value, Mapping):
        keys = list(value)
        if not all(isinstance(key, str) for key in keys):
            raise TypeError("raw JSON object keys must be strings")
        items: list[str] = []
        for key in sorted(cast("list[str]", keys)):
            items.append(f"{json.dumps(key, ensure_ascii=False)}:{_json_value(value[key])}")
        return "{" + ",".join(items) + "}"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "[" + ",".join(_json_value(item) for item in value) + "]"
    raise TypeError(f"unsupported raw JSON value type: {type(value).__name__}")


def canonical_json_text(value: object) -> str:
    """Return stable compact UTF-8-compatible JSON with sorted object keys."""

    return _json_value(value)


def _require_sha256(value: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError("payload_sha256 must be a lowercase hexadecimal SHA-256 digest")


_RAW_EVENT_PROJECTION = tuple(
    column for column in raw_event.c if column.name not in {"raw_payload", "validation_errors"}
) + (
    sql_cast(raw_event.c.raw_payload, Text).label("raw_payload_text"),
    sql_cast(raw_event.c.validation_errors, Text).label("validation_errors_text"),
)


def _raw_event_from_row(row: RowMapping) -> RawEvent:
    raw_payload = json.loads(str(row["raw_payload_text"]), parse_float=Decimal)
    validation_errors = json.loads(
        str(row["validation_errors_text"]),
        parse_float=Decimal,
    )
    if not isinstance(raw_payload, dict):
        raise ValueError("persisted raw_payload must be a JSON object")
    if not isinstance(validation_errors, list):
        raise ValueError("persisted validation_errors must be a JSON array")
    values = {
        column.name: row[column.name]
        for column in raw_event.c
        if column.name not in {"raw_payload", "validation_errors"}
    }
    values["raw_payload"] = cast("dict[str, object]", raw_payload)
    values["validation_errors"] = cast("list[object]", validation_errors)
    return RawEvent.model_validate(values)


class SqlAlchemyRawEventRepository:
    """Insert provider rows and update only their validation outcome fields."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def ensure_month_partition(self, ingested_at: datetime) -> None:
        require_aware_datetime(ingested_at)
        month = ingested_at.astimezone(UTC).date().replace(day=1)
        statement = text(
            "SELECT ingest.create_raw_event_month_partition(CAST(:month AS DATE))"
        ).bindparams(bindparam("month"))
        with translate_database_errors(operation="ensure raw-event month partition"):
            self._connection.execute(statement, {"month": month})

    def insert_response_record(
        self,
        *,
        ingested_at: datetime,
        ingestion_batch_id: int,
        feed_id: int,
        payload_sha256: str,
        raw_payload: Mapping[str, object],
        source_record_key: str | None = None,
        source_event_time_text: str | None = None,
        source_date_text: str | None = None,
        source_sequence: int | None = None,
        observed_at: datetime | None = None,
        validation_status: RawEventValidationStatus = RawEventValidationStatus.PENDING,
        validation_errors: Sequence[object] = (),
    ) -> RawEvent:
        require_aware_datetime(ingested_at)
        if observed_at is not None:
            require_aware_datetime(observed_at)
        _require_sha256(payload_sha256)
        if not isinstance(validation_status, RawEventValidationStatus):
            raise ValueError("validation_status must be a RawEventValidationStatus")

        statement = (
            insert(raw_event)
            .values(
                ingested_at=ingested_at,
                ingestion_batch_id=ingestion_batch_id,
                feed_id=feed_id,
                source_record_key=source_record_key,
                source_event_time_text=source_event_time_text,
                source_date_text=source_date_text,
                source_sequence=source_sequence,
                observed_at=observed_at,
                payload_sha256=payload_sha256,
                raw_payload=sql_cast(
                    bindparam("raw_payload_text", canonical_json_text(raw_payload)), JSONB
                ),
                validation_status=validation_status.value,
                validation_errors=sql_cast(
                    bindparam(
                        "validation_errors_text",
                        canonical_json_text(list(validation_errors)),
                    ),
                    JSONB,
                ),
            )
            .returning(*_RAW_EVENT_PROJECTION)
        )
        with translate_database_errors(operation="insert immutable raw event"):
            row = self._connection.execute(statement).mappings().one()
        return _raw_event_from_row(row)

    def update_validation_result(
        self,
        identity: RawEventIdentity,
        *,
        validation_status: RawEventValidationStatus,
        validation_errors: Sequence[object] = (),
    ) -> RawEvent:
        if not isinstance(validation_status, RawEventValidationStatus):
            raise ValueError("validation_status must be a RawEventValidationStatus")
        statement = (
            update(raw_event)
            .where(
                raw_event.c.ingested_at == identity.ingested_at,
                raw_event.c.raw_event_id == identity.raw_event_id,
            )
            .values(
                validation_status=validation_status.value,
                validation_errors=sql_cast(
                    bindparam(
                        "validation_errors_text",
                        canonical_json_text(list(validation_errors)),
                    ),
                    JSONB,
                ),
            )
            .returning(*_RAW_EVENT_PROJECTION)
        )
        operation = "update raw-event validation result"
        with translate_database_errors(operation=operation):
            row = self._connection.execute(statement).mappings().one_or_none()
        if row is None:
            raise EntityNotFoundError(
                "The requested raw event does not exist.",
                operation=operation,
            )
        return _raw_event_from_row(row)

    def find_identical_record(
        self,
        *,
        feed_id: int,
        source_record_key: str,
        payload_sha256: str,
    ) -> RawEvent | None:
        _require_sha256(payload_sha256)
        statement = (
            select(*_RAW_EVENT_PROJECTION)
            .where(
                raw_event.c.feed_id == feed_id,
                raw_event.c.source_record_key == source_record_key,
                raw_event.c.payload_sha256 == payload_sha256,
            )
            .order_by(raw_event.c.ingested_at.desc(), raw_event.c.raw_event_id.desc())
            .limit(1)
        )
        with translate_database_errors(operation="find identical raw event"):
            row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else _raw_event_from_row(row)

    def list_by_batch(self, ingestion_batch_id: int) -> tuple[RawEvent, ...]:
        statement = (
            select(*_RAW_EVENT_PROJECTION)
            .where(raw_event.c.ingestion_batch_id == ingestion_batch_id)
            .order_by(raw_event.c.ingested_at, raw_event.c.raw_event_id)
        )
        with translate_database_errors(operation="list raw events by ingestion batch"):
            rows = self._connection.execute(statement).mappings().all()
        return tuple(_raw_event_from_row(row) for row in rows)


__all__ = ["SqlAlchemyRawEventRepository", "canonical_json_text"]
