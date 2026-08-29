"""Contract tests for the isolated SQLAlchemy Core backtest mapping surface."""

from bisfin.db.backtest_tables import BACKTEST_MAPPED_TABLES, backtest_metadata


def test_reference_engine_backtest_tables_are_query_only_core_metadata() -> None:
    expected = {
        "backtest.strategy",
        "backtest.strategy_version",
        "backtest.run",
        "backtest.run_instrument",
        "backtest.run_market_series",
        "backtest.decision_context",
        "backtest.decision_bar_input",
        "backtest.signal",
        "backtest.bt_order",
        "backtest.order_event",
        "backtest.fill",
        "backtest.round_trip_trade",
        "backtest.trade_fill_allocation",
        "backtest.fill_market_reference",
        "backtest.cash_ledger",
        "backtest.position_ledger",
        "backtest.position_snapshot",
        "backtest.position_valuation_bar_reference",
        "backtest.equity_point",
        "backtest.run_summary",
    }

    assert {table.fullname for table in BACKTEST_MAPPED_TABLES} == expected
    assert set(backtest_metadata.tables) == expected
