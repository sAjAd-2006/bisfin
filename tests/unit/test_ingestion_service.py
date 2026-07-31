"""Transaction-boundary unit tests for the BrsApi daily-bar service.

The fakes below model commit/rollback visibility rather than merely recording
method calls.  This lets the tests prove that provider I/O observes transaction
A as committed, transaction B survives a later transaction-C failure, and no
repository opens an independent transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Self

import pytest

from bisfin.domain.catalog import (
    DataFeed,
    Instrument,
    InstrumentIdentifier,
    InstrumentSpecification,
    Provider,
    ResolvedInstrument,
    SessionResolvedInstrument,
    Timeframe,
    TradingSession,
)
from bisfin.domain.ingestion import (
    IngestionBatch,
    IngestionBatchStartResult,
    IngestionBatchStatus,
    RawEvent,
    RawEventIdentity,
    RawEventValidationStatus,
)
from bisfin.domain.market_data import (
    BarRevision,
    BarRevisionCandidate,
    BarRevisionWriteResult,
    BarRevisionWriteStatus,
    BarSeries,
)
from bisfin.ingestion.service import (
    BrsApiDailyBarIngestionService,
    DailyBarCanonicalizationError,
    DailyBarIngestionError,
    RequestIdConflictError,
)
from bisfin.integrations.brsapi import (
    BrsApiClient,
    BrsApiHttpError,
    BrsApiRawResponse,
    BrsApiTransportError,
    response_payload_sha256,
)
from bisfin.logging import bind_log_context, clear_log_context, get_log_context
from bisfin.repositories.protocols import (
    BarWriterRepository,
    DataFeedRepository,
    IngestionBatchRepository,
    InstrumentRepository,
    RawEventRepository,
)

pytestmark = pytest.mark.unit

_FIXTURES = Path("tests/fixtures/brsapi")
_STARTED = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
_RECEIVED = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
_SYSTEM_TIMES = tuple(_RECEIVED + timedelta(seconds=index) for index in (1, 2, 3))
_FINISHED = _RECEIVED + timedelta(seconds=10)


@dataclass
class _Store:
    batches: dict[int, IngestionBatch] = field(default_factory=dict)
    raw_events: dict[tuple[datetime, int], RawEvent] = field(default_factory=dict)
    bar_candidates: list[BarRevisionCandidate] = field(default_factory=list)
    next_batch_id: int = 1
    next_raw_event_id: int = 1


@dataclass
class _State:
    store: _Store = field(default_factory=_Store)
    events: list[str] = field(default_factory=list)
    active_transactions: int = 0
    next_uow_id: int = 1
    client_calls: int = 0
    resolution_enabled: bool = True
    identifier_exists_result: bool = True
    write_statuses: list[BarRevisionWriteStatus] = field(
        default_factory=lambda: [BarRevisionWriteStatus.INSERTED]
    )
    raise_on_append: BaseException | None = None
    raise_on_raw_insert: BaseException | None = None
    fail_commit_uow_ids: set[int] = field(default_factory=set)


@dataclass
class _Settings:
    brsapi_provider_code: str = "BRSAPI"
    brsapi_daily_raw_feed_code: str = "TSETMC_CANDLE_DAILY_RAW"
    brsapi_identifier_type: str = "BRSAPI_L18"


class _SequenceClock:
    def __init__(self, values: Sequence[datetime]) -> None:
        self._values = list(values)
        self._index = 0

    def __call__(self) -> datetime:
        if self._index >= len(self._values):
            raise AssertionError("service requested an unexpected clock value")
        value = self._values[self._index]
        self._index += 1
        return value


class _Client:
    def __init__(
        self,
        state: _State,
        *,
        response: BrsApiRawResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._state = state
        self._response = response
        self._error = error

    def fetch_unadjusted_daily_candles(self, symbol: str) -> BrsApiRawResponse:
        assert symbol
        assert self._state.active_transactions == 0, "HTTP ran inside a database transaction"
        assert any(
            batch.status is IngestionBatchStatus.RUNNING
            for batch in self._state.store.batches.values()
        ), "transaction A was not committed before HTTP"
        self._state.client_calls += 1
        self._state.events.append("client_fetch")
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class _DataFeeds:
    def __init__(self, state: _State, uow_id: int) -> None:
        self._state = state
        self._uow_id = uow_id

    def get_provider_by_code(self, provider_code: str) -> Provider:
        self._state.events.append(f"provider_lookup:{self._uow_id}")
        assert provider_code == "BRSAPI"
        return _provider()

    def get_feed_by_code(self, provider_id: int, feed_code: str) -> DataFeed:
        assert provider_id == 7
        assert feed_code == "TSETMC_CANDLE_DAILY_RAW"
        return _feed()

    def get_timeframe_by_code(self, timeframe_code: str) -> Timeframe:
        assert timeframe_code == "1d"
        return _timeframe()

    def get_regular_trading_session(
        self,
        venue_id: int,
        trading_date: date,
    ) -> TradingSession:
        assert venue_id == 11
        return _session(trading_date)


class _Instruments:
    def __init__(self, state: _State) -> None:
        self._state = state

    def get_by_id(self, instrument_id: int) -> Instrument | None:
        return _instrument() if instrument_id == 101 else None

    def identifier_exists(
        self,
        provider_id: int,
        identifier_type: str,
        identifier_value: str,
    ) -> bool:
        del provider_id, identifier_type, identifier_value
        return self._state.identifier_exists_result

    def find_by_identifier(
        self,
        provider_id: int,
        identifier_type: str,
        identifier_value: str,
        as_of: datetime,
    ) -> ResolvedInstrument | None:
        del as_of
        if provider_id != 7 or identifier_type != "BRSAPI_L18":
            return None
        return ResolvedInstrument(
            instrument=_instrument(),
            identifier=_identifier(identifier_value),
        )

    def get_active_spec(
        self,
        instrument_id: int,
        as_of: datetime,
    ) -> InstrumentSpecification | None:
        del instrument_id, as_of
        return None

    def find_by_identifier_for_regular_session(
        self,
        provider_id: int,
        identifier_type: str,
        identifier_value: str,
        trading_date: date,
    ) -> SessionResolvedInstrument | None:
        if not self._state.resolution_enabled:
            return None
        assert provider_id == 7
        assert identifier_type == "BRSAPI_L18"
        assert identifier_value == "فملی"
        return SessionResolvedInstrument(
            instrument=_instrument(),
            identifier=_identifier(identifier_value),
            trading_session=_session(trading_date),
        )


class _Batches:
    def __init__(self, store: _Store, state: _State, uow_id: int) -> None:
        self._store = store
        self._state = state
        self._uow_id = uow_id

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
        del requested_event_from, requested_event_to
        batch = IngestionBatch(
            ingestion_batch_id=self._store.next_batch_id,
            feed_id=feed_id,
            request_id=request_id,
            started_at=started_at or _STARTED,
            status=IngestionBatchStatus.RUNNING,
            parser_version=parser_version,
            source_watermark=source_watermark,
            metadata=dict(metadata or {}),
        )
        self._store.next_batch_id += 1
        self._store.batches[batch.ingestion_batch_id] = batch
        return batch

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
        self._state.events.append(f"batch_start:{self._uow_id}")
        existing = next(
            (
                batch
                for batch in self._store.batches.values()
                if batch.feed_id == feed_id and batch.request_id == request_id
            ),
            None,
        )
        if existing is not None:
            return IngestionBatchStartResult(batch=existing, created=False)
        return IngestionBatchStartResult(
            batch=self.create_batch(
                feed_id=feed_id,
                parser_version=parser_version,
                request_id=request_id,
                requested_event_from=requested_event_from,
                requested_event_to=requested_event_to,
                source_watermark=source_watermark,
                metadata=metadata,
                started_at=started_at,
            ),
            created=True,
        )

    def get_by_id(self, ingestion_batch_id: int) -> IngestionBatch | None:
        return self._store.batches.get(ingestion_batch_id)

    def get_by_request_id(self, feed_id: int, request_id: str) -> IngestionBatch | None:
        return next(
            (
                batch
                for batch in self._store.batches.values()
                if batch.feed_id == feed_id and batch.request_id == request_id
            ),
            None,
        )

    def mark_running(self, ingestion_batch_id: int) -> IngestionBatch:
        batch = self._required(ingestion_batch_id)
        if batch.status is not IngestionBatchStatus.RUNNING:
            raise RuntimeError("batch is terminal")
        return batch

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
            ingestion_batch_id,
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
        del details
        current = self._required(ingestion_batch_id)
        return self.finalize_batch(
            ingestion_batch_id,
            status=IngestionBatchStatus.FAILED,
            received_row_count=current.received_row_count,
            accepted_row_count=0,
            rejected_row_count=0,
            error_summary=f"{error_code}: {error_message}",
            finished_at=finished_at,
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
        self._state.events.append(f"record_acquisition:{self._uow_id}")
        current = self._required(ingestion_batch_id)
        updated = current.model_copy(
            update={
                "payload_sha256": payload_sha256,
                "received_row_count": received_row_count,
                "source_watermark": source_watermark,
                "metadata": {**current.metadata, **dict(metadata or {})},
            }
        )
        self._store.batches[ingestion_batch_id] = updated
        return updated

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
        self._state.events.append(f"finalize:{status.value}:{self._uow_id}")
        current = self._required(ingestion_batch_id)
        updated = current.model_copy(
            update={
                "status": status,
                "received_row_count": received_row_count,
                "accepted_row_count": accepted_row_count,
                "rejected_row_count": rejected_row_count,
                "payload_sha256": payload_sha256 or current.payload_sha256,
                "source_watermark": source_watermark or current.source_watermark,
                "error_summary": error_summary,
                "metadata": {**current.metadata, **dict(metadata or {})},
                "finished_at": finished_at or _FINISHED,
            }
        )
        self._store.batches[ingestion_batch_id] = updated
        return updated

    def _required(self, ingestion_batch_id: int) -> IngestionBatch:
        batch = self._store.batches.get(ingestion_batch_id)
        if batch is None:
            raise RuntimeError("missing fake batch")
        return batch


class _RawEvents:
    def __init__(self, store: _Store, state: _State, uow_id: int) -> None:
        self._store = store
        self._state = state
        self._uow_id = uow_id

    def ensure_month_partition(self, ingested_at: datetime) -> None:
        assert ingested_at == _RECEIVED
        self._state.events.append(f"raw_partition:{self._uow_id}")

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
        if self._state.raise_on_raw_insert is not None:
            raise self._state.raise_on_raw_insert
        event = RawEvent(
            ingested_at=ingested_at,
            raw_event_id=self._store.next_raw_event_id,
            ingestion_batch_id=ingestion_batch_id,
            feed_id=feed_id,
            source_record_key=source_record_key,
            source_event_time_text=source_event_time_text,
            source_date_text=source_date_text,
            source_sequence=source_sequence,
            observed_at=observed_at,
            payload_sha256=payload_sha256,
            raw_payload=dict(raw_payload),
            validation_status=validation_status,
            validation_errors=list(validation_errors),
        )
        self._store.next_raw_event_id += 1
        self._store.raw_events[(event.ingested_at, event.raw_event_id)] = event
        return event

    def update_validation_result(
        self,
        identity: RawEventIdentity,
        *,
        validation_status: RawEventValidationStatus,
        validation_errors: Sequence[object] = (),
    ) -> RawEvent:
        key = (identity.ingested_at, identity.raw_event_id)
        current = self._store.raw_events[key]
        updated = current.model_copy(
            update={
                "validation_status": validation_status,
                "validation_errors": list(validation_errors),
            }
        )
        self._store.raw_events[key] = updated
        return updated

    def find_identical_record(
        self,
        *,
        feed_id: int,
        source_record_key: str,
        payload_sha256: str,
    ) -> RawEvent | None:
        return next(
            (
                event
                for event in self._store.raw_events.values()
                if event.feed_id == feed_id
                and event.source_record_key == source_record_key
                and event.payload_sha256 == payload_sha256
            ),
            None,
        )

    def list_by_batch(self, ingestion_batch_id: int) -> Sequence[RawEvent]:
        return tuple(
            event
            for event in self._store.raw_events.values()
            if event.ingestion_batch_id == ingestion_batch_id
        )


class _BarWriter:
    def __init__(self, store: _Store, state: _State, uow_id: int) -> None:
        self._store = store
        self._state = state
        self._uow_id = uow_id

    def ensure_month_partitions(self, trading_dates: Iterable[date]) -> None:
        months = sorted({value.replace(day=1) for value in trading_dates})
        self._state.events.append(f"bar_partitions:{self._uow_id}:{len(months)}")

    def get_or_create_daily_raw_series(
        self,
        *,
        feed_id: int,
        instrument_id: int,
        timeframe_id: int,
        metadata: Mapping[str, Any] | None = None,
    ) -> BarSeries:
        del metadata
        return BarSeries(
            bar_series_id=501,
            feed_id=feed_id,
            instrument_id=instrument_id,
            timeframe_id=timeframe_id,
            price_basis="RAW",
            adjustment_set_id=None,
            close_semantics="LAST_TRADE",
            session_code="REGULAR",
            created_at=_STARTED,
        )

    def append_revision_if_changed(
        self,
        candidate: BarRevisionCandidate,
    ) -> BarRevisionWriteResult:
        self._state.events.append(f"bar_append:{self._uow_id}")
        if self._state.raise_on_append is not None:
            raise self._state.raise_on_append
        status = (
            self._state.write_statuses.pop(0)
            if self._state.write_statuses
            else BarRevisionWriteStatus.UNCHANGED
        )
        self._store.bar_candidates.append(candidate)
        return BarRevisionWriteResult(
            status=status,
            revision=BarRevision(
                **candidate.model_dump(),
                revision_no=(2 if status is BarRevisionWriteStatus.CORRECTED else 1),
                recorded_at=candidate.system_available_at,
            ),
        )


class _UnitOfWork:
    def __init__(self, state: _State) -> None:
        self._state = state
        self._uow_id = state.next_uow_id
        state.next_uow_id += 1
        self._store = deepcopy(state.store)
        self._committed = False
        self._data_feeds = _DataFeeds(state, self._uow_id)
        self._instruments = _Instruments(state)
        self._batches = _Batches(self._store, state, self._uow_id)
        self._raw_events = _RawEvents(self._store, state, self._uow_id)
        self._bar_writer = _BarWriter(self._store, state, self._uow_id)

    @property
    def data_feeds(self) -> DataFeedRepository:
        return self._data_feeds

    @property
    def instruments(self) -> InstrumentRepository:
        return self._instruments

    @property
    def ingestion_batches(self) -> IngestionBatchRepository:
        return self._batches

    @property
    def raw_events(self) -> RawEventRepository:
        return self._raw_events

    @property
    def bar_writer(self) -> BarWriterRepository:
        return self._bar_writer

    def __enter__(self) -> Self:
        self._state.active_transactions += 1
        self._state.events.append(f"uow_enter:{self._uow_id}")
        return self

    def commit(self) -> None:
        if self._uow_id in self._state.fail_commit_uow_ids:
            self._state.events.append(f"commit_failed:{self._uow_id}")
            raise RuntimeError(f"injected commit failure for transaction {self._uow_id}")
        self._state.store = deepcopy(self._store)
        self._committed = True
        self._state.events.append(f"commit:{self._uow_id}")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, exc, traceback
        if not self._committed:
            self._state.events.append(f"rollback:{self._uow_id}")
        self._state.events.append(f"uow_exit:{self._uow_id}")
        self._state.active_transactions -= 1


class _UnitOfWorkFactory:
    def __init__(self, state: _State) -> None:
        self._state = state

    def __call__(self) -> _UnitOfWork:
        return _UnitOfWork(self._state)


def _provider() -> Provider:
    return Provider(
        provider_id=7,
        provider_code="BRSAPI",
        display_name="BrsApi fixture",
        provider_kind="VENDOR",
        default_timezone="Asia/Tehran",
        created_at=_STARTED,
    )


def _feed() -> DataFeed:
    return DataFeed(
        feed_id=31,
        provider_id=7,
        feed_code="TSETMC_CANDLE_DAILY_RAW",
        display_name="Fixture daily RAW",
        data_kind="BAR",
        native_timezone="Asia/Tehran",
    )


def _timeframe() -> Timeframe:
    return Timeframe(
        timeframe_id=4,
        timeframe_code="1d",
        display_name="One session",
        duration_seconds=None,
        calendar_unit="SESSION",
        session_aligned=True,
    )


def _instrument() -> Instrument:
    return Instrument(
        instrument_id=101,
        asset_type_code="EQUITY",
        venue_id=11,
        quote_currency_code="IRR",
        canonical_symbol="فملی",
        display_name="ملی صنایع مس ایران",
        status="ACTIVE",
        created_at=_STARTED,
    )


def _identifier(value: str) -> InstrumentIdentifier:
    return InstrumentIdentifier(
        provider_id=7,
        identifier_type="BRSAPI_L18",
        identifier_value=value,
        valid_from=None,
        instrument_id=101,
        is_primary=True,
    )


def _session(trading_date: date) -> TradingSession:
    opening = datetime(
        trading_date.year,
        trading_date.month,
        trading_date.day,
        5,
        0,
        tzinfo=UTC,
    )
    return TradingSession(
        venue_id=11,
        trading_date=trading_date,
        session_code="REGULAR",
        is_trading_day=True,
        session_open_ts=opening,
        session_close_ts=opening + timedelta(hours=4),
    )


def _response(fixture: str) -> BrsApiRawResponse:
    return BrsApiRawResponse(
        status_code=200,
        headers=(("content-type", "application/json"),),
        body_bytes=(_FIXTURES / fixture).read_bytes(),
        request_started_at=_RECEIVED - timedelta(milliseconds=50),
        response_received_at=_RECEIVED,
        elapsed=timedelta(milliseconds=50),
    )


def _service(
    state: _State,
    *,
    fixture: str = "candlestick_type2_success.json",
    response: BrsApiRawResponse | None = None,
    error: BaseException | None = None,
    clock_values: Sequence[datetime] | None = None,
    id_generator: Callable[[], str] | None = None,
) -> tuple[BrsApiDailyBarIngestionService, _Client]:
    client = _Client(
        state,
        response=response if response is not None else _response(fixture),
        error=error,
    )
    typed_client: BrsApiClient = client
    service = BrsApiDailyBarIngestionService(
        client=typed_client,
        unit_of_work_factory=_UnitOfWorkFactory(state),
        settings=_Settings(),
        clock=_SequenceClock(clock_values or (_STARTED, *_SYSTEM_TIMES, _FINISHED)),
        id_generator=id_generator or (lambda: "generated-request-id"),
    )
    return service, client


def _terminal_batch(
    *,
    status: IngestionBatchStatus,
    symbol: str = "فملی",
    request_id: str = "existing-request",
) -> IngestionBatch:
    return IngestionBatch(
        ingestion_batch_id=20,
        feed_id=31,
        request_id=request_id,
        started_at=_STARTED,
        finished_at=_FINISHED if status is not IngestionBatchStatus.RUNNING else None,
        status=status,
        received_row_count=3,
        accepted_row_count=2,
        rejected_row_count=1,
        payload_sha256="a" * 64,
        parser_version="brsapi-candlestick-type2-v1",
        source_watermark="2025-03-01",
        metadata={
            "symbol": symbol,
            "inserted_raw_count": 3,
            "inserted_revision_count": 1,
            "corrected_revision_count": 0,
            "unchanged_bar_count": 1,
        },
    )


def test_success_commits_a_before_http_b_before_c_and_uses_exact_availability(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = _State(
        write_statuses=[
            BarRevisionWriteStatus.INSERTED,
            BarRevisionWriteStatus.CORRECTED,
            BarRevisionWriteStatus.UNCHANGED,
        ]
    )
    service, _ = _service(state)
    caplog.set_level("INFO", logger="bisfin.ingestion.service")

    result = service.ingest(symbol="فملی", request_id="success-request")

    assert result.status is IngestionBatchStatus.SUCCEEDED
    assert (
        result.received_count,
        result.accepted_count,
        result.rejected_count,
        result.raw_inserted_count,
    ) == (3, 3, 0, 3)
    assert (
        result.bar_inserted_count,
        result.bar_corrected_count,
        result.bar_unchanged_count,
    ) == (1, 1, 1)
    assert result.source_watermark == "2025-03-01"
    assert result.payload_sha256 == response_payload_sha256(
        (_FIXTURES / "candlestick_type2_success.json").read_bytes()
    )

    events = state.events
    assert events.index("commit:1") < events.index("uow_exit:1") < events.index("client_fetch")
    assert events.index("client_fetch") < events.index("uow_enter:2")
    assert events.index("commit:2") < events.index("uow_exit:2") < events.index("uow_enter:3")
    assert len(state.store.raw_events) == 3
    assert all(
        event.validation_status is RawEventValidationStatus.ACCEPTED
        for event in state.store.raw_events.values()
    )
    assert len(state.store.bar_candidates) == 3
    for candidate, system_time in zip(state.store.bar_candidates, _SYSTEM_TIMES, strict=True):
        assert candidate.available_at == _RECEIVED
        assert candidate.system_available_at == system_time
        assert candidate.available_at >= candidate.bar_close_ts
        assert candidate.is_final is True
    assert get_log_context() == {}
    assert [record.message for record in caplog.records] == [
        "batch_started",
        "provider_response_received",
        "raw_events_persisted",
        "rows_validated",
        "canonicalization_completed",
        "batch_finalized",
    ]


def test_no_data_is_successful_zero_row_acquisition() -> None:
    state = _State()
    service, _ = _service(
        state,
        fixture="candlestick_type2_no_data.json",
        clock_values=(_STARTED, _FINISHED),
    )

    result = service.ingest(symbol="فملی", request_id="no-data-request")

    assert result.status is IngestionBatchStatus.SUCCEEDED
    assert (
        result.received_count,
        result.accepted_count,
        result.rejected_count,
        result.raw_inserted_count,
        result.bar_inserted_count,
    ) == (0, 0, 0, 0, 0)
    assert state.store.raw_events == {}
    assert state.store.bar_candidates == []
    assert state.events.index("commit:2") < state.events.index("uow_enter:3")


def test_partial_fixture_preserves_all_raw_rows_and_returns_partial_counts() -> None:
    state = _State(write_statuses=[BarRevisionWriteStatus.INSERTED])
    service, _ = _service(
        state,
        fixture="candlestick_type2_partial_invalid.json",
        clock_values=(_STARTED, _SYSTEM_TIMES[0], _FINISHED),
    )

    result = service.ingest(symbol="فملی", request_id="partial-request")

    assert result.status is IngestionBatchStatus.PARTIAL
    assert (result.received_count, result.accepted_count, result.rejected_count) == (4, 1, 3)
    assert result.raw_inserted_count == 4
    assert result.bar_inserted_count == 1
    statuses = [event.validation_status for event in state.store.raw_events.values()]
    assert statuses.count(RawEventValidationStatus.ACCEPTED) == 1
    assert statuses.count(RawEventValidationStatus.REJECTED) == 3


@pytest.mark.parametrize(
    ("fixture", "expected_status", "failure_code"),
    [
        (
            "candlestick_malformed_json.txt",
            IngestionBatchStatus.QUARANTINED,
            "BRSAPI_MALFORMED_OR_AMBIGUOUS_RESPONSE",
        ),
        (
            "candlestick_type2_provider_error.json",
            IngestionBatchStatus.FAILED,
            "BRSAPI_PROVIDER_ERROR",
        ),
    ],
)
def test_malformed_and_provider_error_commit_hash_before_terminal_status(
    fixture: str,
    expected_status: IngestionBatchStatus,
    failure_code: str,
) -> None:
    state = _State()
    service, _ = _service(
        state,
        fixture=fixture,
        clock_values=(_STARTED, _FINISHED),
    )

    result = service.ingest(symbol="فملی", request_id=f"failure-{fixture}")

    assert result.status is expected_status
    assert result.payload_sha256 == response_payload_sha256((_FIXTURES / fixture).read_bytes())
    assert result.received_count == 0
    assert result.raw_inserted_count == 0
    assert state.store.raw_events == {}
    batch = state.store.batches[result.ingestion_batch_id]
    assert batch.error_summary is not None
    assert failure_code in batch.error_summary
    assert state.events.index("commit:2") < state.events.index("uow_enter:3")


def test_malformed_body_metadata_has_bounded_redacted_preview_and_exact_hash() -> None:
    api_key = "super-secret-provider-key"
    body = (
        b'{"request_url":"https://Api.BrsApi.ir/Tsetmc/Candlestick.php?key='
        + api_key.encode()
        + b'&type=2","rows":['
        + (b"x" * 2_000)
    )
    response = BrsApiRawResponse(
        status_code=200,
        headers=(("content-type", "application/json"),),
        body_bytes=body,
        request_started_at=_RECEIVED - timedelta(milliseconds=50),
        response_received_at=_RECEIVED,
        elapsed=timedelta(milliseconds=50),
    )
    state = _State()
    service, _ = _service(
        state,
        response=response,
        clock_values=(_STARTED, _FINISHED),
    )

    result = service.ingest(symbol="فملی", request_id="malformed-preview")

    expected_hash = response_payload_sha256(body)
    batch = state.store.batches[result.ingestion_batch_id]
    preview = batch.metadata["body_diagnostic_preview"]
    assert result.status is IngestionBatchStatus.QUARANTINED
    assert result.payload_sha256 == expected_hash
    assert batch.payload_sha256 == expected_hash
    assert isinstance(preview, str)
    assert len(preview) <= 512
    assert api_key not in preview
    assert "***" in preview


def test_transport_error_finalizes_failed_without_fake_response_or_raw_transaction() -> None:
    state = _State()
    service, _ = _service(
        state,
        error=BrsApiTransportError("offline"),
        clock_values=(_STARTED, _FINISHED),
    )

    result = service.ingest(symbol="فملی", request_id="transport-request")

    assert result.status is IngestionBatchStatus.FAILED
    assert result.payload_sha256 is None
    assert result.received_count == 0
    assert state.store.raw_events == {}
    assert state.client_calls == 1
    assert "record_acquisition:2" not in state.events
    assert state.next_uow_id == 3


def test_http_error_commits_exact_body_hash_before_failed_finalization() -> None:
    state = _State()
    response = _response("candlestick_type2_provider_error.json")
    service, _ = _service(
        state,
        error=BrsApiHttpError(503, response=response),
        clock_values=(_STARTED, _FINISHED),
    )

    result = service.ingest(symbol="فملی", request_id="http-request")

    assert result.status is IngestionBatchStatus.FAILED
    assert result.payload_sha256 == response_payload_sha256(response.body_bytes)
    assert state.events.index("commit:2") < state.events.index("uow_enter:3")


@pytest.mark.parametrize("failure_mode", ["insert", "commit"])
def test_transaction_b_failure_is_terminalized_in_a_separate_transaction(
    failure_mode: str,
) -> None:
    state = _State()
    if failure_mode == "insert":
        state.raise_on_raw_insert = RuntimeError("injected raw insert failure")
    else:
        state.fail_commit_uow_ids.add(2)
    service, _ = _service(state, clock_values=(_STARTED, _FINISHED))
    bind_log_context(request_id="stale-outer-context")

    try:
        with pytest.raises(DailyBarIngestionError) as captured:
            service.ingest(symbol="فملی", request_id=f"transaction-b-{failure_mode}")

        assert isinstance(captured.value.__cause__, RuntimeError)
        batch = next(iter(state.store.batches.values()))
        assert batch.status is IngestionBatchStatus.FAILED
        assert batch.error_summary is not None
        assert "INGESTION_FAILED" in batch.error_summary
        assert batch.metadata["failure_code"] == "INGESTION_FAILED"
        assert state.store.raw_events == {}
        assert state.events.index("rollback:2") < state.events.index("uow_enter:3")
        assert state.events.index("finalize:FAILED:3") < state.events.index("commit:3")
        assert state.next_uow_id == 4
        assert get_log_context() == {}
    finally:
        clear_log_context()


def test_terminal_request_id_replays_without_commit_or_second_fetch() -> None:
    state = _State()
    existing = _terminal_batch(status=IngestionBatchStatus.PARTIAL)
    state.store.batches[existing.ingestion_batch_id] = existing
    state.store.next_batch_id = 21
    service, _ = _service(state, clock_values=(_STARTED,))

    result = service.ingest(symbol="فملی", request_id="existing-request")

    assert result.ingestion_batch_id == 20
    assert result.status is IngestionBatchStatus.PARTIAL
    assert result.idempotent_replay is True
    assert (result.received_count, result.accepted_count, result.rejected_count) == (3, 2, 1)
    assert (result.raw_inserted_count, result.bar_inserted_count, result.bar_unchanged_count) == (
        3,
        1,
        1,
    )
    assert state.client_calls == 0
    assert "commit:1" not in state.events


@pytest.mark.parametrize(
    ("status", "stored_symbol", "requested_symbol", "message"),
    [
        (IngestionBatchStatus.RUNNING, "فملی", "فملی", "RUNNING"),
        (IngestionBatchStatus.SUCCEEDED, "فملی", "فولاد", "different normalized symbol"),
    ],
)
def test_request_id_conflicts_do_not_fetch(
    status: IngestionBatchStatus,
    stored_symbol: str,
    requested_symbol: str,
    message: str,
) -> None:
    state = _State()
    existing = _terminal_batch(status=status, symbol=stored_symbol)
    state.store.batches[existing.ingestion_batch_id] = existing
    service, _ = _service(state, clock_values=(_STARTED,))

    with pytest.raises(RequestIdConflictError, match=message):
        service.ingest(symbol=requested_symbol, request_id="existing-request")

    assert state.client_calls == 0
    assert get_log_context() == {}


def test_raw_acquisition_survives_transaction_c_failure_and_context_is_cleared() -> None:
    state = _State(
        write_statuses=[BarRevisionWriteStatus.INSERTED],
        raise_on_append=RuntimeError("deterministic writer failure"),
    )
    service, _ = _service(
        state,
        clock_values=(_STARTED, _SYSTEM_TIMES[0], _FINISHED),
    )
    bind_log_context(request_id="stale-outer-context")

    with pytest.raises(DailyBarCanonicalizationError) as captured:
        service.ingest(symbol="فملی", request_id="canonical-failure")

    assert isinstance(captured.value.__cause__, RuntimeError)
    assert len(state.store.raw_events) == 3
    assert all(
        event.validation_status is RawEventValidationStatus.PENDING
        for event in state.store.raw_events.values()
    )
    batch = next(iter(state.store.batches.values()))
    assert batch.status is IngestionBatchStatus.FAILED
    assert batch.metadata["inserted_raw_count"] == 3
    assert state.store.bar_candidates == []
    assert state.events.index("commit:2") < state.events.index("rollback:3")
    assert state.events.index("rollback:3") < state.events.index("commit:4")
    assert get_log_context() == {}
    clear_log_context()


def test_empty_symbol_clears_preexisting_log_context_before_validation() -> None:
    state = _State()
    service, _ = _service(state)
    bind_log_context(request_id="stale-request", symbol="stale-symbol")

    try:
        with pytest.raises(ValueError, match="symbol must not be empty"):
            service.ingest(symbol=" \t\n ", request_id="never-started")

        assert state.client_calls == 0
        assert state.next_uow_id == 1
        assert get_log_context() == {}
    finally:
        clear_log_context()


def test_failing_id_generator_clears_preexisting_log_context() -> None:
    def fail_id_generation() -> str:
        raise RuntimeError("injected request-id generation failure")

    state = _State()
    service, _ = _service(state, id_generator=fail_id_generation)
    bind_log_context(request_id="stale-request", symbol="stale-symbol")

    try:
        with pytest.raises(RuntimeError, match="request-id generation failure"):
            service.ingest(symbol="فملی")

        assert state.client_calls == 0
        assert state.next_uow_id == 1
        assert get_log_context() == {}
    finally:
        clear_log_context()
