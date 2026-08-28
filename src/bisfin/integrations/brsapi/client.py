"""Synchronous live and deterministic-fixture clients for one BrsApi endpoint."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx
from pydantic import SecretStr

from bisfin.integrations.brsapi.contracts import (
    BrsApiConfigurationError,
    BrsApiFixtureError,
    BrsApiHttpError,
    BrsApiRawResponse,
    BrsApiTimeoutError,
    BrsApiTransportError,
)
from bisfin.integrations.brsapi.normalization import normalize_brsapi_symbol

BRSAPI_CANDLESTICK_PATH = "Tsetmc/Candlestick.php"
BRSAPI_UNADJUSTED_DAILY_TYPE = "2"
BRSAPI_SYMBOL_PATH = "Tsetmc/Symbol.php"

_SAFE_RESPONSE_HEADERS = frozenset(
    {
        "content-length",
        "content-type",
        "date",
        "retry-after",
        "x-request-id",
    }
)
_KEY_QUERY_PARAMETER = re.compile(r"(?i)([?&]key=)[^&\s'\"<>]+")

type Clock = Callable[[], datetime]
type MonotonicClock = Callable[[], float]


class BrsApiClient(Protocol):
    """Only the explicitly scoped type=2 operation is publicly available."""

    def fetch_unadjusted_daily_candles(self, symbol: str) -> BrsApiRawResponse: ...


class BrsApiSymbolClient(Protocol):
    """The documented per-symbol metadata operation; no discovery surface exists."""

    def fetch_symbol_metadata(self, symbol: str) -> BrsApiRawResponse: ...


class HttpxBrsApiClient:
    """Small synchronous client with no automatic retry and no keyed URL logs."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        user_agent: str,
        clock: Clock | None = None,
        monotonic_clock: MonotonicClock | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = _validate_base_url(base_url)
        self._api_key = api_key
        self._connect_timeout = _positive_finite(
            connect_timeout_seconds,
            field="connect_timeout_seconds",
        )
        self._read_timeout = _positive_finite(
            read_timeout_seconds,
            field="read_timeout_seconds",
        )
        normalized_user_agent = user_agent.strip()
        if not normalized_user_agent:
            raise BrsApiConfigurationError("BrsApi User-Agent must not be empty.")
        self._user_agent = normalized_user_agent
        self._clock = clock or _utc_now
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._transport = transport

    def fetch_unadjusted_daily_candles(self, symbol: str) -> BrsApiRawResponse:
        normalized_symbol = normalize_brsapi_symbol(symbol)
        if not normalized_symbol:
            raise BrsApiConfigurationError("BrsApi symbol must not be empty.")

        return _fetch_raw_response(
            base_url=self._base_url,
            api_key=self._api_key,
            connect_timeout=self._connect_timeout,
            read_timeout=self._read_timeout,
            user_agent=self._user_agent,
            clock=self._clock,
            monotonic_clock=self._monotonic_clock,
            transport=self._transport,
            path=BRSAPI_CANDLESTICK_PATH,
            params={"type": BRSAPI_UNADJUSTED_DAILY_TYPE, "l18": normalized_symbol},
        )


