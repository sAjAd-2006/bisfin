"""Unit tests for exact response and canonical row hashing."""

import hashlib
from decimal import Decimal

import pytest

from bisfin.integrations.brsapi.contracts import JsonObject
from bisfin.integrations.brsapi.parser import (
    canonical_json_bytes,
    response_payload_sha256,
    row_payload_sha256,
)

pytestmark = pytest.mark.unit


def test_response_hash_uses_exact_bytes_including_outer_whitespace() -> None:
    compact = b'{"value":1}'
    spaced = b'{ "value": 1 }\n'

    assert response_payload_sha256(compact) == hashlib.sha256(compact).hexdigest()
    assert response_payload_sha256(spaced) == hashlib.sha256(spaced).hexdigest()
    assert response_payload_sha256(compact) != response_payload_sha256(spaced)


def test_canonical_row_hash_ignores_object_order_and_decimal_formatting() -> None:
    first: JsonObject = {"symbol": "فملی", "price": Decimal("7100.000"), "volume": 12}
    second: JsonObject = {"volume": Decimal("12.0"), "price": 7100, "symbol": "فملی"}

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert row_payload_sha256(first) == row_payload_sha256(second)


def test_canonical_json_is_compact_utf8_and_does_not_escape_persian() -> None:
    canonical = canonical_json_bytes({"b": [Decimal("0.0100"), True], "a": "فملی"})

    assert canonical == '{"a":"فملی","b":[0.01,true]}'.encode()
    assert b"\\u" not in canonical


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_decimal_cannot_be_hashed(value: Decimal) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        canonical_json_bytes({"value": value})
