"""Domain-oriented repository contracts without persistence implementation details."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from bisfin.domain.catalog import (
    DataFeed,
    Instrument,
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
    BarRevisionCandidate,
    BarRevisionWriteResult,
    BarSeries,
    PointInTimeBar,
    ReplayMode,
)


@runtime_checkable
class DataFeedRepository(Protocol):
    """Required, pre-provisioned provider/feed/calendar reference data."""

    def get_provider_by_code(self, provider_code: str) -> Provider: ...

    def get_feed_by_code(self, provider_id: int, feed_code: str) -> DataFeed: ...

    def get_timeframe_by_code(self, timeframe_code: str) -> Timeframe: ...

    def get_regular_trading_session(
        self,
        venue_id: int,
        trading_date: date,
    ) -> TradingSession: ...


@runtime_checkable
class InstrumentRepository(Protocol):
    """Historical catalog reads needed by ingestion workflows."""

    def get_by_id(self, instrument_id: int) -> Instrument | None: ...

    def identifier_exists(
        self,
        provider_id: int,
        identifier_type: str,
        identifier_value: str,
    ) -> bool: ...

    def find_by_identifier(
        self,
        provider_id: int,
        identifier_type: str,
        identifier_value: str,
        as_of: datetime,
    ) -> ResolvedInstrument | None: ...

    def get_active_spec(
        self, instrument_id: int, as_of: datetime
    ) -> InstrumentSpecification | None: ...

    def find_by_identifier_for_regular_session(
        self,
        provider_id: int,
        identifier_type: str,
        identifier_value: str,
        trading_date: date,
    ) -> SessionResolvedInstrument | None: ...


@runtime_checkable
class IngestionBatchRepository(Protocol):
    """Explicit ingestion-batch lifecycle; methods never commit transactions."""

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
    ) -> IngestionBatch: ...

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
    ) -> IngestionBatchStartResult: ...

    def get_by_id(self, ingestion_batch_id: int) -> IngestionBatch | None: ...

    def get_by_request_id(self, feed_id: int, request_id: str) -> IngestionBatch | None: ...

    def mark_running(self, ingestion_batch_id: int) -> IngestionBatch: ...

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
    ) -> IngestionBatch: ...

    def mark_failed(
        self,
        ingestion_batch_id: int,
        *,
        error_code: str,
        error_message: str,
        details: Mapping[str, Any] | None = None,
        finished_at: datetime | None = None,
    ) -> IngestionBatch: ...

    def record_acquisition(
        self,
        ingestion_batch_id: int,
        *,
        payload_sha256: str,
        received_row_count: int,
        source_watermark: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> IngestionBatch: ...

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
    ) -> IngestionBatch: ...


@runtime_checkable
class RawEventRepository(Protocol):
    """Immutable provider rows plus mutable structured validation outcomes."""

    def ensure_month_partition(self, ingested_at: datetime) -> None: ...

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
    ) -> RawEvent: ...

    def update_validation_result(
        self,
        identity: RawEventIdentity,
        *,
        validation_status: RawEventValidationStatus,
        validation_errors: Sequence[object] = (),
    ) -> RawEvent: ...

    def find_identical_record(
        self,
        *,
        feed_id: int,
        source_record_key: str,
        payload_sha256: str,
    ) -> RawEvent | None: ...

    def list_by_batch(self, ingestion_batch_id: int) -> Sequence[RawEvent]: ...


@runtime_checkable
class BarRepository(Protocol):
    """Current series metadata and database-authoritative PIT bar access."""

    def get_series_by_id(self, bar_series_id: int) -> BarSeries | None: ...

    def get_bars_as_of(
        self,
        bar_series_id: int,
        from_ts: datetime,
        to_ts: datetime,
        knowledge_cutoff_ts: datetime,
        replay_mode: ReplayMode,
    ) -> Sequence[PointInTimeBar]: ...


@runtime_checkable
class BarWriterRepository(Protocol):
    """Concurrency-safe RAW daily series and append-only revision writes."""

    def ensure_month_partitions(self, trading_dates: Iterable[date]) -> None: ...

    def get_or_create_daily_raw_series(
        self,
        *,
        feed_id: int,
        instrument_id: int,
        timeframe_id: int,
        metadata: Mapping[str, Any] | None = None,
    ) -> BarSeries: ...

    def append_revision_if_changed(
        self,
        candidate: BarRevisionCandidate,
    ) -> BarRevisionWriteResult: ...
