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
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Connection, RowMapping

from bisfin.db.errors import (
    EntityNotFoundError,
    IntegrityViolationError,
    InvalidStateTransitionError,
    redact_secrets,
    translate_database_errors,
)
from bisfin.db.tables import ingestion_batch
from bisfin.domain.common import require_aware_datetime
from bisfin.domain.ingestion import (
    IngestionBatch,
    IngestionBatchStartResult,
    IngestionBatchStatus,
)

_FAILURE_CODE_LIMIT: Final = 128
_FAILURE_MESSAGE_LIMIT: Final = 2_048
_FAILURE_DETAILS_LIMIT: Final = 4_096
_CONTAINER_ITEM_LIMIT: Final = 32
_DETAIL_STRING_LIMIT: Final = 512
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key|authorization|credential)"
)
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_TERMINAL_STATUSES = frozenset(
    {
        IngestionBatchStatus.SUCCEEDED,
        IngestionBatchStatus.PARTIAL,
        IngestionBatchStatus.FAILED,
        IngestionBatchStatus.QUARANTINED,
    }
)


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


def _validate_counts(received: int, accepted: int, rejected: int) -> None:
    if min(received, accepted, rejected) < 0:
        raise ValueError("ingestion counts must be nonnegative")
    if accepted + rejected > received:
        raise ValueError("accepted and rejected counts must not exceed received count")


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
        started_at: datetime | None = None,
    ) -> IngestionBatch:
        if requested_event_from is not None:
            require_aware_datetime(requested_event_from)
        if requested_event_to is not None:
            require_aware_datetime(requested_event_to)
        if started_at is not None:
            require_aware_datetime(started_at)

        values: dict[str, object] = {
            "feed_id": feed_id,
            "parser_version": parser_version,
            "request_id": request_id,
            "requested_event_from": requested_event_from,
            "requested_event_to": requested_event_to,
            "source_watermark": source_watermark,
        }
        if started_at is not None:
            values["started_at"] = started_at
        if metadata is not None:
            values["metadata"] = dict(metadata)
        statement = insert(ingestion_batch).values(**values).returning(*ingestion_batch.c)
        with translate_database_errors(operation="create ingestion batch"):
            row = self._connection.execute(statement).mappings().one()
        return _batch_from_row(row)

    def create_batch_if_absent(
        self,
        *,
        feed_id: int,
        parser_version: str,
        request_id: str | None,
        requested_event_from: datetime | None = None,
        requested_event_to: datetime | None = None,
        source_watermark: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        started_at: datetime | None = None,
    ) -> IngestionBatchStartResult:
        """Start once under the database ``(feed_id, request_id)`` authority."""

        if request_id is None:
            return IngestionBatchStartResult(
                batch=self.create_batch(
                    feed_id=feed_id,
                    parser_version=parser_version,
                    request_id=None,
                    requested_event_from=requested_event_from,
                    requested_event_to=requested_event_to,
                    source_watermark=source_watermark,
                    metadata=metadata,
                    started_at=started_at,
                ),
                created=True,
            )
        if requested_event_from is not None:
            require_aware_datetime(requested_event_from)
        if requested_event_to is not None:
            require_aware_datetime(requested_event_to)
        if started_at is not None:
            require_aware_datetime(started_at)

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
        if started_at is not None:
            values["started_at"] = started_at
        statement = (
            postgresql_insert(ingestion_batch)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=(
                    ingestion_batch.c.feed_id,
                    ingestion_batch.c.request_id,
                )
            )
            .returning(*ingestion_batch.c)
        )
        operation = "start idempotent ingestion batch"
        with translate_database_errors(operation=operation):
            row = self._connection.execute(statement).mappings().one_or_none()
            if row is not None:
                return IngestionBatchStartResult(
                    batch=_batch_from_row(row),
                    created=True,
                )
            existing = (
                self._connection.execute(
                    select(ingestion_batch).where(
                        ingestion_batch.c.feed_id == feed_id,
                        ingestion_batch.c.request_id == request_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        if existing is None:
            raise IntegrityViolationError(
                "The idempotent batch insert conflicted without a visible request row.",
                operation=operation,
            )
        return IngestionBatchStartResult(
            batch=_batch_from_row(existing),
            created=False,
        )

    def get_by_id(self, ingestion_batch_id: int) -> IngestionBatch | None:
        statement = select(ingestion_batch).where(
            ingestion_batch.c.ingestion_batch_id == ingestion_batch_id
        )
        with translate_database_errors(operation="get ingestion batch by id"):
            row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else _batch_from_row(row)

    def get_by_request_id(self, feed_id: int, request_id: str) -> IngestionBatch | None:
        statement = select(ingestion_batch).where(
            ingestion_batch.c.feed_id == feed_id,
            ingestion_batch.c.request_id == request_id,
        )
        with translate_database_errors(operation="get ingestion batch by request id"):
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

    def record_acquisition(
        self,
        ingestion_batch_id: int,
        *,
        payload_sha256: str,
        received_row_count: int,
        source_watermark: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> IngestionBatch:
        if received_row_count < 0:
            raise ValueError("received_row_count must be nonnegative")
        values: dict[str, object] = {
            "payload_sha256": payload_sha256,
            "received_row_count": received_row_count,
        }
        if source_watermark is not None:
            values["source_watermark"] = source_watermark
        if metadata is not None:
            values["metadata"] = ingestion_batch.c.metadata.op("||")(
                bindparam("acquisition_metadata_patch", dict(metadata), type_=JSONB)
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
            target_status="RUNNING acquisition",
        )

    def finalize_batch(
        self,
        ingestion_batch_id: int,
        *,
        status: IngestionBatchStatus,
        received_row_count: int,
        accepted_row_count: int,
        rejected_row_count: int,
        payload_sha256: str | None = None,
        source_watermark: str | None = None,
        error_summary: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        finished_at: datetime | None = None,
    ) -> IngestionBatch:
        if not isinstance(status, IngestionBatchStatus) or status not in _TERMINAL_STATUSES:
            raise ValueError("status must be a terminal IngestionBatchStatus")
        _validate_counts(received_row_count, accepted_row_count, rejected_row_count)
        if finished_at is not None:
            require_aware_datetime(finished_at)

        values: dict[str, object] = {
            "status": status.value,
            "finished_at": finished_at if finished_at is not None else func.current_timestamp(),
            "received_row_count": received_row_count,
            "accepted_row_count": accepted_row_count,
            "rejected_row_count": rejected_row_count,
            "error_summary": (
                None
                if status is IngestionBatchStatus.SUCCEEDED
                else (
                    _redact_failure_text(error_summary)[:_FAILURE_MESSAGE_LIMIT]
                    if error_summary is not None
                    else None
                )
            ),
        }
        if payload_sha256 is not None:
            values["payload_sha256"] = payload_sha256
        if source_watermark is not None:
            values["source_watermark"] = source_watermark
        if metadata is not None:
            values["metadata"] = ingestion_batch.c.metadata.op("||")(
                bindparam("final_metadata_patch", dict(metadata), type_=JSONB)
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
            target_status=status.value,
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
        finished_at: datetime | None = None,
    ) -> IngestionBatch:
        return self.finalize_batch(
            ingestion_batch_id=ingestion_batch_id,
            status=IngestionBatchStatus.SUCCEEDED,
            received_row_count=received_row_count,
            accepted_row_count=accepted_row_count,
            rejected_row_count=rejected_row_count,
            payload_sha256=payload_sha256,
            source_watermark=source_watermark,
            metadata=metadata,
            finished_at=finished_at,
        )

    def mark_failed(
        self,
        ingestion_batch_id: int,
        *,
        error_code: str,
        error_message: str,
        details: Mapping[str, Any] | None = None,
        finished_at: datetime | None = None,
    ) -> IngestionBatch:
        if finished_at is not None:
            require_aware_datetime(finished_at)
        summary, metadata_patch = _failure_payload(error_code, error_message, details)
        statement = (
            update(ingestion_batch)
            .where(
                ingestion_batch.c.ingestion_batch_id == ingestion_batch_id,
                ingestion_batch.c.status == "RUNNING",
            )
            .values(
                status="FAILED",
                finished_at=(finished_at if finished_at is not None else func.current_timestamp()),
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
