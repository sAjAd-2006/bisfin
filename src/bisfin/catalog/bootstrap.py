"""Manifest-authoritative catalog bootstrap orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import TracebackType
from typing import Protocol, Self
from uuid import uuid4

from bisfin.catalog.errors import CatalogConflictError
from bisfin.catalog.manifest import CatalogManifestDocument, InstrumentDefinition
from bisfin.catalog.results import CatalogBootstrapResult
from bisfin.db.errors import BisfinError, redact_secrets
from bisfin.domain.catalog import DataFeed, Provider, Venue
from bisfin.domain.ingestion import IngestionBatch, IngestionBatchStatus, RawEventValidationStatus
from bisfin.integrations.brsapi import (
    BrsApiError,
    BrsApiHttpError,
    BrsApiRawResponse,
    BrsApiSymbolClient,
    BrsApiSymbolMetadata,
    parse_symbol_metadata,
)
from bisfin.logging import clear_log_context
from bisfin.repositories.catalog_writer_repository import SqlAlchemyCatalogWriterRepository
from bisfin.repositories.protocols import IngestionBatchRepository, RawEventRepository


class CatalogValidationMode(StrEnum):
    MANIFEST_ONLY = "manifest-only"
    FIXTURE_VALIDATE = "fixture-validate"
    LIVE_VALIDATE = "live-validate"


class SymbolValidationError(BisfinError):
    code = "SYMBOL_VALIDATION_ERROR"


class SymbolProviderMismatchError(SymbolValidationError):
    code = "SYMBOL_PROVIDER_MISMATCH"


class CatalogBootstrapUnitOfWork(Protocol):
    @property
    def catalog_writer(self) -> SqlAlchemyCatalogWriterRepository: ...

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


type CatalogUnitOfWorkFactory = Callable[[], CatalogBootstrapUnitOfWork]
type Clock = Callable[[], datetime]
type IdGenerator = Callable[[], str]


@dataclass(frozen=True, slots=True)
class _SymbolEvidence:
    instrument: InstrumentDefinition
    response: BrsApiRawResponse | None
    metadata: BrsApiSymbolMetadata | None
    error: SymbolValidationError | None


class CatalogBootstrapService:
    """Apply one fully validated manifest without database I/O during validation."""

    def __init__(
        self,
        *,
        unit_of_work_factory: CatalogUnitOfWorkFactory,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
    ) -> None:
        self._uow_factory = unit_of_work_factory
        self._clock = clock or _utc_now
        self._id_generator = id_generator or (lambda: str(uuid4()))

    def bootstrap(
        self,
        document: CatalogManifestDocument,
        *,
        validation_mode: CatalogValidationMode = CatalogValidationMode.MANIFEST_ONLY,
        symbol_client: BrsApiSymbolClient | None = None,
        request_id: str | None = None,
    ) -> CatalogBootstrapResult:
        """A=batch, external acquisition, B=raw audit, C=atomic canonical writes."""

        clear_log_context()
        manifest = document.manifest
        started_at = self._now()
        effective_request_id = (request_id or "").strip() or self._id_generator()
        try:
            with self._uow_factory() as unit_of_work:
                writer = unit_of_work.catalog_writer
                audit_feeds = self._ensure_audit_references(writer, document)
                catalog_feed = audit_feeds["BISFIN_CATALOG_MANIFEST"]
                symbol_feed = audit_feeds["TSETMC_SYMBOL_METADATA"]
                start = unit_of_work.ingestion_batches.create_batch_if_absent(
                    feed_id=catalog_feed.feed_id,
                    parser_version="catalog-bootstrap-v1",
                    request_id=effective_request_id,
                    metadata={
                        "manifest_id": manifest.manifest_id,
                        "validation_mode": validation_mode.value,
                        "manifest_sha256": document.payload_sha256,
                        "requested_instrument_count": len(manifest.instruments),
                    },
                    started_at=started_at,
                )
                unit_of_work.commit()
            if not start.created:
                if start.batch.status is IngestionBatchStatus.RUNNING:
                    raise CatalogConflictError("catalog request_id is already running")
                return self._replay_result(
                    start.batch, manifest_id=manifest.manifest_id, validation_mode=validation_mode
                )
            batch = start.batch
            try:
                evidence = self._validate_symbols(document, validation_mode, symbol_client)
            except SymbolValidationError as error:
                self._finalize_failure(
                    batch,
                    len(manifest.instruments),
                    error,
                    IngestionBatchStatus.QUARANTINED,
                )
                raise
            with self._uow_factory() as unit_of_work:
                self._record_manifest_events(
                    unit_of_work, batch, catalog_feed, document, started_at
                )
                self._record_symbol_events(unit_of_work, batch, symbol_feed, evidence)
                unit_of_work.commit()
            provider_failure = next(
                (item.error for item in evidence if item.error is not None),
                None,
            )
            if provider_failure is not None:
                self._finalize_failure(
                    batch,
                    len(manifest.instruments),
                    provider_failure,
                    IngestionBatchStatus.QUARANTINED,
                )
                raise provider_failure
            try:
                with self._uow_factory() as unit_of_work:
                    providers, counts = self._ensure_references(
                        unit_of_work.catalog_writer, document
                    )
                    result = self._apply_instruments(
                        unit_of_work.catalog_writer, document, providers
                    )
                    finished_at = self._now()
                    unit_of_work.ingestion_batches.finalize_batch(
                        batch.ingestion_batch_id,
                        status=IngestionBatchStatus.SUCCEEDED,
                        received_row_count=len(manifest.instruments),
                        accepted_row_count=len(manifest.instruments),
                        rejected_row_count=0,
                        payload_sha256=document.payload_sha256,
                        metadata={
                            "manifest_id": manifest.manifest_id,
                            "validation_mode": validation_mode.value,
                        },
                        finished_at=finished_at,
                    )
                    unit_of_work.commit()
            except CatalogConflictError as error:
                self._finalize_failure(
                    batch, len(manifest.instruments), error, IngestionBatchStatus.FAILED
                )
                raise
            return CatalogBootstrapResult(
                batch_id=batch.ingestion_batch_id,
                manifest_id=manifest.manifest_id,
                status=IngestionBatchStatus.SUCCEEDED,
                started_at=started_at,
                finished_at=finished_at,
                payload_sha256=document.payload_sha256,
                validation_mode=validation_mode.value,
                providers_created=counts["providers_created"],
                feeds_created=counts["feeds_created"],
                venues_created=counts["venues_created"],
                timeframes_created=counts["timeframes_created"],
                currencies_created=counts["currencies_created"],
                asset_types_created=counts["asset_types_created"],
                instruments_created=result["instruments_created"],
                instruments_unchanged=result["instruments_unchanged"],
                identifiers_created=result["identifiers_created"],
                identifiers_closed=result["identifiers_closed"],
                spec_versions_created=result["spec_versions_created"],
                entries_rejected=0,
            )
        finally:
            clear_log_context()

    def _validate_symbols(
        self,
        document: CatalogManifestDocument,
        mode: CatalogValidationMode,
        client: BrsApiSymbolClient | None,
    ) -> tuple[_SymbolEvidence, ...]:
        if mode is CatalogValidationMode.MANIFEST_ONLY:
            return ()
        if client is None:
            raise SymbolValidationError("symbol validation requires an explicit client")
        evidence: list[_SymbolEvidence] = []
        for item in document.manifest.instruments:
            response: BrsApiRawResponse | None = None
            try:
                response = client.fetch_symbol_metadata(item.provider_symbol)
                metadata = parse_symbol_metadata(response)
                error: SymbolValidationError | None = None
                if metadata.normalized_symbol != item.provider_symbol:
                    error = SymbolProviderMismatchError(f"symbol mismatch for {item.stable_key}")
                elif metadata.isin != item.isin:
                    error = SymbolProviderMismatchError(f"ISIN mismatch for {item.stable_key}")
                elif (
                    document.manifest.resolve_provider_market(item.provider_code, metadata.market)
                    != item.venue_code
                ):
                    error = SymbolProviderMismatchError(f"market mismatch for {item.stable_key}")
                evidence.append(_SymbolEvidence(item, response, metadata, error))
            except (BisfinError, BrsApiError) as error:
                if response is None and isinstance(error, BrsApiHttpError):
                    response = error.response
                evidence.append(
                    _SymbolEvidence(
                        item, response, None, SymbolValidationError(redact_secrets(error))
                    )
                )
        return tuple(evidence)

    def _ensure_references(
        self,
        writer: SqlAlchemyCatalogWriterRepository,
        document: CatalogManifestDocument,
    ) -> tuple[dict[str, Provider], dict[str, int]]:
        manifest = document.manifest
        counts = {
            "providers_created": 0,
            "feeds_created": 0,
            "venues_created": 0,
            "timeframes_created": 0,
            "currencies_created": 0,
            "asset_types_created": 0,
        }
        for currency_definition in manifest.currencies:
            _, outcome = writer.ensure_currency(currency_definition)
            counts["currencies_created"] += int(outcome.created)
        for asset_type_definition in manifest.asset_types:
            _, outcome = writer.ensure_asset_type(asset_type_definition)
            counts["asset_types_created"] += int(outcome.created)
        providers: dict[str, Provider] = {}
        for provider_definition in manifest.providers:
            value, outcome = writer.ensure_provider(provider_definition)
            providers[provider_definition.provider_code] = value
            counts["providers_created"] += int(outcome.created)
        for feed_definition in manifest.feeds:
            _, outcome = writer.ensure_feed(
                providers[feed_definition.provider_code].provider_id,
                feed_definition,
            )
            counts["feeds_created"] += int(outcome.created)
        for venue_definition in manifest.venues:
            _, outcome = writer.ensure_venue(venue_definition)
            counts["venues_created"] += int(outcome.created)
        for timeframe_definition in manifest.timeframes:
            _, outcome = writer.ensure_timeframe(timeframe_definition)
            counts["timeframes_created"] += int(outcome.created)
        return providers, counts

    @staticmethod
    def _ensure_audit_references(
        writer: SqlAlchemyCatalogWriterRepository,
        document: CatalogManifestDocument,
    ) -> dict[str, DataFeed]:
        """Create only the feeds needed to persist audit evidence before validation.

        All other catalog reference rows remain in transaction C with instrument writes.
        """

        required_codes = {"BISFIN_CATALOG_MANIFEST", "TSETMC_SYMBOL_METADATA"}
        definitions = {
            item.feed_code: item
            for item in document.manifest.feeds
            if item.feed_code in required_codes
        }
        if definitions.keys() != required_codes:
            raise CatalogConflictError("catalog manifest lacks required audit feeds")
        provider_definitions = {item.provider_code: item for item in document.manifest.providers}
        feeds: dict[str, DataFeed] = {}
        for feed_code, definition in definitions.items():
            provider_definition = provider_definitions.get(definition.provider_code)
            if provider_definition is None:
                raise CatalogConflictError("catalog manifest lacks an audit-feed provider")
            provider, _ = writer.ensure_provider(provider_definition)
            feed, _ = writer.ensure_feed(provider.provider_id, definition)
            feeds[feed_code] = feed
        return feeds

    @staticmethod
    def _feed_for(
        providers: dict[str, Provider],
        writer: SqlAlchemyCatalogWriterRepository,
        document: CatalogManifestDocument,
        feed_code: str,
    ) -> DataFeed:
        definition = next(item for item in document.manifest.feeds if item.feed_code == feed_code)
        feed, _ = writer.ensure_feed(providers[definition.provider_code].provider_id, definition)
        return feed

    def _record_manifest_events(
        self,
        unit_of_work: CatalogBootstrapUnitOfWork,
        batch: IngestionBatch,
        feed: DataFeed,
        document: CatalogManifestDocument,
        ingested_at: datetime,
    ) -> None:
        unit_of_work.raw_events.ensure_month_partition(ingested_at)
        for item in document.manifest.instruments:
            raw = item.model_dump(mode="json")
            raw["manifest_id"] = document.manifest.manifest_id
            unit_of_work.raw_events.insert_response_record(
                ingested_at=ingested_at,
                ingestion_batch_id=batch.ingestion_batch_id,
                feed_id=feed.feed_id,
                payload_sha256=_sha256_json(raw),
                raw_payload=raw,
                source_record_key=(
                    f"bisfin|catalog-manifest|{document.manifest.manifest_id}|"
                    f"instrument|{item.stable_key}"
                ),
                validation_status=RawEventValidationStatus.ACCEPTED,
            )

    def _record_symbol_events(
        self,
        unit_of_work: CatalogBootstrapUnitOfWork,
        batch: IngestionBatch,
        feed: DataFeed,
        evidence: tuple[_SymbolEvidence, ...],
    ) -> None:
        for item in evidence:
            if item.response is None:
                continue
            response = item.response
            received_at = response.response_received_at
            unit_of_work.raw_events.ensure_month_partition(received_at)
            raw_payload: dict[str, object]
            if item.metadata is None:
                raw_payload = {
                    "response_sha256": hashlib.sha256(response.body_bytes).hexdigest(),
                    "response_bytes_hex": response.body_bytes.hex(),
                    "parse_error": redact_secrets(item.error or "provider response rejected"),
                }
            else:
                raw_payload = dict(item.metadata.raw_payload)
            unit_of_work.raw_events.insert_response_record(
                ingested_at=received_at,
                ingestion_batch_id=batch.ingestion_batch_id,
                feed_id=feed.feed_id,
                payload_sha256=hashlib.sha256(response.body_bytes).hexdigest(),
                raw_payload=raw_payload,
                source_record_key=(
                    f"brsapi|symbol|{item.instrument.provider_symbol}|{received_at.isoformat()}"
                ),
                observed_at=received_at,
                validation_status=(
                    RawEventValidationStatus.REJECTED
                    if item.error
                    else RawEventValidationStatus.ACCEPTED
                ),
                validation_errors=(
                    [{"code": item.error.code, "message": redact_secrets(item.error)}]
                    if item.error
                    else ()
                ),
            )

    @staticmethod
    def _apply_instruments(
        writer: SqlAlchemyCatalogWriterRepository,
        document: CatalogManifestDocument,
        providers: dict[str, Provider],
    ) -> dict[str, int]:
        venues: dict[str, Venue] = {}
        for venue_definition in document.manifest.venues:
            venue_value, _ = writer.ensure_venue(venue_definition)
            venues[venue_definition.venue_code] = venue_value
        result = {
            "instruments_created": 0,
            "instruments_unchanged": 0,
            "identifiers_created": 0,
            "identifiers_closed": 0,
            "spec_versions_created": 0,
            "entries_rejected": 0,
        }
        for instrument_definition in document.manifest.instruments:
            outcome = writer.apply_instrument(
                instrument_definition,
                provider_id=providers[instrument_definition.provider_code].provider_id,
                venue_id=venues[instrument_definition.venue_code].venue_id,
            )
            result["instruments_created"] += int(outcome.instrument_created)
            result["instruments_unchanged"] += int(outcome.unchanged)
            result["identifiers_created"] += outcome.identifier_created
            result["identifiers_closed"] += outcome.identifier_closed
            result["spec_versions_created"] += outcome.specification_created
        return result

    def _finalize_failure(
        self,
        batch: IngestionBatch,
        received_row_count: int,
        error: BisfinError,
        status: IngestionBatchStatus,
    ) -> None:
        """Transaction D: terminal state never shares the rolled-back write transaction."""

        with self._uow_factory() as unit_of_work:
            unit_of_work.ingestion_batches.finalize_batch(
                batch.ingestion_batch_id,
                status=status,
                received_row_count=received_row_count,
                accepted_row_count=0,
                rejected_row_count=received_row_count,
                error_summary=redact_secrets(error),
                metadata={"failure_code": getattr(error, "code", "CATALOG_FAILURE")},
                finished_at=self._now(),
            )
            unit_of_work.commit()

    @staticmethod
    def _replay_result(
        batch: IngestionBatch,
        *,
        manifest_id: str,
        validation_mode: CatalogValidationMode,
    ) -> CatalogBootstrapResult:
        finished_at = batch.finished_at or batch.started_at
        return CatalogBootstrapResult(
            batch_id=batch.ingestion_batch_id,
            manifest_id=manifest_id,
            status=batch.status,
            started_at=batch.started_at,
            finished_at=finished_at,
            payload_sha256=batch.payload_sha256 or "",
            validation_mode=validation_mode.value,
            idempotent_replay=True,
            entries_rejected=batch.rejected_row_count,
        )

    def _now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _sha256_json(value: object) -> str:
    from bisfin.repositories.raw_event_repository import canonical_json_text

    return hashlib.sha256(canonical_json_text(value).encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


__all__ = [
    "CatalogBootstrapService",
    "CatalogValidationMode",
    "SymbolProviderMismatchError",
    "SymbolValidationError",
]
