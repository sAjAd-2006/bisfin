"""Unit coverage for the guarded ingestion-batch lifecycle."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Connection

from bisfin.db.errors import EntityNotFoundError, InvalidStateTransitionError
from bisfin.domain.ingestion import IngestionBatchStatus
from bisfin.repositories.ingestion_batch_repository import (
    SqlAlchemyIngestionBatchRepository,
    _failure_payload,
)


def _connection() -> tuple[Connection, MagicMock]:
    mock = MagicMock(spec=Connection)
    return cast(Connection, mock), mock


def _batch_row(status: str = "RUNNING") -> dict[str, object]:
    finished_at = None if status == "RUNNING" else datetime(2026, 1, 1, 1, tzinfo=UTC)
    return {
        "ingestion_batch_id": 17,
        "feed_id": 9,
        "request_id": "request-17",
        "requested_event_from": None,
        "requested_event_to": None,
        "started_at": datetime(2026, 1, 1, tzinfo=UTC),
        "finished_at": finished_at,
        "status": status,
        "received_row_count": 0,
        "accepted_row_count": 0,
        "rejected_row_count": 0,
        "payload_sha256": None,
        "parser_version": "parser-v1",
        "source_watermark": None,
        "error_summary": None,
        "metadata": {},
    }


def test_create_batch_starts_running_without_committing() -> None:
    connection, mock = _connection()
    mock.execute.return_value.mappings.return_value.one.return_value = _batch_row()
    repository = SqlAlchemyIngestionBatchRepository(connection)

    batch = repository.create_batch(feed_id=9, parser_version="parser-v1")

    assert batch.status is IngestionBatchStatus.RUNNING
    mock.commit.assert_not_called()
    statement = mock.execute.call_args.args[0]
    assert "status" not in statement.compile().params


def test_mark_running_is_idempotent_only_while_running() -> None:
    connection, mock = _connection()
    first = MagicMock()
    first.mappings.return_value.one_or_none.return_value = _batch_row()
    mock.execute.return_value = first
    repository = SqlAlchemyIngestionBatchRepository(connection)

    assert repository.mark_running(17).status is IngestionBatchStatus.RUNNING

    transition = MagicMock()
    transition.mappings.return_value.one_or_none.return_value = None
    current = MagicMock()
    current.scalar_one_or_none.return_value = "FAILED"
    mock.execute.side_effect = [transition, current]
    with pytest.raises(InvalidStateTransitionError, match="FAILED to RUNNING"):
        repository.mark_running(17)


def test_missing_batch_raises_entity_not_found() -> None:
    connection, mock = _connection()
    transition = MagicMock()
    transition.mappings.return_value.one_or_none.return_value = None
    current = MagicMock()
    current.scalar_one_or_none.return_value = None
    mock.execute.side_effect = [transition, current]

    with pytest.raises(EntityNotFoundError):
        SqlAlchemyIngestionBatchRepository(connection).mark_succeeded(
            404,
            received_row_count=0,
            accepted_row_count=0,
            rejected_row_count=0,
        )


def test_failure_payload_is_structured_bounded_and_redacted() -> None:
    summary, metadata = _failure_payload(
        "FETCH_FAILED",
        "authorization=Bearer VERY_SECRET_TOKEN",
        {
            "password": "p@ssword",
            "request": "postgresql://user:secret@example/db",
            "nested": {"api_key": "abc123", "safe": "kept"},
        },
    )

    rendered = repr((summary, metadata))
    assert "VERY_SECRET_TOKEN" not in rendered
    assert "p@ssword" not in rendered
    assert "abc123" not in rendered
    assert "user:secret@" not in rendered
    assert metadata["failure"]
    assert len(rendered) <= 5_000


def test_failure_payload_removes_basic_authorization_header() -> None:
    summary, metadata = _failure_payload(
        "FETCH_FAILED",
        "authorization=Basic YWxpY2U6c2VjcmV0; upstream rejected request",
        None,
    )

    rendered = repr((summary, metadata))
    assert "YWxpY2U6c2VjcmV0" not in rendered
    assert "authorization=***" in rendered
    assert "upstream rejected request" in rendered


def test_request_id_start_returns_existing_without_second_batch() -> None:
    connection, mock = _connection()
    insert_result = MagicMock()
    insert_result.mappings.return_value.one_or_none.return_value = None
    lookup_result = MagicMock()
    lookup_result.mappings.return_value.one_or_none.return_value = _batch_row()
    mock.execute.side_effect = (insert_result, lookup_result)

    outcome = SqlAlchemyIngestionBatchRepository(connection).create_batch_if_absent(
        feed_id=9,
        parser_version="parser-v1",
        request_id="request-17",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert outcome.created is False
    assert outcome.batch.ingestion_batch_id == 17
    assert "ON CONFLICT" in str(mock.execute.call_args_list[0].args[0])
    mock.commit.assert_not_called()


def test_acquisition_update_keeps_batch_running() -> None:
    connection, mock = _connection()
    row = _batch_row()
    row["payload_sha256"] = "a" * 64
    row["received_row_count"] = 3
    mock.execute.return_value.mappings.return_value.one_or_none.return_value = row

    batch = SqlAlchemyIngestionBatchRepository(connection).record_acquisition(
        17,
        payload_sha256="a" * 64,
        received_row_count=3,
        metadata={"http_status": 200},
    )

    assert batch.status is IngestionBatchStatus.RUNNING
    assert batch.received_row_count == 3


def test_general_finalization_accepts_partial_with_injected_timestamp() -> None:
    connection, mock = _connection()
    row = _batch_row("PARTIAL")
    row["received_row_count"] = 3
    row["accepted_row_count"] = 2
    row["rejected_row_count"] = 1
    mock.execute.return_value.mappings.return_value.one_or_none.return_value = row
    finished_at = datetime(2026, 1, 1, 1, tzinfo=UTC)

    batch = SqlAlchemyIngestionBatchRepository(connection).finalize_batch(
        17,
        status=IngestionBatchStatus.PARTIAL,
        received_row_count=3,
        accepted_row_count=2,
        rejected_row_count=1,
        error_summary="one row rejected",
        finished_at=finished_at,
    )

    assert batch.status is IngestionBatchStatus.PARTIAL
    assert batch.finished_at == finished_at


def test_finalization_rejects_impossible_counts_before_sql() -> None:
    connection, mock = _connection()

    with pytest.raises(ValueError, match="must not exceed"):
        SqlAlchemyIngestionBatchRepository(connection).finalize_batch(
            17,
            status=IngestionBatchStatus.PARTIAL,
            received_row_count=1,
            accepted_row_count=1,
            rejected_row_count=1,
        )

    mock.execute.assert_not_called()
