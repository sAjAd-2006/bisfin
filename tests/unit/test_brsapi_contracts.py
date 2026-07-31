"""Unit tests for provider response value contracts."""

from datetime import UTC, datetime, timedelta

import pytest

from bisfin.integrations.brsapi.contracts import BrsApiRawResponse

pytestmark = pytest.mark.unit


def test_raw_response_requires_aware_ordered_timestamps() -> None:
    aware = datetime(2026, 7, 31, tzinfo=UTC)

    response = BrsApiRawResponse(
        status_code=200,
        headers=(),
        body_bytes=b"[]",
        request_started_at=aware,
        response_received_at=aware + timedelta(seconds=1),
        elapsed=timedelta(milliseconds=20),
    )

    assert response.body_bytes == b"[]"


def test_raw_response_rejects_naive_or_reversed_timestamps() -> None:
    aware = datetime(2026, 7, 31, tzinfo=UTC)
    naive = datetime(2026, 7, 31)

    with pytest.raises(ValueError, match="timezone-aware"):
        BrsApiRawResponse(200, (), b"[]", naive, naive, timedelta(0))

    with pytest.raises(ValueError, match="cannot precede"):
        BrsApiRawResponse(
            200,
            (),
            b"[]",
            aware,
            aware - timedelta(seconds=1),
            timedelta(0),
        )
