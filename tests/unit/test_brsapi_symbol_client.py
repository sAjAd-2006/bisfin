"""Contract tests for the BrsApi single-symbol HTTP and fixture clients."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from bisfin.integrations.brsapi.client import (
    FixtureBrsApiSymbolClient,
    HttpxBrsApiSymbolClient,
)
from bisfin.integrations.brsapi.contracts import (
    BrsApiConfigurationError,
    BrsApiFixtureError,
    BrsApiHttpError,
    BrsApiTimeoutError,
)

pytestmark = pytest.mark.unit

_FIXTURES = Path("tests/fixtures/brsapi")
_SECRET = "symbol-client-secret-must-never-escape"


def _clock(*values: datetime) -> object:
    iterator: Iterator[datetime] = iter(values)
    return lambda: next(iterator)


def _monotonic(*values: float) -> object:
    iterator: Iterator[float] = iter(values)
    return lambda: next(iterator)


def _client(transport: httpx.BaseTransport) -> HttpxBrsApiSymbolClient:
    started = datetime(2026, 8, 28, 8, 0, tzinfo=UTC)
    return HttpxBrsApiSymbolClient(
        base_url="https://Api.BrsApi.ir/",
        api_key=SecretStr(_SECRET),
        connect_timeout_seconds=2.5,
        read_timeout_seconds=7.5,
        user_agent="bisfin-symbol-test/1",
        clock=_clock(started, started + timedelta(milliseconds=25)),  # type: ignore[arg-type]
        monotonic_clock=_monotonic(10.0, 10.025),  # type: ignore[arg-type]
        transport=transport,
    )


def test_symbol_http_client_uses_exact_documented_request_and_preserves_bytes() -> None:
    body = (_FIXTURES / "symbol_success.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/Tsetmc/Symbol.php"
        assert list(request.url.params.multi_items()) == [
            ("key", _SECRET),
            ("l18", "فملی"),
        ]
        assert request.headers["user-agent"] == "bisfin-symbol-test/1"
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "application/json", "X-Request-ID": "symbol-fixture-1"},
        )

    response = _client(httpx.MockTransport(handler)).fetch_symbol_metadata("  فملي ")

    assert response.body_bytes == body
    assert response.elapsed == timedelta(milliseconds=25)
    assert response.headers == (
        ("content-length", str(len(body))),
        ("content-type", "application/json"),
        ("x-request-id", "symbol-fixture-1"),
    )


def test_symbol_http_client_does_not_follow_redirect_or_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": "/elsewhere"}, request=request)

    with pytest.raises(BrsApiHttpError) as captured:
        _client(httpx.MockTransport(handler)).fetch_symbol_metadata("فملی")

    assert calls == 1
    assert captured.value.status_code == 302


def test_symbol_http_client_scrubs_secret_from_timeout_exception_graph() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"failed URL {request.url}", request=request)

    with pytest.raises(BrsApiTimeoutError) as captured:
        _client(httpx.MockTransport(handler)).fetch_symbol_metadata("فملی")

    cause = captured.value.__cause__
    assert isinstance(cause, httpx.ReadTimeout)
    rendered = f"{captured.value!r} {captured.value} {cause!r} {cause} {cause.request.url!r}"
    assert _SECRET not in rendered
    assert cause.request.url.query == b""


def test_symbol_live_client_requires_nonempty_key_and_symbol() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"{}"))
    kwargs = {
        "base_url": "https://Api.BrsApi.ir/",
        "connect_timeout_seconds": 1.0,
        "read_timeout_seconds": 1.0,
        "user_agent": "bisfin-test/1",
        "transport": transport,
    }

    client = HttpxBrsApiSymbolClient(api_key=SecretStr(" "), **kwargs)  # type: ignore[arg-type]
    with pytest.raises(BrsApiConfigurationError, match="API_KEY"):
        client.fetch_symbol_metadata("فملی")

    client = HttpxBrsApiSymbolClient(api_key=SecretStr(_SECRET), **kwargs)  # type: ignore[arg-type]
    with pytest.raises(BrsApiConfigurationError, match="symbol"):
        client.fetch_symbol_metadata("  ")


def test_indexed_symbol_fixture_client_needs_no_key_and_preserves_exact_bytes() -> None:
    started = datetime(2030, 1, 1, 8, 0, tzinfo=UTC)
    client = FixtureBrsApiSymbolClient(
        _FIXTURES / "symbols",
        clock=_clock(started, started + timedelta(milliseconds=5)),  # type: ignore[arg-type]
    )

    response = client.fetch_symbol_metadata("  فملي ")

    expected_path = _FIXTURES / "symbols" / "femeli.json"
    assert response.body_bytes == expected_path.read_bytes()
    assert response.request_started_at == started
    assert response.response_received_at == started + timedelta(milliseconds=5)
    assert response.elapsed == timedelta(milliseconds=5)
    assert ("x-bisfin-source", "deterministic-fixture") in response.headers


def test_indexed_symbol_fixture_rejects_unknown_symbol() -> None:
    client = FixtureBrsApiSymbolClient(_FIXTURES / "symbols")

    with pytest.raises(BrsApiFixtureError, match="not indexed"):
        client.fetch_symbol_metadata("ناشناخته")


@pytest.mark.parametrize(
    "index_body",
    [
        b'{"schema_version":1,"symbols":{"test":"../escape.json"}}',
        b'{"schema_version":1,"symbols":{"test":"C:/escape.json"}}',
        b'{"schema_version":1,"symbols":{"test":"ok.json","test":"other.json"}}',
        b'{"schema_version":NaN,"symbols":{}}',
    ],
)
def test_indexed_symbol_fixture_rejects_unsafe_or_non_strict_index(
    tmp_path: Path,
    index_body: bytes,
) -> None:
    (tmp_path / "index.json").write_bytes(index_body)

    with pytest.raises(BrsApiFixtureError):
        FixtureBrsApiSymbolClient(tmp_path)
