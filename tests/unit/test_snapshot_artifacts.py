from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bisfin.snapshots.artifacts import SnapshotArtifactWriter, component_relative_path
from bisfin.snapshots.errors import SnapshotArtifactError


def test_component_filename_is_safe_and_deterministic() -> None:
    path = component_relative_path(1, "fa/meli")

    assert path.as_posix() == "components/0001-" + hashlib.sha256(b"fa/meli").hexdigest() + ".jsonl"


def test_artifacts_publish_atomically_without_overwrite(tmp_path: Path) -> None:
    writer = SnapshotArtifactWriter(tmp_path, "daily.raw-2026-08-29")
    component = writer.write_component(1, "fa/meli", b'{"one":1}\n')
    manifest = writer.write_manifest(
        {
            "artifact_schema_version": 1,
            "snapshot_code": "daily.raw-2026-08-29",
            "knowledge_cutoff_ts": datetime(2026, 8, 29, tzinfo=UTC),
            "components": [{"relative_storage_path": component.relative_path.as_posix()}],
        }
    )
    final = writer.publish()

    assert final == tmp_path / "daily.raw-2026-08-29"
    assert (final / component.relative_path).read_bytes() == b'{"one":1}\n'
    assert manifest.sha256 == hashlib.sha256((final / "manifest.json").read_bytes()).hexdigest()
    assert not any(path.name.startswith(".daily.raw") for path in tmp_path.iterdir())

    second = SnapshotArtifactWriter(tmp_path, "daily.raw-2026-08-29")
    with pytest.raises(SnapshotArtifactError, match="already exists"):
        second.publish()
