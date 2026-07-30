"""Domain-oriented repository contracts without persistence implementation details."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from bisfin.domain.catalog import Instrument, InstrumentSpecification, ResolvedInstrument
from bisfin.domain.ingestion import IngestionBatch
from bisfin.domain.market_data import BarSeries, PointInTimeBar, ReplayMode


@runtime_checkable
class InstrumentRepository(Protocol):
    """Historical catalog reads needed by ingestion workflows."""

    def get_by_id(self, instrument_id: int) -> Instrument | None: ...

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
    ) -> IngestionBatch: ...

    def get_by_id(self, ingestion_batch_id: int) -> IngestionBatch | None: ...

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
    ) -> IngestionBatch: ...

    def mark_failed(
        self,
        ingestion_batch_id: int,
        *,
        error_code: str,
        error_message: str,
        details: Mapping[str, Any] | None = None,
    ) -> IngestionBatch: ...


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
