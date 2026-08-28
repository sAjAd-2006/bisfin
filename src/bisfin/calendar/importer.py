"""Explicit calendar-file import orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import uuid4

from bisfin.calendar.errors import CalendarConflictError
from bisfin.calendar.manifest import CalendarValidationResult, calendar_source_record_key
from bisfin.calendar.results import CalendarImportResult
from bisfin.domain.catalog import DataFeed, Venue
from bisfin.domain.ingestion import IngestionBatch, IngestionBatchStatus, RawEventValidationStatus
from bisfin.logging import clear_log_context, log_context
from bisfin.repositories.catalog_writer_repository import SqlAlchemyCatalogWriterRepository
from bisfin.repositories.protocols import IngestionBatchRepository, RawEventRepository
from bisfin.repositories.trading_calendar_repository import SqlAlchemyTradingCalendarRepository


class CalendarImportUnitOfWork(Protocol):
    @property
    def catalog_writer(self) -> SqlAlchemyCatalogWriterRepository: ...

    @property
    def trading_calendar(self) -> SqlAlchemyTradingCalendarRepository: ...

    @property
    def ingestion_batches(self) -> IngestionBatchRepository: ...

    @property
    def raw_events(self) -> RawEventRepository: ...

    def __enter__(self) -> Self: ...

    def commit(self) -> None: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


type CalendarUnitOfWorkFactory = Callable[[], CalendarImportUnitOfWork]
type Clock = Callable[[], datetime]
type IdGenerator = Callable[[], str]


class TradingCalendarImportService:
    """Import all explicit sessions atomically after pure file validation."""

    def __init__(
        self,
        *,
        unit_of_work_factory: CalendarUnitOfWorkFactory,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self._uow_factory = unit_of_work_factory
        self._clock = clock or _utc_now
        self._id_generator = id_generator or (lambda: str(uuid4()))

    def import_calendar(
        self,
        validated: CalendarValidationResult,
        *,
        request_id: str | None = None,
    ) -> CalendarImportResult:
        clear_log_context()
        try:
            document = validated.document
            manifest = document.manifest
            started_at = self._now()
            effective_request_id = (request_id or "").strip() or self._id_generator()
            with log_context(
                request_id=effective_request_id,
                correlation_id=effective_request_id,
                calendar_id=manifest.calendar_id,
                venue_code=manifest.venue_code,
            ):
                with self._uow_factory() as unit_of_work:
                    venue = self._get_venue(unit_of_work.catalog_writer, manifest.venue_code)
                    if venue.timezone_name != manifest.timezone:
                        raise CalendarConflictError(
                            "calendar timezone conflicts with venue configuration"
                        )
                    feed = self._get_calendar_feed(unit_of_work.catalog_writer)
                    start = unit_of_work.ingestion_batches.create_batch_if_absent(
                        feed_id=feed.feed_id,
                        parser_version="trading-calendar-v1",
                        request_id=effective_request_id,
                        metadata={
                            "calendar_id": manifest.calendar_id,
                            "venue_code": manifest.venue_code,
                        },
                        started_at=started_at,
                    )
                    if not start.created:
                        if start.batch.status is IngestionBatchStatus.RUNNING:
                            raise CalendarConflictError("calendar request_id is already running")
                        return self._replay_result(start.batch, validated)
                    batch = start.batch
                    with log_context(ingestion_batch_id=batch.ingestion_batch_id):
                        unit_of_work.trading_calendar.acquire_session_locks(
                            venue.venue_id, validated.sessions
                        )
                        inserted_open = 0
                        inserted_closed = 0
                        unchanged = 0
                        unit_of_work.raw_events.ensure_month_partition(started_at)
                        for session in validated.sessions:
                            raw = {
                                "calendar_id": manifest.calendar_id,
                                "venue_code": manifest.venue_code,
                                "timezone": manifest.timezone,
                                "trading_date": session.trading_date.isoformat(),
                                "session_code": session.session_code,
                                "is_trading_day": session.is_trading_day,
                                "open_local_time": (
                                    session.open_local_time.isoformat()
                                    if session.open_local_time is not None
                                    else None
                                ),
                                "close_local_time": (
                                    session.close_local_time.isoformat()
                                    if session.close_local_time is not None
                                    else None
                                ),
                                "source_status": session.source_status,
                                "metadata": session.metadata,
                            }
                            unit_of_work.raw_events.insert_response_record(
                                ingested_at=started_at,
                                ingestion_batch_id=batch.ingestion_batch_id,
                                feed_id=feed.feed_id,
                                payload_sha256=_sha256_json(raw),
                                raw_payload=raw,
                                source_record_key=calendar_source_record_key(manifest, session),
                                source_date_text=session.trading_date.isoformat(),
                                validation_status=RawEventValidationStatus.ACCEPTED,
                            )
                            outcome = unit_of_work.trading_calendar.ensure_session(
                                venue.venue_id,
                                session,
                            )
                            if outcome.created:
                                if session.is_trading_day:
                                    inserted_open += 1
                                else:
                                    inserted_closed += 1
                            else:
                                unchanged += 1
                        finished_at = self._now()
                        unit_of_work.ingestion_batches.finalize_batch(
                            batch.ingestion_batch_id,
                            status=IngestionBatchStatus.SUCCEEDED,
                            received_row_count=len(validated.sessions),
                            accepted_row_count=len(validated.sessions),
                            rejected_row_count=0,
                            payload_sha256=document.payload_sha256,
                            metadata={
                                "calendar_id": manifest.calendar_id,
                                "venue_code": manifest.venue_code,
                            },
                            finished_at=finished_at,
                        )
                        unit_of_work.commit()
            return CalendarImportResult(
                batch_id=batch.ingestion_batch_id,
                calendar_id=manifest.calendar_id,
                venue_code=manifest.venue_code,
                status=IngestionBatchStatus.SUCCEEDED,
                date_from=manifest.date_from.isoformat(),
                date_to=manifest.date_to.isoformat(),
                sessions_received=len(validated.sessions),
                trading_days_inserted=inserted_open,
                closed_days_inserted=inserted_closed,
                sessions_unchanged=unchanged,
                started_at=started_at,
                finished_at=finished_at,
                payload_sha256=document.payload_sha256,
            )
        finally:
            clear_log_context()

    @staticmethod
    def _get_venue(writer: SqlAlchemyCatalogWriterRepository, venue_code: str) -> Venue:
        return writer.get_venue_by_code(venue_code)

    @staticmethod
    def _get_calendar_feed(writer: SqlAlchemyCatalogWriterRepository) -> DataFeed:
        return writer.get_feed_by_codes("BISFIN", "BISFIN_TRADING_CALENDAR")

    @staticmethod
    def _replay_result(
        batch: IngestionBatch,
        validated: CalendarValidationResult,
    ) -> CalendarImportResult:
        manifest = validated.document.manifest
        return CalendarImportResult(
            batch_id=batch.ingestion_batch_id,
            calendar_id=manifest.calendar_id,
            venue_code=manifest.venue_code,
            status=batch.status,
            date_from=manifest.date_from.isoformat(),
            date_to=manifest.date_to.isoformat(),
            sessions_received=len(validated.sessions),
            sessions_rejected=batch.rejected_row_count,
            started_at=batch.started_at,
            finished_at=batch.finished_at or batch.started_at,
            payload_sha256=batch.payload_sha256 or "",
            idempotent_replay=True,
        )

    def _now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _sha256_json(value: object) -> str:
    from bisfin.repositories.raw_event_repository import canonical_json_text

    return hashlib.sha256(canonical_json_text(value).encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


__all__ = ["TradingCalendarImportService"]
