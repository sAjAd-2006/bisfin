"""Fail-closed parsing and semantic hashing for snapshot specifications."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bisfin.snapshots.contracts import SnapshotBuildRequest
from bisfin.snapshots.errors import SnapshotManifestError
from bisfin.snapshots.serialization import canonical_json_bytes

_SAFE_SNAPSHOT_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class _DuplicateJsonField(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SnapshotManifestDocument:
    payload_bytes: bytes
    source_manifest_sha256: str
    request: SnapshotBuildRequest
    specification_sha256: str


def _pairs_to_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonField(f"duplicate JSON object field: {key}")
        result[key] = value
    return result


def _forbid_non_finite(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not permitted: {value}")


def validate_snapshot_code(snapshot_code: str) -> str:
    """Accept only portable path-component codes; unsafe values are never rewritten."""

    if (
        _SAFE_SNAPSHOT_CODE.fullmatch(snapshot_code) is None
        or ".." in snapshot_code
        or snapshot_code.startswith(".")
    ):
        raise SnapshotManifestError("snapshot_code is not a safe filesystem component")
    return snapshot_code


def canonical_specification_bytes(request: SnapshotBuildRequest) -> bytes:
    """Serialize semantic meaning, independent of source whitespace/order/timezone spelling."""

    payload = request.model_dump()
    components = payload["components"]
    assert isinstance(components, tuple)
    payload["components"] = sorted(components, key=lambda item: str(item["component_key"]))
    return canonical_json_bytes(payload)


def parse_snapshot_manifest_bytes(payload_bytes: bytes) -> SnapshotManifestDocument:
    try:
        decoded = payload_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise SnapshotManifestError("snapshot manifest is not UTF-8") from error
    try:
        raw = json.loads(
            decoded,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_forbid_non_finite,
            object_pairs_hook=_pairs_to_object,
        )
    except _DuplicateJsonField as error:
        raise SnapshotManifestError("snapshot manifest has duplicate JSON fields") from error
    except (ValueError, json.JSONDecodeError) as error:
        raise SnapshotManifestError("snapshot manifest is malformed JSON") from error
    if not isinstance(raw, dict):
        raise SnapshotManifestError("snapshot manifest root must be an object")
    if raw.get("schema_version") != 1 or type(raw.get("schema_version")) is not int:
        raise SnapshotManifestError("unsupported schema_version")
    try:
        request = SnapshotBuildRequest.model_validate(raw)
        validate_snapshot_code(request.snapshot_code)
    except (ValidationError, ValueError) as error:
        raise SnapshotManifestError(f"snapshot manifest is invalid: {error}") from error
    semantic = canonical_specification_bytes(request)
    return SnapshotManifestDocument(
        payload_bytes=payload_bytes,
        source_manifest_sha256=hashlib.sha256(payload_bytes).hexdigest(),
        request=request,
        specification_sha256=hashlib.sha256(semantic).hexdigest(),
    )


def load_snapshot_manifest(path: str | Path) -> SnapshotManifestDocument:
    try:
        payload_bytes = Path(path).read_bytes()
    except OSError as error:
        raise SnapshotManifestError("snapshot manifest cannot be read") from error
    return parse_snapshot_manifest_bytes(payload_bytes)


def snapshot_manifest_json_schema() -> dict[str, Any]:
    return SnapshotBuildRequest.model_json_schema()


__all__ = [
    "SnapshotManifestDocument",
    "canonical_specification_bytes",
    "load_snapshot_manifest",
    "parse_snapshot_manifest_bytes",
    "snapshot_manifest_json_schema",
    "validate_snapshot_code",
]
