"""Unit tests for synchronous live and fixture BrsApi clients."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from bisfin.integrations.brsapi.client import FixtureBrsApiClient, HttpxBrsApiClient
from bisfin.integrations.brsapi.contracts import (
    BrsApiConfigurationError,
    BrsApiHttpError,
    BrsApiTimeoutError,
    BrsApiTransportError,
)

pytestmark = pytest.mark.unit

_FIXTURES = Path("tests/fixtures/brsapi")
_SECRET = "unit-test-key-must-never-escape"


def _clock(*values: datetime) -> object:
    iterator: Iterator[datetime] = iter(values)
    return lambda: next(iterator)


def _monotonic(*values: float) -> object:
    iterator: Iterator[float] = iter(values)
    return lambda: next(iterator)


def _client(
    transport: httpx.BaseTransport,
    *,
    secret: str = _SECRET,
) -> HttpxBrsApiClient:
    started = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
    return HttpxBrsApiClient(
        base_url="https://Api.BrsApi.ir/",
        api_key=SecretStr(secret),
        connect_timeout_seconds=2.5,
        read_timeout_seconds=7.5,
        user_agent="bisfin-test/1",
        clock=_clock(started, started + timedelta(milliseconds=40)),  # type: ignore[arg-type]
        monotonic_clock=_monotonic(10.0, 10.04),  # type: ignore[arg-type]
        transport=transport,
    )


def test_http_client_sends_exact_type2_query_and_encodes_persian_symbol() -> None:
    body = (_FIXTURES / "candlestick_type2_success.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/Tsetmc/Candlestick.php"
        assert dict(request.url.params) == {
            "key": _SECRET,
            "type": "2",
            "l18": "فملی",
        }
        assert request.headers["user-agent"] == "bisfin-test/1"
        assert b"%D9%81%D9%85%D9%84%DB%8C" in request.url.query
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "application/json", "X-Request-ID": "fixture-1"},
        )

    response = _client(httpx.MockTransport(handler)).fetch_unadjusted_daily_candles("فملی")

    assert response.body_bytes == body
    assert response.elapsed == timedelta(milliseconds=40)
    assert response.headers == (
        ("content-length", str(len(body))),
        ("content-type", "application/json"),
        ("x-request-id", "fixture-1"),
    )


def test_http_client_normalizes_symbol_before_query_without_numeric_coercion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["l18"] == "کیمیا 007"
        return httpx.Response(200, content=b"[]")

    _client(httpx.MockTransport(handler)).fetch_unadjusted_daily_candles("  كيميا  ۰۰۷ ")


def test_http_error_retains_exact_body_but_scrubs_chained_request_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b'{"temporary":true}', request=request)

    with pytest.raises(BrsApiHttpError) as captured:
        _client(httpx.MockTransport(handler)).fetch_unadjusted_daily_candles("فملی")

    error = captured.value
    assert error.status_code == 503
    assert error.response.body_bytes == b'{"temporary":true}'
    rendered = f"{error!r} {error} {error.__cause__!r} {error.__cause__}"
    assert _SECRET not in rendered
    cause = error.__cause__
    assert isinstance(cause, httpx.HTTPStatusError)
    assert cause.request.url.query == b""


def test_timeout_maps_with_chaining_and_no_key_in_exception_graph() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("deterministic timeout", request=request)

    with pytest.raises(BrsApiTimeoutError) as captured:
        _client(httpx.MockTransport(handler)).fetch_unadjusted_daily_candles("فملی")

    cause = captured.value.__cause__
    assert isinstance(cause, httpx.ReadTimeout)
    rendered = f"{captured.value!r} {captured.value} {cause!r} {cause} {cause.request.url!r}"
    assert _SECRET not in rendered
    assert cause.request.url.query == b""


def test_transport_failure_maps_without_automatic_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(BrsApiTransportError):
        _client(httpx.MockTransport(handler)).fetch_unadjusted_daily_candles("فملی")

    assert calls == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_url": "http://Api.BrsApi.ir/"},
        {"base_url": "https://user:password@Api.BrsApi.ir/"},
        {"base_url": "https://Api.BrsApi.ir/?key=unsafe"},
        {"connect_timeout_seconds": 0.0},
        {"read_timeout_seconds": float("inf")},
        {"user_agent": "   "},
    ],
)
def test_http_client_rejects_unsafe_configuration(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "base_url": "https://Api.BrsApi.ir/",
        "api_key": SecretStr(_SECRET),
        "connect_timeout_seconds": 1.0,
        "read_timeout_seconds": 2.0,
        "user_agent": "bisfin-test/1",
        "transport": httpx.MockTransport(lambda _: httpx.Response(200, content=b"[]")),
    }
    values.update(overrides)

    with pytest.raises(BrsApiConfigurationError):
        HttpxBrsApiClient(**values)  # type: ignore[arg-type]


def test_live_mode_requires_nonempty_secret_and_symbol() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"[]"))

    with pytest.raises(BrsApiConfigurationError, match="API_KEY"):
        _client(transport, secret="  ").fetch_unadjusted_daily_candles("فملی")
    with pytest.raises(BrsApiConfigurationError, match="symbol"):
        _client(transport).fetch_unadjusted_daily_candles("  ")


def test_fixture_client_preserves_exact_bytes_and_uses_injected_clock() -> None:
    path = _FIXTURES / "candlestick_type2_success.json"
    started = datetime(2030, 1, 1, 8, 0, tzinfo=UTC)
    client = FixtureBrsApiClient(
        path,
        clock=_clock(started, started + timedelta(milliseconds=5)),  # type: ignore[arg-type]
    )

    response = client.fetch_unadjusted_daily_candles("فملی")

    assert response.body_bytes == path.read_bytes()
    assert response.request_started_at == started
    assert response.response_received_at == started + timedelta(milliseconds=5)
    assert response.elapsed == timedelta(milliseconds=5)
    assert ("x-bisfin-source", "deterministic-fixture") in response.headers


def test_fixture_client_needs_no_api_key_and_returns_malformed_bytes_unchanged() -> None:
    path = _FIXTURES / "candlestick_malformed_json.txt"
    now = datetime(2030, 1, 1, tzinfo=UTC)
    client = FixtureBrsApiClient(path, clock=lambda: now)

    response = client.fetch_unadjusted_daily_candles("فملی")

    assert response.body_bytes == path.read_bytes()
