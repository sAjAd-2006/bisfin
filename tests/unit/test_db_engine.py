"""Engine construction tests that never contact PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Engine

import bisfin.db.engine as engine_module
from bisfin.db.engine import create_engine, dispose_engine, get_engine_configuration


@dataclass(frozen=True, slots=True)
class _Settings:
    database_pool_size: int = 7
    database_max_overflow: int = 3
    database_pool_timeout_seconds: float = 4.5
    database_statement_timeout_ms: int = 8_000
    database_application_name: str = "bisfin-test"

    @property
    def sqlalchemy_database_url(self) -> str:
        return "postgresql+psycopg://worker:very-secret@localhost:5432/bisfin"


def test_engine_configuration_is_non_secret() -> None:
    configuration = get_engine_configuration(_Settings())
    assert configuration.pool_pre_ping is True
    assert configuration.pool_size == 7
    assert configuration.max_overflow == 3
    assert configuration.pool_timeout_seconds == 4.5
    assert configuration.application_name == "bisfin-test"
    assert configuration.statement_timeout_ms == 8_000
    assert "secret" not in repr(configuration)


def test_engine_factory_passes_pool_and_postgresql_session_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = cast("Engine", object())

    def fake_create_engine(url: str, **kwargs: object) -> Engine:
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(engine_module, "sqlalchemy_create_engine", fake_create_engine)

    result = create_engine(_Settings())

    assert result is sentinel
    assert captured["pool_pre_ping"] is True
    assert captured["pool_size"] == 7
    assert captured["max_overflow"] == 3
    assert captured["pool_timeout"] == 4.5
    assert captured["connect_args"] == {
        "application_name": "bisfin-test",
        "options": "-c statement_timeout=8000",
    }


def test_dispose_engine_closes_pool() -> None:
    engine = MagicMock(spec=Engine)
    dispose_engine(engine)
    engine.dispose.assert_called_once_with(close=True)
