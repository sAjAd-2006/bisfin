from __future__ import annotations

from pathlib import Path

import pytest

from bisfin.backtest.errors import SnapshotArtifactUnavailableError
from bisfin.backtest.snapshot_data import load_artifact_bars


def test_artifact_loader_parses_exact_decimal_and_timestamps(tmp_path: Path) -> None:
    component = tmp_path / "component.jsonl"
    component.write_text(
        '{"available_at":"2031-01-02T00:00:00Z","bar_open_ts":"2031-01-01T00:00:00Z",'
        '"bar_series_id":9,"close_price":"10.2500","effective_available_at":"2031-01-02T00:00:00Z",'
        '"revision_no":1,"system_available_at":"2031-01-02T00:00:00Z"}\n',
        encoding="utf-8",
    )

    rows = load_artifact_bars(component.as_uri(), expected_bar_series_id=9)

    assert str(rows[0].close_price) == "10.2500"
    assert rows[0].bar_open_ts.isoformat() == "2031-01-01T00:00:00+00:00"


def test_artifact_loader_rejects_invalid_rows_and_wrong_series(tmp_path: Path) -> None:
    component = tmp_path / "component.jsonl"
    component.write_text('{"bar_open_ts":"not-a-timestamp"}\n', encoding="utf-8")

    with pytest.raises(SnapshotArtifactUnavailableError):
        load_artifact_bars(component.as_uri(), expected_bar_series_id=9)
