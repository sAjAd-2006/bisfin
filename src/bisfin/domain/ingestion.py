"""DTOs for auditable ingestion batches and immutable raw events."""

from enum import StrEnum

from pydantic import Field

from bisfin.domain.common import AwareDateTime, ImmutableDTO, JsonObject


class IngestionBatchStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"


class IngestionBatch(ImmutableDTO):
    ingestion_batch_id: int
    feed_id: int
    request_id: str | None = None
    requested_event_from: AwareDateTime | None = None
    requested_event_to: AwareDateTime | None = None
    started_at: AwareDateTime
    finished_at: AwareDateTime | None = None
    status: IngestionBatchStatus
    received_row_count: int = 0
    accepted_row_count: int = 0
    rejected_row_count: int = 0
    payload_sha256: str | None = None
    parser_version: str
    source_watermark: str | None = None
    error_summary: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class RawEvent(ImmutableDTO):
    ingested_at: AwareDateTime
    raw_event_id: int
    ingestion_batch_id: int
    feed_id: int
    source_record_key: str | None = None
    source_event_time_text: str | None = None
    source_date_text: str | None = None
    source_sequence: int | None = None
    observed_at: AwareDateTime | None = None
    payload_sha256: str
    raw_payload: JsonObject
    validation_status: str
    validation_errors: list[object] = Field(default_factory=list)
