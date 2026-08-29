from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bisfin.snapshots.errors import SnapshotManifestError
from bisfin.snapshots.manifest import (
    canonical_specification_bytes,
    parse_snapshot_manifest_bytes,
)
from bisfin.snapshots.serialization import canonical_jsonl_bytes


def _manifest_bytes(*, snapshot_code: str = "daily.raw-2026-08-29") -> bytes:
    return (
        "{\n"
        '  "schema_version": 1,\n'
        f'  "snapshot_code": "{snapshot_code}",\n'
        '  "knowledge_cutoff_ts": "2026-08-29T00:00:00Z",\n'
        '  "availability_mode": "PUBLIC_REPLAY",\n'
        '  "components": [{"component_key":"z","kind":"BAR_REVISION","bar_series_id":2,'
        '"event_from":"2026-01-01T00:00:00Z","event_to":"2026-02-01T00:00:00Z"},'
        '{"component_key":"a","kind":"BAR_REVISION","bar_series_id":1,'
        '"event_from":"2026-01-01T00:00:00Z","event_to":"2026-02-01T00:00:00Z",'
        '"allow_empty":true}]\n'
        "}\n"
    ).encode()


def test_manifest_is_strict_and_tracks_exact_and_semantic_hashes() -> None:
    document = parse_snapshot_manifest_bytes(_manifest_bytes())

    assert document.source_manifest_sha256 == hashlib.sha256(_manifest_bytes()).hexdigest()
    assert document.request.components[0].component_key == "z"
    assert (
        document.specification_sha256
        == hashlib.sha256(canonical_specification_bytes(document.request)).hexdigest()
    )


@pytest.mark.parametrize("code", ["../escape", ".hidden", "a/b", "a\\b", "a..b", "کید"])
def test_unsafe_snapshot_codes_are_rejected_without_sanitizing(code: str) -> None:
    with pytest.raises(SnapshotManifestError, match="snapshot_code"):
        parse_snapshot_manifest_bytes(_manifest_bytes(snapshot_code=code))


@pytest.mark.parametrize(
    "replacement",
    [
        '"unknown": true,',
        '"schema_version": 2,',
        '"event_to":"2026-09-01T00:00:00Z",',
    ],
)
def test_invalid_versions_unknown_fields_and_ranges_are_rejected(replacement: str) -> None:
    raw = _manifest_bytes()
    if replacement.startswith('"unknown"'):
        raw = raw.replace(
            b'  "schema_version": 1,', b"  " + replacement.encode() + b'\n  "schema_version": 1,'
        )
    elif replacement.startswith('"schema_version"'):
        raw = raw.replace(b'"schema_version": 1,', replacement.encode())
    else:
        raw = raw.replace(b'"event_to":"2026-02-01T00:00:00Z",', replacement.encode(), 1)
    with pytest.raises(SnapshotManifestError):
        parse_snapshot_manifest_bytes(raw)


def test_semantic_hash_is_order_and_timestamp_format_independent() -> None:
    first = parse_snapshot_manifest_bytes(_manifest_bytes())
    second = parse_snapshot_manifest_bytes(
        _manifest_bytes().replace(b"2026-08-29T00:00:00Z", b"2026-08-29T00:00:00+00:00")
    )

    assert first.source_manifest_sha256 != second.source_manifest_sha256
    assert first.specification_sha256 == second.specification_sha256


def test_jsonl_is_decimal_safe_sorted_and_uses_one_lf() -> None:
    rows = [
        {
            "bar_open_ts": datetime(2026, 1, 2, tzinfo=UTC),
            "bar_series_id": 2,
            "revision_no": 1,
            "price": Decimal("10.2300"),
            "nullable": None,
        },
        {
            "bar_open_ts": datetime(2026, 1, 1, tzinfo=UTC),
            "bar_series_id": 1,
            "revision_no": 2,
            "price": Decimal("1E+2"),
            "nullable": None,
        },
    ]

    payload = canonical_jsonl_bytes(rows)

    assert payload.endswith(b"\n")
    assert not payload.endswith(b"\n\n")
    assert payload.splitlines()[0].startswith(b'{"bar_open_ts":"2026-01-01T00:00:00Z"')
    assert b'"price":"100"' in payload
    assert b'"price":"10.23"' in payload