class HttpxBrsApiSymbolClient:
    """Synchronous live client for the documented single-symbol endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        user_agent: str,
        clock: Clock | None = None,
        monotonic_clock: MonotonicClock | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = _validate_base_url(base_url)
        self._api_key = api_key
        self._connect_timeout = _positive_finite(
            connect_timeout_seconds,
            field="connect_timeout_seconds",
        )
        self._read_timeout = _positive_finite(
            read_timeout_seconds,
            field="read_timeout_seconds",
        )
        self._user_agent = _require_user_agent(user_agent)
        self._clock = clock or _utc_now
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._transport = transport

    def fetch_symbol_metadata(self, symbol: str) -> BrsApiRawResponse:
        normalized_symbol = normalize_brsapi_symbol(symbol)
        if not normalized_symbol:
            raise BrsApiConfigurationError("BrsApi symbol must not be empty.")
        return _fetch_raw_response(
            base_url=self._base_url,
            api_key=self._api_key,
            connect_timeout=self._connect_timeout,
            read_timeout=self._read_timeout,
            user_agent=self._user_agent,
            clock=self._clock,
            monotonic_clock=self._monotonic_clock,
            transport=self._transport,
            path=BRSAPI_SYMBOL_PATH,
            params={"l18": normalized_symbol},
        )


class FixtureBrsApiClient:
    """Network-free client that preserves exact committed UTF-8 fixture bytes."""

    def __init__(self, fixture_path: str | Path, *, clock: Clock | None = None) -> None:
        self._fixture_path = Path(fixture_path)
        self._clock = clock or _utc_now

    def fetch_unadjusted_daily_candles(self, symbol: str) -> BrsApiRawResponse:
        if not normalize_brsapi_symbol(symbol):
            raise BrsApiConfigurationError("BrsApi symbol must not be empty.")
        started_at = self._clock()
        try:
            body_bytes = self._fixture_path.read_bytes()
        except OSError as error:
            raise BrsApiFixtureError("BrsApi fixture could not be read.") from error
        try:
            body_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise BrsApiFixtureError("BrsApi fixture must be valid UTF-8.") from error
        received_at = self._clock()
        return BrsApiRawResponse(
            status_code=200,
            headers=(
                ("content-type", "application/json; charset=utf-8"),
                ("x-bisfin-source", "deterministic-fixture"),
            ),
            body_bytes=body_bytes,
            request_started_at=started_at,
            response_received_at=received_at,
            elapsed=received_at - started_at,
        )


class FixtureBrsApiSymbolClient:
    """Indexed, traversal-safe, network-free Symbol.php fixture client."""

    def __init__(self, fixture_directory: str | Path, *, clock: Clock | None = None) -> None:
        self._directory = Path(fixture_directory)
        self._clock = clock or _utc_now
        self._paths = _load_symbol_fixture_index(self._directory)

    def fetch_symbol_metadata(self, symbol: str) -> BrsApiRawResponse:
        normalized_symbol = normalize_brsapi_symbol(symbol)
        if not normalized_symbol:
            raise BrsApiConfigurationError("BrsApi symbol must not be empty.")
        path = self._paths.get(normalized_symbol)
        if path is None:
            raise BrsApiFixtureError("BrsApi Symbol fixture is not indexed for this symbol.")
        started_at = self._clock()
        try:
            body_bytes = path.read_bytes()
            body_bytes.decode("utf-8", errors="strict")
        except (OSError, UnicodeDecodeError) as error:
            raise BrsApiFixtureError("BrsApi Symbol fixture could not be read as UTF-8.") from error
        received_at = self._clock()
        return BrsApiRawResponse(
            status_code=200,
            headers=(
                ("content-type", "application/json; charset=utf-8"),
                ("x-bisfin-source", "deterministic-fixture"),
            ),
            body_bytes=body_bytes,
            request_started_at=started_at,
            response_received_at=received_at,
            elapsed=received_at - started_at,
        )


def _require_user_agent(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise BrsApiConfigurationError("BrsApi User-Agent must not be empty.")
    return normalized


def _fetch_raw_response(
    *,
    base_url: str,
    api_key: SecretStr,
    connect_timeout: float,
    read_timeout: float,
    user_agent: str,
    clock: Clock,
    monotonic_clock: MonotonicClock,
    transport: httpx.BaseTransport | None,
    path: str,
    params: dict[str, str],
) -> BrsApiRawResponse:
    secret = api_key.get_secret_value().strip()
    if not secret:
        raise BrsApiConfigurationError("BRSAPI_API_KEY is required for live mode.")
    started_at = clock()
    monotonic_started = monotonic_clock()
    timeout = httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=read_timeout,
        pool=connect_timeout,
    )
    try:
        with httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": user_agent},
            follow_redirects=False,
        ) as client:
            response = client.get(path, params={"key": secret, **params})
            body_bytes = response.content
    except httpx.TimeoutException as error:
        _scrub_httpx_exception(error, secret=secret)
        raise BrsApiTimeoutError("BrsApi request timed out.") from error
    except httpx.TransportError as error:
        _scrub_httpx_exception(error, secret=secret)
        raise BrsApiTransportError("BrsApi transport request failed.") from error
    received_at = clock()
    raw_response = BrsApiRawResponse(
        status_code=response.status_code,
        headers=_capture_safe_headers(response.headers, secret=secret),
        body_bytes=body_bytes,
        request_started_at=started_at,
        response_received_at=received_at,
        elapsed=timedelta(seconds=max(0.0, monotonic_clock() - monotonic_started)),
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        _scrub_httpx_exception(error, secret=secret)
        raise BrsApiHttpError(response.status_code, response=raw_response) from error
    return raw_response


def _duplicate_key_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _load_symbol_fixture_index(directory: Path) -> dict[str, Path]:
    try:
        payload = json.loads(
            (directory / "index.json").read_bytes().decode("utf-8", errors="strict"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            object_pairs_hook=_duplicate_key_object,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise BrsApiFixtureError("BrsApi Symbol fixture index is invalid.") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "symbols"}
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or not isinstance(payload["symbols"], dict)
    ):
        raise BrsApiFixtureError("BrsApi Symbol fixture index is invalid.")
    result: dict[str, Path] = {}
    for raw_symbol, raw_relative in payload["symbols"].items():
        if not isinstance(raw_symbol, str) or not isinstance(raw_relative, str):
            raise BrsApiFixtureError("BrsApi Symbol fixture index is invalid.")
        normalized_symbol = normalize_brsapi_symbol(raw_symbol)
        relative = Path(raw_relative)
        if (
            not normalized_symbol
            or normalized_symbol in result
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix != ".json"
        ):
            raise BrsApiFixtureError("BrsApi Symbol fixture index is unsafe.")
        candidate = directory / relative
        if not candidate.is_file():
            raise BrsApiFixtureError("BrsApi Symbol fixture file is missing.")
        result[normalized_symbol] = candidate
    return result


def _validate_base_url(value: str) -> str:
    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        raise BrsApiConfigurationError("BRSAPI_BASE_URL is invalid.") from None
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise BrsApiConfigurationError("BRSAPI_BASE_URL must use HTTPS with a valid host.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BrsApiConfigurationError(
            "BRSAPI_BASE_URL must not contain credentials, query parameters, or fragments."
        )
    return normalized.rstrip("/") + "/"


def _positive_finite(value: float, *, field: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise BrsApiConfigurationError(f"{field} must be positive and finite.")
    return value


def _capture_safe_headers(
    headers: httpx.Headers,
    *,
    secret: str,
) -> tuple[tuple[str, str], ...]:
    captured: list[tuple[str, str]] = []
    for name in sorted(_SAFE_RESPONSE_HEADERS):
        value = headers.get(name)
        if value is None:
            continue
        captured.append((name, _redact_text(value, secret=secret)[:512]))
    return tuple(captured)


def _redact_text(value: str, *, secret: str) -> str:
    sanitized = value
    if secret:
        sanitized = sanitized.replace(secret, "***")
        sanitized = sanitized.replace(quote(secret, safe=""), "***")
    return _KEY_QUERY_PARAMETER.sub(r"\1***", sanitized)


def _scrub_httpx_exception(
    error: httpx.RequestError | httpx.HTTPStatusError,
    *,
    secret: str,
) -> None:
    """Redact the message and remove query data from the chained exception."""

    try:
        request = error.request
        request.url = request.url.copy_with(query=None)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        # The mapped exception never includes the unsafe value.  This fallback
        # only accommodates custom transports that expose an incomplete request.
        pass
    error.args = tuple(
        _redact_text(argument, secret=secret) if isinstance(argument, str) else argument
        for argument in error.args
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "BRSAPI_CANDLESTICK_PATH",
    "BRSAPI_SYMBOL_PATH",
    "BRSAPI_UNADJUSTED_DAILY_TYPE",
    "BrsApiClient",
    "BrsApiSymbolClient",
    "FixtureBrsApiClient",
    "FixtureBrsApiSymbolClient",
    "HttpxBrsApiClient",
    "HttpxBrsApiSymbolClient",
]
