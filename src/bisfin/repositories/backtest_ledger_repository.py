"""Atomic persistence of the deterministic reference-backtest journal."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from bisfin.backtest.engine import SimulatedFill, SimulationResult
from bisfin.db.errors import translate_database_errors
from bisfin.snapshots.serialization import canonical_json_bytes


class SqlAlchemyBacktestLedgerRepository:
    """Persist one completed reference journal; callers own the encompassing transaction."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def persist(
        self,
        *,
        run_id: int,
        result: SimulationResult,
        initial_capital: Decimal,
        base_currency_code: str,
        summary: Mapping[str, object],
    ) -> None:
        """Write the all-or-nothing v1 ledger in deterministic journal order."""

        with translate_database_errors(operation="persist reference backtest ledger"):
            self._insert_cash(
                run_id=run_id,
                entry_ts=_first_event_ts(result),
                currency_code=base_currency_code,
                entry_type="INITIAL_CAPITAL",
                amount=initial_capital,
                source_key="initial-capital",
            )
            context_ids = self._insert_decisions(run_id, result)
            signal_ids = self._insert_signals(run_id, result, context_ids)
            order_ids = self._insert_orders(run_id, result, signal_ids)
            fill_ids = self._insert_fills(
                run_id=run_id,
                result=result,
                order_ids=order_ids,
                currency_code=base_currency_code,
            )
            self._insert_order_events(run_id, result, order_ids, fill_ids)
            self._insert_round_trips(run_id, result, fill_ids, signal_ids)
            self._insert_cash_and_positions(
                run_id=run_id,
                result=result,
                fill_ids=fill_ids,
                currency_code=base_currency_code,
            )
            self._insert_valuations(run_id, result)
            self._insert_summary(run_id, summary)

    def _insert_decisions(self, run_id: int, result: SimulationResult) -> dict[int, int]:
        context_ids: dict[int, int] = {}
        for decision in result.decisions:
            context_id = int(
                self._connection.execute(
                    text(
                        """
                        INSERT INTO backtest.decision_context
                            (run_id, decision_seq, decision_ts, strategy_state_sha256,
                             input_manifest_sha256, metadata)
                        VALUES
                            (:run_id, :decision_seq, :decision_ts, :state_hash, :input_hash,
                             CAST(:metadata AS jsonb))
                        RETURNING decision_context_id
                        """
                    ),
                    {
                        "run_id": run_id,
                        "decision_seq": decision.sequence,
                        "decision_ts": decision.decision_ts,
                        "state_hash": decision.state_sha256,
                        "input_hash": decision.input_manifest_sha256,
                        "metadata": _json({"trigger_bar": _bar_payload(decision.trigger_bar)}),
                    },
                ).scalar_one()
            )
            context_ids[decision.sequence] = context_id
            last_input = len(decision.inputs) - 1
            for input_no, bar in enumerate(decision.inputs):
                self._connection.execute(
                    text(
                        """
                        INSERT INTO backtest.decision_bar_input
                            (decision_context_id, input_no, input_role, bar_open_ts,
                             bar_series_id, bar_revision_no, bar_available_at,
                             effective_available_at)
                        VALUES
                            (:context_id, :input_no, :input_role, :bar_open_ts,
                             :bar_series_id, :revision_no, :available_at,
                             :effective_available_at)
                        """
                    ),
                    {
                        "context_id": context_id,
                        "input_no": input_no,
                        "input_role": "SIGNAL" if input_no == last_input else "WARMUP",
                        "bar_open_ts": bar.bar_open_ts,
                        "bar_series_id": bar.bar_series_id,
                        "revision_no": bar.revision_no,
                        "available_at": bar.available_at,
                        "effective_available_at": bar.effective_available_at,
                    },
                )
        return context_ids

    def _insert_signals(
        self,
        run_id: int,
        result: SimulationResult,
        context_ids: Mapping[int, int],
    ) -> dict[int, int]:
        signal_ids: dict[int, int] = {}
        for signal in result.signals:
            current_target = Decimal("0")
            signal_type = "ENTRY" if signal.target_quantity > current_target else "EXIT"
            direction = "LONG" if signal.target_quantity > current_target else "FLAT"
            signal_ids[signal.decision_sequence] = int(
                self._connection.execute(
                    text(
                        """
                        INSERT INTO backtest.signal
                            (run_id, instrument_id, signal_ts, signal_type, direction,
                             target_quantity, reason_code, payload, decision_context_id)
                        VALUES
                            (:run_id, :instrument_id, :signal_ts, :signal_type, :direction,
                             :target_quantity, :reason_code, CAST(:payload AS jsonb), :context_id)
                        RETURNING signal_id
                        """
                    ),
                    {
                        "run_id": run_id,
                        "instrument_id": signal.instrument_id,
                        "signal_ts": signal.signal_ts,
                        "signal_type": signal_type,
                        "direction": direction,
                        "target_quantity": signal.target_quantity,
                        "reason_code": signal.reason_code,
                        "payload": _json({"trigger_bar": _bar_payload(signal.trigger_bar)}),
                        "context_id": context_ids[signal.decision_sequence],
                    },
                ).scalar_one()
            )
        return signal_ids

    def _insert_orders(
        self,
        run_id: int,
        result: SimulationResult,
        signal_ids: Mapping[int, int],
    ) -> dict[str, int]:
        filled_keys = {fill.order.semantic_key for fill in result.fills}
        order_ids: dict[str, int] = {}
        for order in result.orders:
            status = "FILLED" if order.semantic_key in filled_keys else "REJECTED"
            order_ids[order.semantic_key] = int(
                self._connection.execute(
                    text(
                        """
                        INSERT INTO backtest.bt_order
                            (run_id, signal_id, instrument_id, client_order_key, submitted_at,
                             side, order_type, time_in_force, quantity, status, reject_reason,
                             metadata)
                        VALUES
                            (:run_id, :signal_id, :instrument_id, :client_order_key, :submitted_at,
                             :side, 'MARKET', 'DAY', :quantity, :status, :reject_reason,
                             CAST(:metadata AS jsonb))
                        RETURNING order_id
                        """
                    ),
                    {
                        "run_id": run_id,
                        "signal_id": signal_ids[order.signal.decision_sequence],
                        "instrument_id": order.signal.instrument_id,
                        "client_order_key": order.semantic_key,
                        "submitted_at": order.submitted_at,
                        "side": order.side,
                        "quantity": order.quantity,
                        "status": status,
                        "reject_reason": order.rejected_reason
                        or (None if status == "FILLED" else "INSUFFICIENT_CASH"),
                        "metadata": _json({"target_quantity": order.signal.target_quantity}),
                    },
                ).scalar_one()
            )
        return order_ids

    def _insert_fills(
        self,
        *,
        run_id: int,
        result: SimulationResult,
        order_ids: Mapping[str, int],
        currency_code: str,
    ) -> dict[str, int]:
        fill_ids: dict[str, int] = {}
        for fill in result.fills:
            fill_id = int(
                self._connection.execute(
                    text(
                        """
                        INSERT INTO backtest.fill
                            (run_id, order_id, instrument_id, fill_ts, price, quantity,
                             commission_amount, slippage_amount, tax_amount, fee_currency_code,
                             liquidity_flag, execution_reference, execution_key)
                        VALUES
                            (:run_id, :order_id, :instrument_id, :fill_ts, :price, :quantity,
                             :commission_amount, :slippage_amount, :tax_amount, :currency_code,
                             'U', CAST(:execution_reference AS jsonb), :execution_key)
                        RETURNING fill_id
                        """
                    ),
                    {
                        "run_id": run_id,
                        "order_id": order_ids[fill.order.semantic_key],
                        "instrument_id": fill.order.signal.instrument_id,
                        "fill_ts": fill.fill_ts,
                        "price": fill.economics.execution_price,
                        "quantity": fill.economics.quantity,
                        "commission_amount": fill.economics.commission_amount,
                        "slippage_amount": fill.economics.slippage_amount,
                        "tax_amount": fill.economics.tax_amount,
                        "currency_code": currency_code,
                        "execution_reference": _json(_bar_payload(fill.reference_bar)),
                        "execution_key": fill.order.semantic_key,
                    },
                ).scalar_one()
            )
            fill_ids[fill.order.semantic_key] = fill_id
            bar = fill.reference_bar
            self._connection.execute(
                text(
                    """
                    INSERT INTO backtest.fill_market_reference
                        (fill_id, run_id, instrument_id, reference_no, reference_role,
                         reference_type, bar_open_ts, bar_series_id, bar_revision_no,
                         bar_available_at, effective_available_at, metadata)
                    VALUES
                        (:fill_id, :run_id, :instrument_id, 0, 'PRICE', 'BAR',
                         :bar_open_ts, :bar_series_id, :revision_no, :available_at,
                         :effective_available_at, CAST(:metadata AS jsonb))
                    """
                ),
                {
                    "fill_id": fill_id,
                    "run_id": run_id,
                    "instrument_id": fill.order.signal.instrument_id,
                    "bar_open_ts": bar.bar_open_ts,
                    "bar_series_id": bar.bar_series_id,
                    "revision_no": bar.revision_no,
                    "available_at": bar.available_at,
                    "effective_available_at": bar.effective_available_at,
                    "metadata": "{}",
                },
            )
        return fill_ids

    def _insert_round_trips(
        self,
        run_id: int,
        result: SimulationResult,
        fill_ids: Mapping[str, int],
        signal_ids: Mapping[int, int],
    ) -> None:
        open_trades: dict[int, tuple[int, SimulatedFill]] = {}
        for fill in result.fills:
            instrument_id = fill.order.signal.instrument_id
            fill_id = fill_ids[fill.order.semantic_key]
            if fill.economics.side == "BUY":
                trade_id = int(
                    self._connection.execute(
                        text(
                            """
                            INSERT INTO backtest.round_trip_trade
                                (run_id, instrument_id, entry_signal_id, direction, entry_ts,
                                 quantity, average_entry_price, commission_amount,
                                 slippage_amount, tax_amount, status)
                            VALUES
                                (:run_id, :instrument_id, :entry_signal_id, 'LONG', :entry_ts,
                                 :quantity, :entry_price, :commission_amount,
                                 :slippage_amount, :tax_amount, 'OPEN')
                            RETURNING trade_id
                            """
                        ),
                        {
                            "run_id": run_id,
                            "instrument_id": instrument_id,
                            "entry_signal_id": signal_ids[fill.order.signal.decision_sequence],
                            "entry_ts": fill.fill_ts,
                            "quantity": fill.economics.quantity,
                            "entry_price": fill.economics.execution_price,
                            "commission_amount": fill.economics.commission_amount,
                            "slippage_amount": fill.economics.slippage_amount,
                            "tax_amount": fill.economics.tax_amount,
                        },
                    ).scalar_one()
                )
                open_trades[instrument_id] = (trade_id, fill)
                self._insert_trade_fill_allocation(
                    trade_id, fill_id, "ENTRY", fill.economics.quantity
                )
                continue
            open_trade = open_trades.pop(instrument_id, None)
            if open_trade is None:
                raise ValueError("Reference long-only sell has no open round-trip trade.")
            trade_id, entry = open_trade
            entry_cost = (
                entry.economics.commission_amount
                + entry.economics.slippage_amount
                + entry.economics.tax_amount
            )
            exit_cost = (
                fill.economics.commission_amount
                + fill.economics.slippage_amount
                + fill.economics.tax_amount
            )
            gross = (
                fill.economics.execution_price - entry.economics.execution_price
            ) * fill.economics.quantity
            net = gross - entry_cost - exit_cost
            self._connection.execute(
                text(
                    """
                    UPDATE backtest.round_trip_trade
                    SET exit_signal_id = :exit_signal_id, exit_ts = :exit_ts,
                        average_exit_price = :exit_price, gross_pnl = :gross_pnl,
                        commission_amount = commission_amount + :commission_amount,
                        slippage_amount = slippage_amount + :slippage_amount,
                        tax_amount = tax_amount + :tax_amount, net_pnl = :net_pnl,
                        return_fraction = :return_fraction, status = 'CLOSED'
                    WHERE trade_id = :trade_id AND status = 'OPEN'
                    """
                ),
                {
                    "trade_id": trade_id,
                    "exit_signal_id": signal_ids[fill.order.signal.decision_sequence],
                    "exit_ts": fill.fill_ts,
                    "exit_price": fill.economics.execution_price,
                    "gross_pnl": gross,
                    "commission_amount": fill.economics.commission_amount,
                    "slippage_amount": fill.economics.slippage_amount,
                    "tax_amount": fill.economics.tax_amount,
                    "net_pnl": net,
                    "return_fraction": float(
                        net / (entry.economics.execution_price * entry.economics.quantity)
                    ),
                },
            )
            self._insert_trade_fill_allocation(trade_id, fill_id, "EXIT", fill.economics.quantity)

    def _insert_trade_fill_allocation(
        self, trade_id: int, fill_id: int, leg_type: str, quantity: Decimal
    ) -> None:
        self._connection.execute(
            text(
                """
                INSERT INTO backtest.trade_fill_allocation
                    (trade_id, fill_id, leg_type, allocated_quantity)
                VALUES (:trade_id, :fill_id, :leg_type, :quantity)
                """
            ),
            {"trade_id": trade_id, "fill_id": fill_id, "leg_type": leg_type, "quantity": quantity},
        )

    def _insert_order_events(
        self,
        run_id: int,
        result: SimulationResult,
        order_ids: Mapping[str, int],
        fill_ids: Mapping[str, int],
    ) -> None:
        for order in result.orders:
            order_id = order_ids[order.semantic_key]
            events = [(1, "SUBMITTED", "ACCEPTED"), (2, "ACCEPTED", "ACCEPTED")]
            if order.semantic_key in fill_ids:
                events.append((3, "FILLED", "FILLED"))
            else:
                events.append((3, "REJECTED", "REJECTED"))
            for sequence, event_type, status_after in events:
                self._connection.execute(
                    text(
                        """
                        INSERT INTO backtest.order_event
                            (run_id, order_id, instrument_id, event_seq, event_key, event_ts,
                             event_type, status_after, source_fill_id, filled_quantity_delta,
                             remaining_quantity, event_price, reason_code, payload)
                        VALUES
                            (:run_id, :order_id, :instrument_id, :event_seq, :event_key, :event_ts,
                             :event_type, :status_after, :source_fill_id, :filled_quantity_delta,
                             :remaining_quantity, :event_price, :reason_code,
                             CAST(:payload AS jsonb))
                        """
                    ),
                    {
                        "run_id": run_id,
                        "order_id": order_id,
                        "instrument_id": order.signal.instrument_id,
                        "event_seq": sequence,
                        "event_key": f"{order.semantic_key}|{sequence}",
                        "event_ts": order.execution_bar.effective_available_at
                        if sequence == 3 and order.execution_bar is not None
                        else order.submitted_at,
                        "event_type": event_type,
                        "status_after": status_after,
                        "source_fill_id": (
                            fill_ids[order.semantic_key] if event_type == "FILLED" else None
                        ),
                        "filled_quantity_delta": order.quantity if event_type == "FILLED" else None,
                        "remaining_quantity": Decimal("0")
                        if event_type == "FILLED"
                        else order.quantity,
                        "event_price": order.execution_bar.close_price
                        if event_type == "FILLED" and order.execution_bar is not None
                        else None,
                        "reason_code": order.rejected_reason if event_type == "REJECTED" else None,
                        "payload": "{}",
                    },
                )

    def _insert_cash_and_positions(
        self,
        *,
        run_id: int,
        result: SimulationResult,
        fill_ids: Mapping[str, int],
        currency_code: str,
    ) -> None:
        for fill in result.fills:
            fill_id = fill_ids[fill.order.semantic_key]
            sign = Decimal("-1") if fill.economics.side == "BUY" else Decimal("1")
            self._insert_cash(
                run_id=run_id,
                entry_ts=fill.fill_ts,
                currency_code=currency_code,
                entry_type="TRADE_NOTIONAL",
                amount=sign * fill.economics.notional,
                source_key=f"{fill.order.semantic_key}|notional",
                fill_id=fill_id,
            )
            self._insert_cash(
                run_id=run_id,
                entry_ts=fill.fill_ts,
                currency_code=currency_code,
                entry_type="COMMISSION",
                amount=-fill.economics.commission_amount,
                source_key=f"{fill.order.semantic_key}|commission",
                fill_id=fill_id,
            )
            if fill.economics.tax_amount:
                self._insert_cash(
                    run_id=run_id,
                    entry_ts=fill.fill_ts,
                    currency_code=currency_code,
                    entry_type="TAX",
                    amount=-fill.economics.tax_amount,
                    source_key=f"{fill.order.semantic_key}|tax",
                    fill_id=fill_id,
                )
            self._connection.execute(
                text(
                    """
                    INSERT INTO backtest.position_ledger
                        (run_id, instrument_id, event_ts, entry_type, quantity_delta,
                         unit_cost_base, fill_id, source_key, metadata)
                    VALUES
                        (:run_id, :instrument_id, :event_ts, 'FILL', :quantity_delta,
                         :unit_cost_base, :fill_id, :source_key, CAST(:metadata AS jsonb))
                    """
                ),
                {
                    "run_id": run_id,
                    "instrument_id": fill.order.signal.instrument_id,
                    "event_ts": fill.fill_ts,
                    "quantity_delta": fill.economics.quantity
                    if fill.economics.side == "BUY"
                    else -fill.economics.quantity,
                    "unit_cost_base": fill.economics.execution_price,
                    "fill_id": fill_id,
                    "source_key": f"{fill.order.semantic_key}|position",
                    "metadata": "{}",
                },
            )

    def _insert_cash(
        self,
        *,
        run_id: int,
        entry_ts: datetime,
        currency_code: str,
        entry_type: str,
        amount: Decimal,
        source_key: str,
        fill_id: int | None = None,
    ) -> None:
        self._connection.execute(
            text(
                """
                INSERT INTO backtest.cash_ledger
                    (run_id, entry_ts, currency_code, entry_type, amount, fill_id,
                     source_key, metadata)
                VALUES
                    (:run_id, :entry_ts, :currency_code, :entry_type, :amount, :fill_id,
                     :source_key, CAST(:metadata AS jsonb))
                """
            ),
            {
                "run_id": run_id,
                "entry_ts": entry_ts,
                "currency_code": currency_code,
                "entry_type": entry_type,
                "amount": amount,
                "fill_id": fill_id,
                "source_key": source_key,
                "metadata": "{}",
            },
        )

    def _insert_valuations(self, run_id: int, result: SimulationResult) -> None:
        for valuation in result.valuations:
            self._connection.execute(
                text(
                    """
                    INSERT INTO backtest.position_snapshot
                        (run_id, instrument_id, snapshot_ts, quantity, average_cost,
                         market_price, market_value_base, realized_pnl_base,
                         unrealized_pnl_base, margin_used_base)
                    VALUES
                        (:run_id, :instrument_id, :snapshot_ts, :quantity, :average_cost,
                         :market_price, :market_value_base, :realized_pnl_base,
                         :unrealized_pnl_base, 0)
                    """
                ),
                {
                    "run_id": run_id,
                    "instrument_id": valuation.instrument_id,
                    "snapshot_ts": valuation.snapshot_ts,
                    "quantity": valuation.quantity,
                    "average_cost": valuation.average_cost,
                    "market_price": valuation.reference_bar.close_price,
                    "market_value_base": valuation.market_value,
                    "realized_pnl_base": valuation.realized_pnl,
                    "unrealized_pnl_base": valuation.unrealized_pnl,
                },
            )
            bar = valuation.reference_bar
            self._connection.execute(
                text(
                    """
                    INSERT INTO backtest.position_valuation_bar_reference
                        (run_id, instrument_id, snapshot_ts, bar_open_ts, bar_series_id,
                         bar_revision_no, bar_available_at, effective_available_at, price_field)
                    VALUES
                        (:run_id, :instrument_id, :snapshot_ts, :bar_open_ts, :bar_series_id,
                         :revision_no, :available_at, :effective_available_at, 'CLOSE')
                    """
                ),
                {
                    "run_id": run_id,
                    "instrument_id": valuation.instrument_id,
                    "snapshot_ts": valuation.snapshot_ts,
                    "bar_open_ts": bar.bar_open_ts,
                    "bar_series_id": bar.bar_series_id,
                    "revision_no": bar.revision_no,
                    "available_at": bar.available_at,
                    "effective_available_at": bar.effective_available_at,
                },
            )
        snapshot_ts = (
            result.valuations[0].snapshot_ts if result.valuations else _first_event_ts(result)
        )
        gross_exposure = sum((item.market_value for item in result.valuations), Decimal("0"))
        self._connection.execute(
            text(
                """
                INSERT INTO backtest.equity_point
                    (run_id, event_ts, cash_base, equity_base, gross_exposure_base,
                     net_exposure_base, drawdown_fraction)
                VALUES
                    (:run_id, :event_ts, :cash_base, :equity_base, :gross_exposure_base,
                     :net_exposure_base, 0)
                """
            ),
            {
                "run_id": run_id,
                "event_ts": snapshot_ts,
                "cash_base": result.cash,
                "equity_base": result.equity_base,
                "gross_exposure_base": gross_exposure,
                "net_exposure_base": gross_exposure,
            },
        )

    def _insert_summary(self, run_id: int, summary: Mapping[str, object]) -> None:
        self._connection.execute(
            text(
                """
                INSERT INTO backtest.run_summary
                    (run_id, total_return, max_drawdown, trade_count, winning_trade_count,
                     losing_trade_count, win_rate, gross_pnl_base, net_pnl_base, total_cost_base,
                     calculation_version, annualization_basis)
                VALUES
                    (:run_id, :total_return, :max_drawdown, :trade_count, :winning_trade_count,
                     :losing_trade_count, :win_rate, :gross_pnl_base, :net_pnl_base,
                     :total_cost_base, 'reference-v1', CAST(:annualization_basis AS jsonb))
                """
            ),
            {"run_id": run_id, "annualization_basis": "{}", **summary},
        )


def _first_event_ts(result: SimulationResult) -> datetime:
    if result.decisions:
        return result.decisions[0].decision_ts
    if result.fills:
        return result.fills[0].fill_ts
    raise ValueError("Reference simulation must contain at least one decision or fill.")


def _bar_payload(bar: object) -> dict[str, object]:
    return {
        "bar_open_ts": getattr(bar, "bar_open_ts"),
        "bar_series_id": getattr(bar, "bar_series_id"),
        "revision_no": getattr(bar, "revision_no"),
        "available_at": getattr(bar, "available_at"),
        "effective_available_at": getattr(bar, "effective_available_at"),
    }


def _json(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


__all__ = ["SqlAlchemyBacktestLedgerRepository"]
