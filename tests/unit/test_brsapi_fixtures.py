"""Guard deterministic BrsApi fixtures against accidental secret/network data."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_DIRECTORY = Path("tests/fixtures/brsapi")
_EXPECTED = {
    "candlestick_malformed_json.txt",
    "candlestick_type2_corrected.json",
    "candlestick_type2_duplicate_date.json",
    "candlestick_type2_no_data.json",
    "candlestick_type2_partial_invalid.json",
    "candlestick_type2_provider_error.json",
    "candlestick_type2_success.json",
}


def test_required_fixture_set_is_complete_and_utf8() -> None:
    paths = {path.name: path for path in _DIRECTORY.iterdir() if path.is_file()}

    assert set(paths) == _EXPECTED
    for path in paths.values():
        assert path.read_bytes().decode("utf-8")


def test_json_fixtures_are_valid_and_contain_no_credentials_or_private_headers() -> None:
    for path in sorted(_DIRECTORY.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        json.loads(text)
        lowered = text.lower()
        assert "api_key" not in lowered
        assert '"key"' not in lowered
        assert "authorization" not in lowered
        assert "bearer " not in lowered
        assert "production_request_id" not in lowered


def test_malformed_fixture_is_intentionally_not_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        json.loads((_DIRECTORY / "candlestick_malformed_json.txt").read_text(encoding="utf-8"))
