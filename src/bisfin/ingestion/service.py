"""Three-phase BrsApi unadjusted daily-bar ingestion orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID, uuid4

from bisfin.db.errors import BisfinError, redact_secrets
from bisfin.domain.catalog import DataFeed, Provider, SessionResolvedInstrument
from bisfin.domain.common import require_aware_datetime
from bisfin.domain.ingestion import (
    IngestionBatch,
    IngestionBatchStatus,
    RawEvent,
    RawEventValidationStatus,
)
from bisfin.domain.market_data import (
    BarRevisionCandidate,
    BarRevisionWriteStatus,
)
from bisfin.ingestion.daily_bars import (
    CanonicalizationCode,
    canonicalization_issue,
    issue_payloads,
    raw_string,
    source_record_key,
    terminal_status,
)
from bisfin.ingestion.results import DailyBarIngestionResult
from bisfin.integrations.brsapi import (
    BrsApiClient,
    BrsApiContractError,
    BrsApiError,
    BrsApiHttpError,
    BrsApiMalformedResponseError,
    BrsApiProviderError,
    BrsApiRawResponse,
    DailyBarParseResult,
    ParsedDailyBarCandidate,
    RowValidationResult,
    normalize_brsapi_symbol,
    parse_unadjusted_daily_candles,
    response_payload_sha256,
    row_payload_sha256,
)
from bisfin.logging import clear_log_context, log_context
from bisfin.repositories.protocols import (
    BarWriterRepository,
    DataFeedRepository,
    IngestionBatchRepository,
    InstrumentRepository,
    RawEventRepository,
)

PARSER_VERSION = "brsapi-candlestick-type2-v1"

type Clock = Callable[[], datetime]
type IdGenerator = Callable[[], UUID | str]

_LOGGER = logging.getLogger(__name__)


class BrsApiIngestionSettings(Protocol):
    @property
    def brsapi_provider_code(self) -> str: ...

    @property
    def brsapi_daily_raw_feed_code(self) -> str: ...

    @property
    def brsapi_identifier_type(self) -> str: ...


class IngestionUnitOfWork(Protocol):
    @property
    def data_feeds(self) -> DataFeedRepository: ...

    @property
    def instruments(self) -> InstrumentRepository: ...

    @property
    def ingestion_batches(self) -> IngestionBatchRepository: ...

    @property
    def raw_events(self) -> RawEventRepository: ...

    @property
    def bar_writer(self) -> BarWriterRepository: ...

    def __enter__(self) -> Self: ...

    def commit(self) -> None: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


type UnitOfWorkFactory = Callable[[], IngestionUnitOfWork]


class RequestIdConflictError(BisfinError):
    """A request identifier is already running or belongs to another symbol."""


class DailyBarCanonicalizationError(BisfinError):
    """Raw acquisition committed, but canonicalization failed unexpectedly."""


class DailyBarIngestionError(BisfinError):
    """A started batch failed outside the expected provider outcomes."""


@dataclass(frozen=True, slots=True)
class _Acquisition:
    response: BrsApiRawResponse
    parsed: DailyBarParseResult
    raw_events: tuple[RawEvent, ...]
    source_watermark: str | None


@dataclass(frozen=True, slots=True)
class _ResolvedRow:
    row: RowValidationResult
    raw_event: RawEvent
    candidate: ParsedDailyBarCandidate
    resolution: SessionResolvedInstrument


class BrsApiDailyBarIngestionService:
    """Acquire, preserve, validate, and append BrsApi ``type=2`` daily bars."""

    def __init__(
        self,
        *,
        client: BrsApiClient,
        unit_of_work_factory: UnitOfWorkFactory,
        settings: BrsApiIngestionSettings,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self._client = client
        self._uow_factory = unit_of_work_factory
        self._settings = settings
        self._clock = clock or _utc_now
        self._id_generator = id_generator or uuid4

    def ingest(
        self,
        *,
        symbol: str,
        request_id: str | None = None,
    ) -> DailyBarIngestionResult:
        """Run one request without holding a database transaction during I/O."""

        clear_log_context()
        try:
            normalized_symbol = normalize_brsapi_symbol(symbol)
            if not normalized_symbol:
                raise ValueError("symbol must not be empty")
            effective_request_id = request_id.strip() if request_id is not None else ""
            if not effective_request_id:
                effective_request_id = str(self._id_generator())

            with log_context(
                request_id=effective_request_id,
                correlation_id=effective_request_id,
                provider_code=self._settings.brsapi_provider_code,
                feed_code=self._settings.brsapi_daily_raw_feed_code,
                symbol=normalized_symbol,
            ):
                provider, feed, batch, idempotent = self._start_batch(
                    symbol=normalized_symbol,
                    request_id=effective_request_id,
                )
                if idempotent:
                    return _result_from_batch(
                        batch,
                        provider_code=provider.provider_code,
                        feed_code=feed.feed_code,
                        symbol=normalized_symbol,
                        idempotent_replay=True,
                    )

                with log_context(ingestion_batch_id=batch.ingestion_batch_id):
                    try:
                        return self._run_started_batch(
                            provider=provider,
                            feed=feed,
                            batch=batch,
                            symbol=normalized_symbol,
                        )
                    except DailyBarCanonicalizationError:
                        raise
                    except Exception as error:
                        self._mark_unexpected_started_failure(batch=batch, error=error)
                        raise DailyBarIngestionError(
                            "The started BrsApi ingestion batch failed unexpectedly."
                        ) from error
        finally:
            clear_log_context()

    def _start_batch(
        self,
        *,
        symbol: str,
        request_id: str,
    ) -> tuple[Provider, DataFeed, IngestionBatch, bool]:
        started_at = self._now()
        with self._uow_factory() as unit_of_work:
            provider = unit_of_work.data_feeds.get_provider_by_code(
                self._settings.brsapi_provider_code
            )
            feed = unit_of_work.data_feeds.get_feed_by_code(
                provider.provider_id,
                self._settings.brsapi_daily_raw_feed_code,
            )
            start = unit_of_work.ingestion_batches.create_batch_if_absent(
                feed_id=feed.feed_id,
                parser_version=PARSER_VERSION,
                request_id=request_id,
                started_at=started_at,
                metadata={
                    "provider_code": provider.provider_code,
                    "feed_code": feed.feed_code,
                    "symbol": symbol,
                    "request_type": 2,
                    "endpoint": "Tsetmc/Candlestick.php",
                },
            )
            if start.created:
                unit_of_work.commit()

        batch = start.batch
        if not start.created:
            recorded_symbol = batch.metadata.get("symbol")
            if recorded_symbol != symbol:
                raise RequestIdConflictError(
                    "The request_id already belongs to a different normalized symbol."
                )
            if batch.status is IngestionBatchStatus.RUNNING:
                raise RequestIdConflictError("The request_id already has a RUNNING batch.")
            _LOGGER.info("batch_finalized")
            return provider, feed, batch, True

        _LOGGER.info("batch_started")
        return provider, feed, batch, False

    def _run_started_batch(
        self,
        *,
        provider: Provider,
        feed: DataFeed,
        batch: IngestionBatch,
        symbol: str,
    ) -> DailyBarIngestionResult:
        try:
            response = self._client.fetch_unadjusted_daily_candles(symbol)
        except BrsApiHttpError as error:
            _LOGGER.info("provider_response_received")
            self._persist_empty_acquisition(
                batch=batch,
                feed=feed,
                response=error.response,
                provider_status="http_error",
            )
            return self._finalize_expected_failure(
                provider=provider,
                feed=feed,
                batch=batch,
                symbol=symbol,
                status=IngestionBatchStatus.FAILED,
                error_code="BRSAPI_HTTP_ERROR",
                error=error,
                response=error.response,
            )
        except BrsApiError as error:
            return self._finalize_expected_failure(
                provider=provider,
                feed=feed,
                batch=batch,
                symbol=symbol,
                status=IngestionBatchStatus.FAILED,
                error_code="BRSAPI_TRANSPORT_ERROR",
                error=error,
            )

        _LOGGER.info("provider_response_received")
        try:
            parsed = parse_unadjusted_daily_candles(response, requested_symbol=symbol)
        except BrsApiProviderError as error:
            self._persist_empty_acquisition(
                batch=batch,
                feed=feed,
                response=response,
                provider_status="provider_error",
            )
            return self._finalize_expected_failure(
                provider=provider,
                feed=feed,
                batch=batch,
                symbol=symbol,
                status=IngestionBatchStatus.FAILED,
                error_code="BRSAPI_PROVIDER_ERROR",
                error=error,
                response=response,
            )
        except (BrsApiMalformedResponseError, BrsApiContractError) as error:
            self._persist_empty_acquisition(
                batch=batch,
                feed=feed,
                response=response,
                provider_status="quarantined_contract",
                diagnostic_preview=_bounded_body_diagnostic(response.body_bytes),
            )
            return self._finalize_expected_failure(
                provider=provider,
                feed=feed,
                batch=batch,
                symbol=symbol,
                status=IngestionBatchStatus.QUARANTINED,
                error_code="BRSAPI_MALFORMED_OR_AMBIGUOUS_RESPONSE",
                error=error,
                response=response,
            )

        acquisition = self._persist_acquisition(
            feed=feed,
            batch=batch,
            symbol=symbol,
            response=response,
            parsed=parsed,
        )
        if parsed.no_data is not None:
            return self._finalize_no_data(
                provider=provider,
                feed=feed,
                batch=batch,
                symbol=symbol,
                acquisition=acquisition,
            )

        try:
            return self._canonicalize(
                provider=provider,
                feed=feed,
                batch=batch,
                symbol=symbol,
                acquisition=acquisition,
            )
        except Exception as error:
            self._mark_unexpected_canonicalization_failure(
                batch=batch,
                acquisition=acquisition,
                error=error,
            )
            raise DailyBarCanonicalizationError(
                "Raw BrsApi acquisition was preserved, but canonicalization failed."
            ) from error

    def _persist_acquisition(
        self,
        *,
        feed: DataFeed,
        batch: IngestionBatch,
        symbol: str,
        response: BrsApiRawResponse,
        parsed: DailyBarParseResult,
    ) -> _Acquisition:
        watermark = _source_watermark(parsed)
        raw_events: list[RawEvent] = []
        with self._uow_factory() as unit_of_work:
            if parsed.rows:
                unit_of_work.raw_events.ensure_month_partition(response.response_received_at)
            for row in parsed.rows:
                source_symbol = raw_string(row.raw_payload, "l18")
                normalized_row_symbol = (
                    normalize_brsapi_symbol(source_symbol) if source_symbol is not None else symbol
                )
                raw_events.append(
                    unit_of_work.raw_events.insert_response_record(
                        ingested_at=response.response_received_at,
                        ingestion_batch_id=batch.ingestion_batch_id,
                        feed_id=feed.feed_id,
                        source_record_key=source_record_key(
                            normalized_symbol=normalized_row_symbol or symbol,
                            source_date_text=raw_string(row.raw_payload, "date"),
                        ),
                        source_event_time_text=raw_string(row.raw_payload, "time"),
                        source_date_text=raw_string(row.raw_payload, "date"),
                        source_sequence=row.source_sequence,
                        observed_at=response.response_received_at,
                        payload_sha256=row_payload_sha256(row.raw_payload),
                        raw_payload=row.raw_payload,
                    )
                )
            unit_of_work.ingestion_batches.record_acquisition(
                batch.ingestion_batch_id,
                payload_sha256=parsed.response_sha256,
                received_row_count=len(parsed.rows),
                source_watermark=watermark,
                metadata=_acquisition_metadata(
                    response,
                    provider_status="no_data" if parsed.no_data is not None else "rows",
                    inserted_raw_count=len(raw_events),
                ),
            )
            unit_of_work.commit()

        _LOGGER.info("raw_events_persisted")
        return _Acquisition(
            response=response,
            parsed=parsed,
            raw_events=tuple(raw_events),
            source_watermark=watermark,
        )

    def _persist_empty_acquisition(
        self,
        *,
        batch: IngestionBatch,
        feed: DataFeed,
        response: BrsApiRawResponse,
        provider_status: str,
        diagnostic_preview: str | None = None,
    ) -> None:
        digest = response_payload_sha256(response.body_bytes)
        with self._uow_factory() as unit_of_work:
            unit_of_work.ingestion_batches.record_acquisition(
                batch.ingestion_batch_id,
                payload_sha256=digest,
                received_row_count=0,
                metadata=_acquisition_metadata(
                    response,
                    provider_status=provider_status,
                    inserted_raw_count=0,
                    diagnostic_preview=diagnostic_preview,
                ),
            )
            unit_of_work.commit()
        _LOGGER.info("raw_events_persisted")

    def _canonicalize(
        self,
        *,
        provider: Provider,
        feed: DataFeed,
        batch: IngestionBatch,
        symbol: str,
        acquisition: _Acquisition,
    ) -> DailyBarIngestionResult:
        accepted_count = 0
        rejected_count = 0
        inserted_count = 0
        corrected_count = 0
        unchanged_count = 0
        resolved_rows: list[_ResolvedRow] = []

        if len(acquisition.raw_events) != len(acquisition.parsed.rows):
            raise RuntimeError("raw-event acquisition order does not match parsed rows")

        with self._uow_factory() as unit_of_work:
            current_provider = unit_of_work.data_feeds.get_provider_by_code(
                self._settings.brsapi_provider_code
            )
            current_feed = unit_of_work.data_feeds.get_feed_by_code(
                current_provider.provider_id,
                self._settings.brsapi_daily_raw_feed_code,
            )
            timeframe = unit_of_work.data_feeds.get_timeframe_by_code("1d")
            if (
                current_provider.provider_id != provider.provider_id
                or current_feed.feed_id != feed.feed_id
            ):
                raise RuntimeError("provider/feed identity changed during ingestion")

            for row, raw_event in zip(
                acquisition.parsed.rows,
                acquisition.raw_events,
                strict=True,
            ):
                diagnostics = issue_payloads(errors=row.errors, warnings=row.warnings)
                if not row.accepted or row.candidate is None:
                    unit_of_work.raw_events.update_validation_result(
                        raw_event.identity,
                        validation_status=RawEventValidationStatus.REJECTED,
                        validation_errors=diagnostics,
                    )
                    rejected_count += 1
                    continue

                if not row.include_in_canonicalization:
                    unit_of_work.raw_events.update_validation_result(
                        raw_event.identity,
                        validation_status=RawEventValidationStatus.ACCEPTED,
                        validation_errors=diagnostics,
                    )
                    accepted_count += 1
                    continue

                resolution = unit_of_work.instruments.find_by_identifier_for_regular_session(
                    provider.provider_id,
                    self._settings.brsapi_identifier_type,
                    row.candidate.normalized_symbol,
                    row.candidate.trading_date,
                )
                if resolution is None:
                    identifier_exists = unit_of_work.instruments.identifier_exists(
                        provider.provider_id,
                        self._settings.brsapi_identifier_type,
                        row.candidate.normalized_symbol,
                    )
                    code = (
                        CanonicalizationCode.TRADING_SESSION_NOT_FOUND
                        if identifier_exists
                        else CanonicalizationCode.INSTRUMENT_NOT_FOUND
                    )
                    diagnostics.append(
                        canonicalization_issue(
                            code,
                            field="l18" if not identifier_exists else "date",
                            message=(
                                "No pre-provisioned instrument identifier exists."
                                if not identifier_exists
                                else "No exact identifier and REGULAR-session pairing exists."
                            ),
                        )
                    )
                    unit_of_work.raw_events.update_validation_result(
                        raw_event.identity,
                        validation_status=RawEventValidationStatus.REJECTED,
                        validation_errors=diagnostics,
                    )
                    rejected_count += 1
                    continue

                session_issue = _validate_resolution(resolution)
                if session_issue is not None:
                    diagnostics.append(session_issue)
                    unit_of_work.raw_events.update_validation_result(
                        raw_event.identity,
                        validation_status=RawEventValidationStatus.REJECTED,
                        validation_errors=diagnostics,
                    )
                    rejected_count += 1
                    continue

                assert resolution.trading_session.session_close_ts is not None
                if (
                    acquisition.response.response_received_at
                    < resolution.trading_session.session_close_ts
                ):
                    diagnostics.append(
                        canonicalization_issue(
                            CanonicalizationCode.RESPONSE_BEFORE_SESSION_CLOSE,
                            field="available_at",
                            message="A final daily bar cannot be available before session close.",
                        )
                    )
                    unit_of_work.raw_events.update_validation_result(
                        raw_event.identity,
                        validation_status=RawEventValidationStatus.REJECTED,
                        validation_errors=diagnostics,
                    )
                    rejected_count += 1
                    continue

                resolved_rows.append(
                    _ResolvedRow(
                        row=row,
                        raw_event=raw_event,
                        candidate=row.candidate,
                        resolution=resolution,
                    )
                )

            unit_of_work.bar_writer.ensure_month_partitions(
                item.candidate.trading_date for item in resolved_rows
            )
            for item in resolved_rows:
                diagnostics = issue_payloads(
                    errors=item.row.errors,
                    warnings=item.row.warnings,
                )
                session = item.resolution.trading_session
                assert session.session_open_ts is not None
                assert session.session_close_ts is not None
                system_available_at = self._now()
                if system_available_at < acquisition.response.response_received_at:
                    diagnostics.append(
                        canonicalization_issue(
                            CanonicalizationCode.SYSTEM_AVAILABILITY_BEFORE_PUBLIC,
                            field="system_available_at",
                            message="System availability cannot precede response receipt.",
                        )
                    )
                    unit_of_work.raw_events.update_validation_result(
                        item.raw_event.identity,
                        validation_status=RawEventValidationStatus.REJECTED,
                        validation_errors=diagnostics,
                    )
                    rejected_count += 1
                    continue

                series = unit_of_work.bar_writer.get_or_create_daily_raw_series(
                    feed_id=feed.feed_id,
                    instrument_id=item.resolution.instrument.instrument_id,
                    timeframe_id=timeframe.timeframe_id,
                    metadata={"provider_code": provider.provider_code, "request_type": 2},
                )
                write = unit_of_work.bar_writer.append_revision_if_changed(
                    BarRevisionCandidate(
                        bar_open_ts=session.session_open_ts,
                        bar_series_id=series.bar_series_id,
                        available_at=acquisition.response.response_received_at,
                        system_available_at=system_available_at,
                        bar_close_ts=session.session_close_ts,
                        trading_date=item.candidate.trading_date,
                        open_price=item.candidate.open,
                        high_price=item.candidate.high,
                        low_price=item.candidate.low,
                        close_price=item.candidate.close,
                        volume=item.candidate.volume,
                        official_close_price=None,
                        settlement_price=None,
                        quote_volume=None,
                        trade_count=None,
                        vwap=None,
                        open_interest=None,
                        previous_close_price=None,
                        is_final=True,
                        quality_flags=0,
                        ingestion_batch_id=batch.ingestion_batch_id,
                    )
                )
                if write.status is BarRevisionWriteStatus.INSERTED:
                    inserted_count += 1
                elif write.status is BarRevisionWriteStatus.CORRECTED:
                    corrected_count += 1
                else:
                    unchanged_count += 1
                unit_of_work.raw_events.update_validation_result(
                    item.raw_event.identity,
                    validation_status=RawEventValidationStatus.ACCEPTED,
                    validation_errors=diagnostics,
                )
                accepted_count += 1

            _LOGGER.info("rows_validated")
            _LOGGER.info("canonicalization_completed")
            status = terminal_status(
                accepted_count=accepted_count,
                rejected_count=rejected_count,
            )
            finished = unit_of_work.ingestion_batches.finalize_batch(
                batch.ingestion_batch_id,
                status=status,
                received_row_count=len(acquisition.parsed.rows),
                accepted_row_count=accepted_count,
                rejected_row_count=rejected_count,
                payload_sha256=acquisition.parsed.response_sha256,
                source_watermark=acquisition.source_watermark,
                error_summary=(
                    None
                    if status is IngestionBatchStatus.SUCCEEDED
                    else "One or more rows were rejected."
                ),
                metadata=_completion_metadata(
                    raw_inserted_count=len(acquisition.raw_events),
                    accepted_count=accepted_count,
                    rejected_count=rejected_count,
                    inserted_count=inserted_count,
                    corrected_count=corrected_count,
                    unchanged_count=unchanged_count,
                ),
                finished_at=self._now(),
            )
            unit_of_work.commit()

        _LOGGER.info("batch_finalized")
        return _result_from_batch(
            finished,
            provider_code=provider.provider_code,
            feed_code=feed.feed_code,
            symbol=symbol,
        )

    def _finalize_no_data(
        self,
        *,
        provider: Provider,
        feed: DataFeed,
        batch: IngestionBatch,
        symbol: str,
        acquisition: _Acquisition,
    ) -> DailyBarIngestionResult:
        with self._uow_factory() as unit_of_work:
            finished = unit_of_work.ingestion_batches.finalize_batch(
                batch.ingestion_batch_id,
                status=IngestionBatchStatus.SUCCEEDED,
                received_row_count=0,
                accepted_row_count=0,
                rejected_row_count=0,
                payload_sha256=acquisition.parsed.response_sha256,
                metadata=_completion_metadata(
                    raw_inserted_count=0,
                    accepted_count=0,
                    rejected_count=0,
                    inserted_count=0,
                    corrected_count=0,
                    unchanged_count=0,
                ),
                finished_at=self._now(),
            )
            unit_of_work.commit()
        _LOGGER.info("rows_validated")
        _LOGGER.info("canonicalization_completed")
        _LOGGER.info("batch_finalized")
        return _result_from_batch(
            finished,
            provider_code=provider.provider_code,
            feed_code=feed.feed_code,
            symbol=symbol,
        )

    def _finalize_expected_failure(
        self,
        *,
        provider: Provider,
        feed: DataFeed,
        batch: IngestionBatch,
        symbol: str,
        status: IngestionBatchStatus,
        error_code: str,
        error: BaseException,
        response: BrsApiRawResponse | None = None,
    ) -> DailyBarIngestionResult:
        digest = response_payload_sha256(response.body_bytes) if response is not None else None
        with self._uow_factory() as unit_of_work:
            current = unit_of_work.ingestion_batches.get_by_id(batch.ingestion_batch_id)
            if current is None:
                raise RuntimeError("started ingestion batch disappeared")
            finished = unit_of_work.ingestion_batches.finalize_batch(
                batch.ingestion_batch_id,
                status=status,
                received_row_count=current.received_row_count,
                accepted_row_count=0,
                rejected_row_count=0,
                payload_sha256=digest,
                error_summary=f"{error_code}: {redact_secrets(error)}",
                metadata={
                    **_completion_metadata(
                        raw_inserted_count=0,
                        accepted_count=0,
                        rejected_count=0,
                        inserted_count=0,
                        corrected_count=0,
                        unchanged_count=0,
                    ),
                    "failure_code": error_code,
                },
                finished_at=self._now(),
            )
            unit_of_work.commit()
        _LOGGER.info("batch_failed")
        return _result_from_batch(
            finished,
            provider_code=provider.provider_code,
            feed_code=feed.feed_code,
            symbol=symbol,
        )

    def _mark_unexpected_canonicalization_failure(
        self,
        *,
        batch: IngestionBatch,
        acquisition: _Acquisition,
        error: BaseException,
    ) -> None:
        with self._uow_factory() as unit_of_work:
            unit_of_work.ingestion_batches.finalize_batch(
                batch.ingestion_batch_id,
                status=IngestionBatchStatus.FAILED,
                received_row_count=len(acquisition.parsed.rows),
                accepted_row_count=0,
                rejected_row_count=0,
                payload_sha256=acquisition.parsed.response_sha256,
                source_watermark=acquisition.source_watermark,
                error_summary=f"CANONICALIZATION_FAILED: {redact_secrets(error)}",
                metadata={
                    **_completion_metadata(
                        raw_inserted_count=len(acquisition.raw_events),
                        accepted_count=0,
                        rejected_count=0,
                        inserted_count=0,
                        corrected_count=0,
                        unchanged_count=0,
                    ),
                    "failure_code": "CANONICALIZATION_FAILED",
                },
                finished_at=self._now(),
            )
            unit_of_work.commit()
        _LOGGER.info("batch_failed")

    def _mark_unexpected_started_failure(
        self,
        *,
        batch: IngestionBatch,
        error: BaseException,
    ) -> None:
        """Best-effort terminal transition after a committed Transaction A."""

        try:
            with self._uow_factory() as unit_of_work:
                current = unit_of_work.ingestion_batches.get_by_id(batch.ingestion_batch_id)
                if current is None or current.status is not IngestionBatchStatus.RUNNING:
                    return
                unit_of_work.ingestion_batches.finalize_batch(
                    batch.ingestion_batch_id,
                    status=IngestionBatchStatus.FAILED,
                    received_row_count=current.received_row_count,
                    accepted_row_count=current.accepted_row_count,
                    rejected_row_count=current.rejected_row_count,
                    payload_sha256=current.payload_sha256,
                    source_watermark=current.source_watermark,
                    error_summary=f"INGESTION_FAILED: {redact_secrets(error)}",
                    metadata={"failure_code": "INGESTION_FAILED"},
                    finished_at=self._now(),
                )
                unit_of_work.commit()
            _LOGGER.info("batch_failed")
        except Exception:
            _LOGGER.exception("batch_failed")

    def _now(self) -> datetime:
        return require_aware_datetime(self._clock())


def _validate_resolution(resolution: SessionResolvedInstrument) -> dict[str, object] | None:
    instrument = resolution.instrument
    session = resolution.trading_session
    if instrument.venue_id is None or instrument.venue_id != session.venue_id:
        return canonicalization_issue(
            CanonicalizationCode.INSTRUMENT_VENUE_MISSING,
            field="venue_id",
            message="The resolved instrument has no matching canonical venue.",
        )
    if (
        not session.is_trading_day
        or session.session_open_ts is None
        or session.session_close_ts is None
        or session.session_close_ts <= session.session_open_ts
    ):
        return canonicalization_issue(
            CanonicalizationCode.INVALID_TRADING_SESSION,
            field="date",
            message="The REGULAR trading session is absent or structurally invalid.",
        )
    return None


def _source_watermark(parsed: DailyBarParseResult) -> str | None:
    dates = [row.candidate.trading_date for row in parsed.rows if row.candidate is not None]
    return max(dates).isoformat() if dates else None


def _acquisition_metadata(
    response: BrsApiRawResponse,
    *,
    provider_status: str,
    inserted_raw_count: int,
    diagnostic_preview: str | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "http_status": response.status_code,
        "response_elapsed_ms": int(response.elapsed.total_seconds() * 1_000),
        "response_received_at": response.response_received_at.isoformat(),
        "response_headers": dict(response.headers),
        "provider_status": provider_status,
        "inserted_raw_count": inserted_raw_count,
    }
    if diagnostic_preview is not None:
        metadata["body_diagnostic_preview"] = diagnostic_preview
    return metadata


def _bounded_body_diagnostic(body_bytes: bytes) -> str:
    """Return a short readable preview without treating invalid bytes as JSON."""

    decoded = body_bytes[:1_024].decode("utf-8", errors="replace")
    return redact_secrets(decoded)[:512]


def _completion_metadata(
    *,
    raw_inserted_count: int,
    accepted_count: int,
    rejected_count: int,
    inserted_count: int,
    corrected_count: int,
    unchanged_count: int,
) -> dict[str, object]:
    return {
        "inserted_raw_count": raw_inserted_count,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "inserted_revision_count": inserted_count,
        "corrected_revision_count": corrected_count,
        "unchanged_bar_count": unchanged_count,
    }


def _metadata_count(batch: IngestionBatch, key: str) -> int:
    value = batch.metadata.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _result_from_batch(
    batch: IngestionBatch,
    *,
    provider_code: str,
    feed_code: str,
    symbol: str,
    idempotent_replay: bool = False,
) -> DailyBarIngestionResult:
    return DailyBarIngestionResult(
        ingestion_batch_id=batch.ingestion_batch_id,
        status=batch.status,
        provider_code=provider_code,
        feed_code=feed_code,
        symbol=symbol,
        received_count=batch.received_row_count,
        accepted_count=batch.accepted_row_count,
        rejected_count=batch.rejected_row_count,
        raw_inserted_count=_metadata_count(batch, "inserted_raw_count"),
        bar_inserted_count=_metadata_count(batch, "inserted_revision_count"),
        bar_corrected_count=_metadata_count(batch, "corrected_revision_count"),
        bar_unchanged_count=_metadata_count(batch, "unchanged_bar_count"),
        source_watermark=batch.source_watermark,
        payload_sha256=batch.payload_sha256,
        started_at=batch.started_at,
        finished_at=batch.finished_at,
        idempotent_replay=idempotent_replay,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "BrsApiDailyBarIngestionService",
    "BrsApiIngestionSettings",
    "DailyBarCanonicalizationError",
    "DailyBarIngestionError",
    "IngestionUnitOfWork",
    "PARSER_VERSION",
    "RequestIdConflictError",
    "UnitOfWorkFactory",
]
