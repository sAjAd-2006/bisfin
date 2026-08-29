"""Read bar revisions from verified immutable snapshot artifact components."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from bisfin.backtest.contracts import DecisionBar
from bisfin.backtest.errors import SnapshotArtifactUnavailableError


def load_artifact_bars(storage_uri: str, *, expected_bar_series_id: int) -> tuple[DecisionBar, ...]:
    """Decode one PR-07 JSONL component without consulting PostgreSQL for values."""

    path = _file_uri_path(storage_uri)
    if path is None or not path.is_file():
        raise SnapshotArtifactUnavailableError("Snapshot component artifact is unavailable.")
    rows: list[DecisionBar] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise SnapshotArtifactUnavailableError(
            "Snapshot component artifact cannot be read."
        ) from error
    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise SnapshotArtifactUnavailableError("Snapshot component contains a blank JSONL row.")
        try:
            row = json.loads(line, parse_float=Decimal, parse_constant=_reject_non_finite)
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
            bar = DecisionBar.model_validate(
                {
                    "bar_open_ts": row["bar_open_ts"],
                    "bar_series_id": row["bar_series_id"],
                    "revision_no": row["revision_no"],
                    "available_at": row["available_at"],
                    "system_available_at": row["system_available_at"],
                    "effective_available_at": row["effective_available_at"],
                    "close_price": _decimal_value(row["close_price"]),
                }
            )
        except (KeyError, TypeError, ValueError, InvalidOperation, ValidationError) as error:
            raise SnapshotArtifactUnavailableError(
                f"Snapshot component row {line_number} is invalid."
            ) from error
        if bar.bar_series_id != expected_bar_series_id:
            raise SnapshotArtifactUnavailableError(
                "Snapshot component bar series does not match binding."
            )
        if bar.revision_no < 1:
            raise SnapshotArtifactUnavailableError("Snapshot component revision number is invalid.")
        rows.append(bar)
    if not rows:
        raise SnapshotArtifactUnavailableError("Snapshot component has no bar rows.")
    return tuple(rows)


def _decimal_value(value: object) -> Decimal:
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int | str):
        parsed = Decimal(value)
    else:
        raise ValueError("financial artifact values must be Decimal strings or integers")
    if not parsed.is_finite():
        raise ValueError("financial artifact values must be finite")
    return parsed


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"non-finite JSON constant {value!r} is not permitted")


def _file_uri_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        return None
    path = unquote(parsed.path)
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(path)


__all__ = ["load_artifact_bars"]
