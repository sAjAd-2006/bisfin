"""Explicit, transaction-neutral persistence for ingestion batch lifecycle."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import bindparam, func, insert, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, RowMapping

from bisfin.db.errors import (
    EntityNotFoundError,
    InvalidStateTransitionError,
    redact_secrets,
    translate_database_errors,
)
from bisfin.db.tables import ingestion_batch
from bisfin.domain.common import require_aware_datetime
from bisfin.domain.ingestion import IngestionBatch

_FAILURE_CODE_LIMIT: Final = 128
_FAILURE_MESSAGE_LIMIT: Final = 2_048
_FAILURE_DETAILS_LIMIT: Final = 4_096
_CONTAINER_ITEM_LIMIT: Final = 32
_DETAIL_STRING_LIMIT: Final = 512
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|authorization|credential)"
)
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def _batch_from_row(row: RowMapping) -> IngestionBatch:
    return IngestionBatch.model_validate(dict(row))


def _redact_failure_text(value: object) -> str:
    return redact_secrets(_BEARER_VALUE.sub("Bearer ***", str(value)))


def _sanitize_detail(value: object, *, depth: int = 0) -> object:
    """Produce bounded JSON-compatible diagnostics without common secret fields."""

    if depth >= 4:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str):
        return _redact_failure_text(value)[:_DETAIL_STRING_LIMIT]
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        for raw_key, item in list(value.items())[:_CONTAINER_ITEM_LIMIT]:
            key = str(raw_key)[:128]
            sanitized[key] = (
                "***" if _SENSITIVE_KEY.search(key) else _sanitize_detail(item, depth=depth + 1)
            )
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _sanitize_detail(item, depth=depth + 1) for item in list(value)[:_CONTAINER_ITEM_LIMIT]
        ]
    return _redact_failure_text(value)[:_DETAIL_STRING_LIMIT]


def _failure_payload(
    error_code: str,
    error_message: str,
    details: Mapping[str, Any] | None,
) -> tuple[str, dict[str, object]]:
    code = _redact_failure_text(error_code)[:_FAILURE_CODE_LIMIT]
    message = _redact_failure_text(error_message)[:_FAILURE_MESSAGE_LIMIT]
    failure: dict[str, object] = {"code": code, "message": message}
    if details:
        failure["details"] = _sanitize_detail(details)
    if len(json.dumps(failure, ensure_ascii=False, separators=(",", ":"))) > _FAILURE_DETAILS_LIMIT:
        failure = {"code": code, "message": message, "details_truncated": True}
    summary = f"{code}: {message}"[:_FAILURE_MESSAGE_LIMIT]
    return summary, {"failure": failure}


class SqlAlchemyIngestionBatchRepository:
    """Persist batches without owning, committing, or rolling back transactions.

    The schema has no pre-running status: creating a row starts it immediately
    in ``RUNNING``.  ``mark_running`` is therefore a guarded, idempotent
    assertion and can never reopen a finalized batch.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def create_batch(
        self,
        *,
        feed_id: int,
        parser_version: str,
        request_id: str | None = None,
        requested_event_from: datetime | None = None,
        requested_event_to: datetime | None = None,
        source_watermark: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> IngestionBatch:
        if requested_event_from is not None:
            require_aware_datetime(requested_event_from)
        if requested_event_to is not None:
            require_aware_datetime(requested_event_to)

        values: dict[str, object] = {
            "feed_id": feed_id,
            "parser_version": parser_version,
            "request_id": request_id,
            "requested_event_from": requested_event_from,
            "requested_event_to": requested_event_to,
            "source_watermark": source_watermark,
        }
        if metadata is not None:
            values["metadata"] = dict(metadata)
        statement = insert(ingestion_batch).values(**values).returning(*ingestion_batch.c)
        with translate_database_errors(operation="create ingestion batch"):
            row = self._connection.execute(statement).mappings().one()
        return _batch_from_row(row)

    def get_by_id(self, ingestion_batch_id: int) -> IngestionBatch | None:
        statement = select(ingestion_batch).where(
            ingestion_batch.c.ingestion_batch_id == ingestion_batch_id
        )
        with translate_database_errors(operation="get ingestion batch by id"):
            row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else _batch_from_row(row)

    def mark_running(self, ingestion_batch_id: int) -> IngestionBatch:
        statement = (
            update(ingestion_batch)
            .where(
                ingestion_batch.c.ingestion_batch_id == ingestion_batch_id,
                ingestion_batch.c.status == "RUNNING",
            )
            .values(status="RUNNING")
            .returning(*ingestion_batch.c)
        )
        return self._execute_running_transition(
            statement,
            ingestion_batch_id=ingestion_batch_id,
            target_status="RUNNING",
        )

    def mark_succeeded(
        self,
        ingestion_batch_id: int,
        *,
        received_row_count: int,
        accepted_row_count: int,
        rejected_row_count: int,
        payload_sha256: str | None = None,
        source_watermark: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> IngestionBatch:
        values: dict[str, object] = {
            "status": "SUCCEEDED",
            "finished_at": func.current_timestamp(),
            "received_row_count": received_row_count,
            "accepted_row_count": accepted_row_count,
            "rejected_row_count": rejected_row_count,
            "payload_sha256": payload_sha256,
            "source_watermark": source_watermark,
            "error_summary": None,
        }
        if metadata is not None:
            values["metadata"] = ingestion_batch.c.metadata.op("||")(
                bindparam("success_metadata_patch", dict(metadata), type_=JSONB)
            )
        statement = (
            update(ingestion_batch)
            .where(
                ingestion_batch.c.ingestion_batch_id == ingestion_batch_id,
                ingestion_batch.c.status == "RUNNING",
            )
            .values(**values)
            .returning(*ingestion_batch.c)
        )
        return self._execute_running_transition(
            statement,
            ingestion_batch_id=ingestion_batch_id,
            target_status="SUCCEEDED",
        )

    def mark_failed(
        self,
        ingestion_batch_id: int,
        *,
        error_code: str,
        error_message: str,
        details: Mapping[str, Any] | None = None,
    ) -> IngestionBatch:
        summary, metadata_patch = _failure_payload(error_code, error_message, details)
        statement = (
            update(ingestion_batch)
            .where(
                ingestion_batch.c.ingestion_batch_id == ingestion_batch_id,
                ingestion_batch.c.status == "RUNNING",
            )
            .values(
                status="FAILED",
                finished_at=func.current_timestamp(),
                error_summary=summary,
                metadata=ingestion_batch.c.metadata.op("||")(
                    bindparam("failure_metadata_patch", metadata_patch, type_=JSONB)
                ),
            )
            .returning(*ingestion_batch.c)
        )
        return self._execute_running_transition(
            statement,
            ingestion_batch_id=ingestion_batch_id,
            target_status="FAILED",
        )

    def _execute_running_transition(
        self,
        statement: Any,
        *,
        ingestion_batch_id: int,
        target_status: str,
    ) -> IngestionBatch:
        operation = f"mark ingestion batch {target_status.lower()}"
        with translate_database_errors(operation=operation):
            row = self._connection.execute(statement).mappings().one_or_none()
            if row is not None:
                return _batch_from_row(row)

            current_status = self._connection.execute(
                select(ingestion_batch.c.status).where(
                    ingestion_batch.c.ingestion_batch_id == ingestion_batch_id
                )
            ).scalar_one_or_none()

        if current_status is None:
            raise EntityNotFoundError(
                "The requested ingestion batch does not exist.",
                operation=operation,
            )
        raise InvalidStateTransitionError(
            f"Cannot transition ingestion batch {ingestion_batch_id} "
            f"from {current_status} to {target_status}."
        )


__all__ = ["SqlAlchemyIngestionBatchRepository"]
