"""Transactional Point-in-Time snapshot construction over revision history."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sqlalchemy.engine import Engine

from bisfin.db.transaction import TransactionManager
from bisfin.repositories.snapshot_bar_repository import SqlAlchemySnapshotBarRepository
from bisfin.repositories.snapshot_repository import SnapshotRecord, SqlAlchemySnapshotRepository
from bisfin.snapshots.artifacts import SnapshotArtifactWriter, WrittenArtifact
from bisfin.snapshots.contracts import (
    SnapshotBuildResult,
    SnapshotComponentResult,
    SnapshotStatus,
)
from bisfin.snapshots.errors import (
    SnapshotBuildError,
    SnapshotConflictError,
    SnapshotInProgressError,
)
from bisfin.snapshots.manifest import SnapshotManifestDocument

_LOGGER = logging.getLogger(__name__)
_BUILDER_VERSION = "pr-07"


class SnapshotBuilder:
    """Build artifacts under one read snapshot, then atomically freeze their DB record.

    A frozen snapshot is a global reproducibility boundary, not permission to
    use every frozen row at every historical decision timestamp.
    """

    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] | None = None) -> None:
        self._transactions = TransactionManager(engine)
        self._clock = clock or (lambda: datetime.now(UTC))

    def build(
        self, document: SnapshotManifestDocument, *, output_dir: str | Path
    ) -> SnapshotBuildResult:
        request = document.request
        metadata = self._building_metadata(document)
        with self._transactions.begin() as connection:
            snapshots = SqlAlchemySnapshotRepository(connection)
            existing = snapshots.get_by_code(request.snapshot_code)
            if existing is not None:
                return self._resolve_existing(existing, document)
            created = snapshots.create_building(
                snapshot_code=request.snapshot_code,
                knowledge_cutoff_ts=request.knowledge_cutoff_ts,
                availability_mode=request.availability_mode,
                metadata=metadata,
            )

        _LOGGER.info("snapshot_build_started", extra={"snapshot_code": request.snapshot_code})
        writer = SnapshotArtifactWriter(output_dir, request.snapshot_code)
        published = False
        try:
            components = self._build_components(document, writer)
            manifest = self._write_manifest(document, writer, components)
            final_directory = writer.publish()
            published = True
            _LOGGER.info(
                "snapshot_artifacts_published", extra={"snapshot_code": request.snapshot_code}
            )
            final_components = tuple(
                component.model_copy(
                    update={
                        "storage_uri": (final_directory / component.relative_storage_path).as_uri()
                    }
                )
                for component in components
            )
            completed_metadata = {
                **metadata,
                "components": [item.model_dump(mode="json") for item in final_components],
            }
            with self._transactions.begin() as connection:
                snapshots = SqlAlchemySnapshotRepository(connection)
                snapshots.insert_components(created.data_snapshot_id, final_components)
                frozen = snapshots.freeze(
                    created.data_snapshot_id,
                    manifest_sha256=manifest.sha256,
                    frozen_at=self._clock(),
                    metadata=completed_metadata,
                )
            _LOGGER.info("snapshot_frozen", extra={"snapshot_code": request.snapshot_code})
            return self._result(frozen, final_components)
        except BaseException as error:
            if not published:
                writer.cleanup_incomplete()
            self._mark_failed(created.data_snapshot_id, metadata, error)
            _LOGGER.exception("snapshot_failed", extra={"snapshot_code": request.snapshot_code})
            if isinstance(error, SnapshotBuildError):
                raise
            raise SnapshotBuildError("Snapshot build failed.") from error

    def _build_components(
        self,
        document: SnapshotManifestDocument,
        writer: SnapshotArtifactWriter,
    ) -> tuple[SnapshotComponentResult, ...]:
        request = document.request
        components: list[SnapshotComponentResult] = []
        with self._transactions.begin(
            isolation_level="REPEATABLE READ", read_only=True
        ) as connection:
            bars = SqlAlchemySnapshotBarRepository(connection)
            for ordinal, spec in enumerate(
                sorted(request.components, key=lambda item: item.component_key), start=1
            ):
                feed_id = bars.resolve_series(
                    spec.bar_series_id, knowledge_cutoff_ts=request.knowledge_cutoff_ts
                )
                rows = bars.eligible_revisions(
                    bar_series_id=spec.bar_series_id,
                    event_from=spec.event_from,
                    event_to=spec.event_to,
                    knowledge_cutoff_ts=request.knowledge_cutoff_ts,
                    availability_mode=request.availability_mode,
                )
                if not rows and not spec.allow_empty:
                    raise SnapshotBuildError(
                        f"Component {spec.component_key!r} has no eligible final revisions."
                    )
                from bisfin.snapshots.serialization import canonical_jsonl_bytes

                artifact = writer.write_component(
                    ordinal, spec.component_key, canonical_jsonl_bytes(rows)
                )
                available_values = [
                    value for row in rows if isinstance((value := row["available_at"]), datetime)
                ]
                system_available_values = [
                    value
                    for row in rows
                    if isinstance((value := row["system_available_at"]), datetime)
                ]
                components.append(
                    SnapshotComponentResult(
                        component_key=spec.component_key,
                        kind=spec.kind,
                        bar_series_id=spec.bar_series_id,
                        feed_id=feed_id,
                        event_from=spec.event_from,
                        event_to=spec.event_to,
                        row_count=len(rows),
                        component_sha256=artifact.sha256,
                        storage_uri=(writer.final_directory / artifact.relative_path).as_uri(),
                        relative_storage_path=artifact.relative_path.as_posix(),
                        max_available_at=max(available_values, default=None),
                        max_system_available_at=max(system_available_values, default=None),
                    )
                )
                _LOGGER.info(
                    "snapshot_component_built",
                    extra={
                        "snapshot_code": request.snapshot_code,
                        "component_key": spec.component_key,
                    },
                )
        return tuple(components)

    def _write_manifest(
        self,
        document: SnapshotManifestDocument,
        writer: SnapshotArtifactWriter,
        components: tuple[SnapshotComponentResult, ...],
    ) -> WrittenArtifact:
        request = document.request
        manifest_components: list[dict[str, object]] = []
        for component in components:
            payload = component.model_dump(mode="json")
            payload.pop("storage_uri")
            manifest_components.append(payload)
        return writer.write_manifest(
            {
                "artifact_schema_version": 1,
                "snapshot_code": request.snapshot_code,
                "knowledge_cutoff_ts": request.knowledge_cutoff_ts,
                "availability_mode": request.availability_mode.value,
                "specification_sha256": document.specification_sha256,
                "components": manifest_components,
            }
        )

    def _resolve_existing(
        self,
        existing: SnapshotRecord,
        document: SnapshotManifestDocument,
    ) -> SnapshotBuildResult:
        stored_specification = existing.metadata.get("specification_sha256")
        if existing.status is SnapshotStatus.FROZEN:
            if stored_specification != document.specification_sha256:
                raise SnapshotConflictError(
                    "Snapshot code is already frozen for another specification."
                )
            raw_components = existing.metadata.get("components", [])
            if not isinstance(raw_components, list):
                raw_components = []
            components = tuple(
                SnapshotComponentResult.model_validate(item)
                for item in cast(list[object], raw_components)
                if isinstance(item, dict)
            )
            return self._result(existing, components, idempotent_replay=True)
        if existing.status is SnapshotStatus.BUILDING:
            raise SnapshotInProgressError("Snapshot code is already BUILDING.")
        raise SnapshotConflictError(f"Snapshot code is already {existing.status.value}.")

    def _mark_failed(
        self,
        data_snapshot_id: int,
        metadata: dict[str, object],
        error: BaseException,
    ) -> None:
        failure_metadata = {
            **metadata,
            "failure": {"code": type(error).__name__},
        }
        try:
            with self._transactions.begin() as connection:
                SqlAlchemySnapshotRepository(connection).mark_failed(
                    data_snapshot_id, metadata=failure_metadata
                )
        except BaseException:
            _LOGGER.exception("snapshot_failed_finalization")

    @staticmethod
    def _building_metadata(document: SnapshotManifestDocument) -> dict[str, object]:
        return {
            "source_manifest_sha256": document.source_manifest_sha256,
            "specification_sha256": document.specification_sha256,
            "builder_version": _BUILDER_VERSION,
            "requested_component_count": len(document.request.components),
            "component_specs": [
                item.model_dump(mode="json") for item in document.request.components
            ],
        }

    @staticmethod
    def _result(
        record: SnapshotRecord,
        components: tuple[SnapshotComponentResult, ...],
        *,
        idempotent_replay: bool = False,
    ) -> SnapshotBuildResult:
        source_hash = record.metadata.get("source_manifest_sha256")
        specification_hash = record.metadata.get("specification_sha256")
        if not isinstance(source_hash, str) or not isinstance(specification_hash, str):
            raise SnapshotBuildError("Snapshot metadata is missing reproducibility hashes.")
        return SnapshotBuildResult(
            data_snapshot_id=record.data_snapshot_id,
            snapshot_code=record.snapshot_code,
            status=record.status,
            knowledge_cutoff_ts=record.knowledge_cutoff_ts,
            availability_mode=record.availability_mode,
            source_manifest_sha256=source_hash,
            specification_sha256=specification_hash,
            manifest_sha256=record.manifest_sha256,
            components=components,
            created_at=record.created_at,
            frozen_at=record.frozen_at,
            idempotent_replay=idempotent_replay,
        )


__all__ = ["SnapshotBuilder"]
