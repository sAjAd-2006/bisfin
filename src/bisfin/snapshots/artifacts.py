"""Atomic local filesystem publication for immutable snapshot artifacts."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from bisfin.snapshots.errors import SnapshotArtifactError
from bisfin.snapshots.manifest import validate_snapshot_code
from bisfin.snapshots.serialization import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class WrittenArtifact:
    relative_path: Path
    absolute_path: Path
    sha256: str
    byte_count: int


def component_relative_path(ordinal: int, component_key: str) -> Path:
    if ordinal < 1:
        raise ValueError("component ordinal must be positive")
    component_digest = hashlib.sha256(component_key.encode("utf-8")).hexdigest()
    return Path("components") / f"{ordinal:04d}-{component_digest}.jsonl"


class SnapshotArtifactWriter:
    """Create a private staging directory and atomically publish it once complete."""

    def __init__(self, output_root: str | Path, snapshot_code: str) -> None:
        self._snapshot_code = validate_snapshot_code(snapshot_code)
        self._output_root = Path(output_root).expanduser().resolve(strict=False)
        self._final_directory = self._output_root / self._snapshot_code
        self._staging_directory: Path | None = None

    @property
    def final_directory(self) -> Path:
        return self._final_directory

    def write_component(self, ordinal: int, component_key: str, payload: bytes) -> WrittenArtifact:
        relative_path = component_relative_path(ordinal, component_key)
        return self._write_bytes(relative_path, payload)

    def write_manifest(self, payload: object) -> WrittenArtifact:
        return self._write_bytes(Path("manifest.json"), canonical_json_bytes(payload))

    def publish(self) -> Path:
        staging = self._ensure_staging()
        if self._final_directory.exists():
            raise SnapshotArtifactError("snapshot artifact directory already exists")
        if not (staging / "manifest.json").is_file():
            raise SnapshotArtifactError("snapshot artifact manifest has not been written")
        try:
            os.replace(staging, self._final_directory)
        except OSError as error:
            raise SnapshotArtifactError("snapshot artifact publication failed") from error
        self._staging_directory = None
        return self._final_directory

    def cleanup_incomplete(self) -> None:
        """Remove only this writer's private staging directory, never final evidence."""

        if self._staging_directory is not None and self._staging_directory.exists():
            shutil.rmtree(self._staging_directory)
        self._staging_directory = None

    def _write_bytes(self, relative_path: Path, payload: bytes) -> WrittenArtifact:
        staging = self._ensure_staging()
        destination = staging / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.write_bytes(payload)
        except OSError as error:
            raise SnapshotArtifactError("snapshot artifact write failed") from error
        return WrittenArtifact(
            relative_path=relative_path,
            absolute_path=destination,
            sha256=hashlib.sha256(payload).hexdigest(),
            byte_count=len(payload),
        )

    def _ensure_staging(self) -> Path:
        if self._staging_directory is None:
            try:
                self._output_root.mkdir(parents=True, exist_ok=True)
                self._staging_directory = self._output_root / (
                    f".{self._snapshot_code}.staging-{uuid.uuid4().hex}"
                )
                self._staging_directory.mkdir(mode=0o700)
            except OSError as error:
                raise SnapshotArtifactError(
                    "snapshot staging directory cannot be created"
                ) from error
        return self._staging_directory


__all__ = ["SnapshotArtifactWriter", "WrittenArtifact", "component_relative_path"]
