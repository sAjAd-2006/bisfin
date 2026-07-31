"""Synchronous live and deterministic-fixture clients for one BrsApi endpoint."""

from __future__ import annotations

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

        secret = self._api_key.get_secret_value().strip()
        if not secret:
            raise BrsApiConfigurationError("BRSAPI_API_KEY is required for live mode.")

        started_at = self._clock()
        monotonic_started = self._monotonic_clock()
        timeout = httpx.Timeout(
            connect=self._connect_timeout,
            read=self._read_timeout,
            write=self._read_timeout,
            pool=self._connect_timeout,
        )
        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=timeout,
                transport=self._transport,
                headers={"Accept": "application/json", "User-Agent": self._user_agent},
                follow_redirects=False,
            ) as client:
                response = client.get(
                    BRSAPI_CANDLESTICK_PATH,
                    params={
                        "key": secret,
                        "type": BRSAPI_UNADJUSTED_DAILY_TYPE,
                        "l18": normalized_symbol,
                    },
                )
                body_bytes = response.content
        except httpx.TimeoutException as error:
            _scrub_httpx_exception(error, secret=secret)
            raise BrsApiTimeoutError("BrsApi request timed out.") from error
        except httpx.TransportError as error:
            _scrub_httpx_exception(error, secret=secret)
            raise BrsApiTransportError("BrsApi transport request failed.") from error

        received_at = self._clock()
        elapsed_seconds = self._monotonic_clock() - monotonic_started
        raw_response = BrsApiRawResponse(
            status_code=response.status_code,
            headers=_capture_safe_headers(response.headers, secret=secret),
            body_bytes=body_bytes,
            request_started_at=started_at,
            response_received_at=received_at,
            elapsed=timedelta(seconds=max(0.0, elapsed_seconds)),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            _scrub_httpx_exception(error, secret=secret)
            raise BrsApiHttpError(response.status_code, response=raw_response) from error
        return raw_response


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
    "BRSAPI_UNADJUSTED_DAILY_TYPE",
    "BrsApiClient",
    "FixtureBrsApiClient",
    "HttpxBrsApiClient",
]
