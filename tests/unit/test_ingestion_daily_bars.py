"""Pure daily-bar ingestion policy tests."""

import pytest

from bisfin.domain.ingestion import IngestionBatchStatus
from bisfin.ingestion.daily_bars import source_record_key, terminal_status

pytestmark = pytest.mark.unit


def test_source_record_key_preserves_string_identity_and_leading_zeroes() -> None:
    assert (
        source_record_key(
            normalized_symbol="۰۰فملی",
            source_date_text=" ۱۴۰۵/۰۱/۰۲ ",
        )
        == "brsapi|candlestick|type=2|۰۰فملی|۱۴۰۵/۰۱/۰۲"
    )


@pytest.mark.parametrize(
    ("accepted", "rejected", "expected"),
    [
        (2, 0, IngestionBatchStatus.SUCCEEDED),
        (2, 1, IngestionBatchStatus.PARTIAL),
        (0, 2, IngestionBatchStatus.QUARANTINED),
        (0, 0, IngestionBatchStatus.SUCCEEDED),
    ],
)
def test_terminal_status_rules(
    accepted: int,
    rejected: int,
    expected: IngestionBatchStatus,
) -> None:
    assert terminal_status(accepted_count=accepted, rejected_count=rejected) is expected


def test_terminal_status_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        terminal_status(accepted_count=-1, rejected_count=0)
