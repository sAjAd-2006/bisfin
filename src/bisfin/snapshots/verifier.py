"""Read-only integrity and live-drift verification for frozen snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy.engine import Engine

from bisfin.db.transaction import TransactionManager
from bisfin.repositories.snapshot_bar_repository import SqlAlchemySnapshotBarRepository
from bisfin.repositories.snapshot_repository import SnapshotRecord, SqlAlchemySnapshotRepository
from bisfin.snapshots.contracts import (
    SnapshotComponentSpec,
    SnapshotStatus,
    SnapshotVerificationIssue,
    SnapshotVerificationResult,
)
from bisfin.snapshots.errors import SnapshotVerificationError
from bisfin.snapshots.serialization import canonical_datetime, canonical_jsonl_bytes


class SnapshotVerifier:
    """Verify frozen artifact bytes first, and optionally re-enumerate the live database."""

    def __init__(self, engine: Engine) -> None:
        self._transactions = TransactionManager(engine)

    def verify(self, snapshot_code: str, *, against_db: bool = False) -> SnapshotVerificationResult:
        with self._transactions.begin(read_only=True) as connection:
            snapshots = SqlAlchemySnapshotRepository(connection)
            snapshot = snapshots.get_by_code(snapshot_code)
            if snapshot is None or snapshot.status is not SnapshotStatus.FROZEN:
                raise SnapshotVerificationError("Only a FROZEN snapshot can be verified.")
            components = snapshots.list_components(snapshot.data_snapshot_id)
        issues = list(self._verify_artifacts(snapshot, components))
        if issues:
            return SnapshotVerificationResult(
                snapshot_code=snapshot_code,
                verified=False,
                artifact_verified=False,
                database_verified=None,
                issues=tuple(issues),
            )
        if not against_db:
            return SnapshotVerificationResult(
                snapshot_code=snapshot_code, verified=True, artifact_verified=True
            )
        drift_issues = self._verify_database(snapshot, components)
        return SnapshotVerificationResult(
            snapshot_code=snapshot_code,
            verified=not drift_issues,
            artifact_verified=True,
            database_verified=not drift_issues,
            database_drift=bool(drift_issues),
            issues=tuple(drift_issues),
        )

    def _verify_artifacts(
        self,
        snapshot: SnapshotRecord,
        components: tuple[dict[str, object], ...],
    ) -> tuple[SnapshotVerificationIssue, ...]:
        issues: list[SnapshotVerificationIssue] = []
        if snapshot.manifest_sha256 is None:
            return (
                SnapshotVerificationIssue(
                    code="MISSING_MANIFEST_HASH", message="Missing manifest hash."
                ),
            )
        manifest_path: Path | None = None
        for component in components:
            uri = component.get("storage_uri")
            if not isinstance(uri, str):
                issues.append(
                    SnapshotVerificationIssue(
                        code="MISSING_STORAGE_URI", message="Missing component URI."
                    )
                )
                continue
            path = _file_uri_path(uri)
            if path is None or not path.is_file():
                issues.append(
                    SnapshotVerificationIssue(
                        code="MISSING_COMPONENT", message="Component file is missing."
                    )
                )
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != component.get("component_sha256"):
                issues.append(
                    SnapshotVerificationIssue(
                        code="COMPONENT_HASH_MISMATCH",
                        component_key=str(component["component_key"]),
                        message="Component hash mismatch.",
                    )
                )
            if len(path.read_bytes().splitlines()) != component.get("row_count"):
                issues.append(
                    SnapshotVerificationIssue(
                        code="COMPONENT_ROW_COUNT_MISMATCH",
                        component_key=str(component["component_key"]),
                        message="Component row count mismatch.",
                    )
                )
            manifest_path = path.parent.parent / "manifest.json"
        if manifest_path is None or not manifest_path.is_file():
            issues.append(
                SnapshotVerificationIssue(
                    code="MISSING_MANIFEST", message="Manifest file is missing."
                )
            )
        else:
            manifest_bytes = manifest_path.read_bytes()
            if hashlib.sha256(manifest_bytes).hexdigest() != snapshot.manifest_sha256:
                issues.append(
                    SnapshotVerificationIssue(
                        code="MANIFEST_HASH_MISMATCH", message="Manifest hash mismatch."
                    )
                )
            else:
                issues.extend(self._verify_manifest_metadata(snapshot, components, manifest_bytes))
        return tuple(issues)

    @staticmethod
    def _verify_manifest_metadata(
        snapshot: SnapshotRecord,
        components: tuple[dict[str, object], ...],
        manifest_bytes: bytes,
    ) -> tuple[SnapshotVerificationIssue, ...]:
        """Confirm the artifact manifest describes the frozen database evidence."""

        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return (
                SnapshotVerificationIssue(
                    code="INVALID_MANIFEST", message="Manifest is not valid UTF-8 JSON."
                ),
            )
        if not isinstance(manifest, dict):
            return (
                SnapshotVerificationIssue(
                    code="INVALID_MANIFEST", message="Manifest root is not an object."
                ),
            )
        expected_components = snapshot.metadata.get("components")
        expected_specification = snapshot.metadata.get("specification_sha256")
        artifact_components = manifest.get("components")
        if not isinstance(artifact_components, list):
            return (
                SnapshotVerificationIssue(
                    code="INVALID_MANIFEST", message="Manifest components are not a list."
                ),
            )
        expected = {
            "snapshot_code": snapshot.snapshot_code,
            "knowledge_cutoff_ts": canonical_datetime(snapshot.knowledge_cutoff_ts),
            "availability_mode": snapshot.availability_mode.value,
            "specification_sha256": expected_specification,
            "components": [
                {key: value for key, value in item.items() if key != "storage_uri"}
                for item in expected_components
            ]
            if isinstance(expected_components, list)
            else None,
        }
        actual = {
            "snapshot_code": manifest.get("snapshot_code"),
            "knowledge_cutoff_ts": manifest.get("knowledge_cutoff_ts"),
            "availability_mode": manifest.get("availability_mode"),
            "specification_sha256": manifest.get("specification_sha256"),
            "components": artifact_components,
        }
        if actual != expected:
            return (
                SnapshotVerificationIssue(
                    code="MANIFEST_METADATA_MISMATCH",
                    message="Manifest metadata does not match the frozen database record.",
                ),
            )
        component_keys = {
            str(component.get("component_key"))
            for component in components
            if component.get("component_key") is not None
        }
        manifest_keys = {
            str(component.get("component_key"))
            for component in artifact_components
            if isinstance(component, dict) and component.get("component_key") is not None
        }
        if manifest_keys != component_keys:
            return (
                SnapshotVerificationIssue(
                    code="MANIFEST_COMPONENT_MISMATCH",
                    message="Manifest components do not match the frozen database components.",
                ),
            )
        return ()

    def _verify_database(
        self, snapshot: SnapshotRecord, components: tuple[dict[str, object], ...]
    ) -> list[SnapshotVerificationIssue]:
        raw_specs = snapshot.metadata.get("component_specs", [])
        if not isinstance(raw_specs, list):
            return [
                SnapshotVerificationIssue(
                    code="MISSING_SPECIFICATION", message="Missing component specs."
                )
            ]
        specs = {
            item.component_key: item
            for item in map(SnapshotComponentSpec.model_validate, raw_specs)
        }
        issues: list[SnapshotVerificationIssue] = []
        with self._transactions.begin(
            isolation_level="REPEATABLE READ", read_only=True
        ) as connection:
            bars = SqlAlchemySnapshotBarRepository(connection)
            for component in components:
                spec = specs.get(str(component["component_key"]))
                if spec is None:
                    issues.append(
                        SnapshotVerificationIssue(
                            code="MISSING_COMPONENT_SPEC", message="Component spec missing."
                        )
                    )
                    continue
                rows = bars.eligible_revisions(
                    bar_series_id=spec.bar_series_id,
                    event_from=spec.event_from,
                    event_to=spec.event_to,
                    knowledge_cutoff_ts=snapshot.knowledge_cutoff_ts,
                    availability_mode=snapshot.availability_mode,
                )
                digest = hashlib.sha256(canonical_jsonl_bytes(rows)).hexdigest()
                if digest != component.get("component_sha256"):
                    issues.append(
                        SnapshotVerificationIssue(
                            code="DATABASE_DRIFT",
                            component_key=spec.component_key,
                            message="Live candidate hash differs.",
                        )
                    )
        return issues


def _file_uri_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        return None
    path = unquote(parsed.path)
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(path)


__all__ = ["SnapshotVerifier"]
