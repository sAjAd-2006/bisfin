"""Strict run-manifest parsing and semantic hashing."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bisfin.backtest.contracts import BacktestRunRequest
from bisfin.backtest.errors import BacktestManifestError
from bisfin.snapshots.serialization import canonical_json_bytes

_SAFE_RUN_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class _DuplicateJsonField(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BacktestManifestDocument:
    payload_bytes: bytes
    source_manifest_sha256: str
    request: BacktestRunRequest
    run_spec_sha256: str
    parameter_sha256: str


def _pairs_to_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonField(f"duplicate JSON object field: {key}")
        result[key] = value
    return result


def _forbid_non_finite(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not permitted: {value}")


def validate_run_code(run_code: str) -> str:
    """Accept a portable identifier; unsafe values are never normalized."""

    if _SAFE_RUN_CODE.fullmatch(run_code) is None or ".." in run_code or run_code.startswith("."):
        raise BacktestManifestError("run_code is not a safe identifier")
    return run_code


def canonical_run_specification_bytes(request: BacktestRunRequest) -> bytes:
    """Hash semantic experiment inputs while excluding storage-only run identity."""

    payload = request.model_dump()
    payload.pop("run_code")
    instruments = payload["instruments"]
    assert isinstance(instruments, tuple)
    payload["instruments"] = sorted(instruments, key=lambda item: int(item["instrument_id"]))
    return canonical_json_bytes(payload)


def canonical_parameter_bytes(request: BacktestRunRequest) -> bytes:
    """Hash only strategy parameters, independently of the selected strategy source."""

    return canonical_json_bytes(request.strategy.parameters)


def parse_backtest_manifest_bytes(payload_bytes: bytes) -> BacktestManifestDocument:
    try:
        decoded = payload_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise BacktestManifestError("backtest manifest is not UTF-8") from error
    try:
        raw = json.loads(
            decoded,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_forbid_non_finite,
            object_pairs_hook=_pairs_to_object,
        )
    except _DuplicateJsonField as error:
        raise BacktestManifestError("backtest manifest has duplicate JSON fields") from error
    except (ValueError, json.JSONDecodeError) as error:
        raise BacktestManifestError("backtest manifest is malformed JSON") from error
    if not isinstance(raw, dict):
        raise BacktestManifestError("backtest manifest root must be an object")
    if raw.get("schema_version") != 1 or type(raw.get("schema_version")) is not int:
        raise BacktestManifestError("unsupported schema_version")
    try:
        request = BacktestRunRequest.model_validate(raw)
        validate_run_code(request.run_code)
    except (ValidationError, ValueError) as error:
        raise BacktestManifestError(f"backtest manifest is invalid: {error}") from error
    return BacktestManifestDocument(
        payload_bytes=payload_bytes,
        source_manifest_sha256=hashlib.sha256(payload_bytes).hexdigest(),
        request=request,
        run_spec_sha256=hashlib.sha256(canonical_run_specification_bytes(request)).hexdigest(),
        parameter_sha256=hashlib.sha256(canonical_parameter_bytes(request)).hexdigest(),
    )


def load_backtest_manifest(path: str | Path) -> BacktestManifestDocument:
    try:
        payload = Path(path).read_bytes()
    except OSError as error:
        raise BacktestManifestError("backtest manifest cannot be read") from error
    return parse_backtest_manifest_bytes(payload)


def backtest_manifest_json_schema() -> dict[str, Any]:
    return BacktestRunRequest.model_json_schema()


__all__ = [
    "BacktestManifestDocument",
    "backtest_manifest_json_schema",
    "canonical_parameter_bytes",
    "canonical_run_specification_bytes",
    "load_backtest_manifest",
    "parse_backtest_manifest_bytes",
    "validate_run_code",
]
