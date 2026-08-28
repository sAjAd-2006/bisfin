"""Manifest-authoritative catalog bootstrap orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from types import TracebackType
from typing import Protocol, Self
from uuid import uuid4

from bisfin.catalog.errors import CatalogConflictError
from bisfin.catalog.manifest import CatalogManifestDocument, InstrumentDefinition
from bisfin.catalog.results import CatalogBootstrapResult
from bisfin.db.errors import BisfinError
from bisfin.domain.catalog import DataFeed, Provider, Venue
from bisfin.domain.ingestion import IngestionBatch, IngestionBatchStatus, RawEventValidationStatus
from bisfin.integrations.brsapi import (
    BrsApiRawResponse,
    BrsApiSymbolClient,
    BrsApiSymbolMetadata,
    parse_symbol_metadata,
)
from bisfin.logging import clear_log_context, log_context
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
        """Validate optional responses first, then execute one temporal transaction."""

        clear_log_context()
        try:
            evidence = self._validate_symbols(document, validation_mode, symbol_client)
            effective_request_id = (request_id or "").strip() or self._id_generator()
            started_at = self._now()
            manifest = document.manifest
            with log_context(
                request_id=effective_request_id,
                correlation_id=effective_request_id,
                manifest_id=manifest.manifest_id,
            ):
                with self._uow_factory() as unit_of_work:
                    writer = unit_of_work.catalog_writer
                    providers, counts = self._ensure_references(writer, document)
                    catalog_feed = self._feed_for(
                        providers, writer, document, "BISFIN_CATALOG_MANIFEST"
                    )
                    start = unit_of_work.ingestion_batches.create_batch_if_absent(
                        feed_id=catalog_feed.feed_id,
                        parser_version="catalog-bootstrap-v1",
                        request_id=effective_request_id,
                        metadata={
                            "manifest_id": manifest.manifest_id,
                            "validation_mode": validation_mode.value,
                        },
                        started_at=started_at,
                    )
                    if not start.created:
                        if start.batch.status is IngestionBatchStatus.RUNNING:
                            raise CatalogConflictError("catalog request_id is already running")
                        return self._replay_result(
                            start.batch,
                            manifest_id=manifest.manifest_id,
                            validation_mode=validation_mode,
                        )
                    batch = start.batch
                    with log_context(ingestion_batch_id=batch.ingestion_batch_id):
                        self._record_manifest_events(
                            unit_of_work, batch, catalog_feed, document, started_at
                        )
                        self._record_symbol_events(
                            unit_of_work,
                            batch,
                            writer,
                            providers,
                            document,
                            evidence,
                        )
                        result = self._apply_instruments(writer, document, providers)
                        finished_at = self._now()
                        status = (
                            IngestionBatchStatus.PARTIAL
                            if result["entries_rejected"]
                            else IngestionBatchStatus.SUCCEEDED
                        )
                        unit_of_work.ingestion_batches.finalize_batch(
                            batch.ingestion_batch_id,
                            status=status,
                            received_row_count=len(manifest.instruments),
                            accepted_row_count=(
                                len(manifest.instruments) - result["entries_rejected"]
                            ),
                            rejected_row_count=result["entries_rejected"],
                            payload_sha256=document.payload_sha256,
                            metadata={
                                "manifest_id": manifest.manifest_id,
                                "validation_mode": validation_mode.value,
                            },
                            finished_at=finished_at,
                        )
                        unit_of_work.commit()
            return CatalogBootstrapResult(
                batch_id=batch.ingestion_batch_id,
                manifest_id=manifest.manifest_id,
                status=status,
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
                entries_rejected=result["entries_rejected"],
            )
        finally:
            clear_log_context()

    def _validate_symbols(
        self,
        document: CatalogManifestDocument,
        mode: CatalogValidationMode,
        client: BrsApiSymbolClient | None,
    ) -> tuple[tuple[InstrumentDefinition, BrsApiSymbolMetadata, BrsApiRawResponse], ...]:
        if mode is CatalogValidationMode.MANIFEST_ONLY:
            return ()
        if client is None:
            raise SymbolValidationError("symbol validation requires an explicit client")
        evidence: list[tuple[InstrumentDefinition, BrsApiSymbolMetadata, BrsApiRawResponse]] = []
        for item in document.manifest.instruments:
            response = client.fetch_symbol_metadata(item.provider_symbol)
            metadata = parse_symbol_metadata(response)
            if metadata.normalized_symbol != item.provider_symbol:
                raise SymbolProviderMismatchError(f"symbol mismatch for {item.stable_key}")
            if metadata.isin != item.isin:
                raise SymbolProviderMismatchError(f"ISIN mismatch for {item.stable_key}")
            mapped_venue = document.manifest.resolve_provider_market(
                item.provider_code,
                metadata.market,
            )
            if mapped_venue != item.venue_code:
                raise SymbolProviderMismatchError(f"market mismatch for {item.stable_key}")
            evidence.append((item, metadata, response))
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
        writer: SqlAlchemyCatalogWriterRepository,
        providers: dict[str, Provider],
        document: CatalogManifestDocument,
        evidence: tuple[tuple[InstrumentDefinition, BrsApiSymbolMetadata, BrsApiRawResponse], ...],
    ) -> None:
        if not evidence:
            return
        feed = self._feed_for(providers, writer, document, "TSETMC_SYMBOL_METADATA")
        for _, metadata, response in evidence:
            received_at = response.response_received_at
            unit_of_work.raw_events.ensure_month_partition(received_at)
            unit_of_work.raw_events.insert_response_record(
                ingested_at=received_at,
                ingestion_batch_id=batch.ingestion_batch_id,
                feed_id=feed.feed_id,
                payload_sha256=metadata.response_sha256,
                raw_payload=metadata.raw_payload,
                source_record_key=(
                    f"brsapi|symbol|{metadata.normalized_symbol}|{received_at.isoformat()}"
                ),
                observed_at=received_at,
                validation_status=RawEventValidationStatus.ACCEPTED,
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
