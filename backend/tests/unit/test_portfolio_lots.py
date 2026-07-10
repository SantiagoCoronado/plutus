"""Lot engine: FIFO matching, specific-ID, fees, transfers, oversell."""

from datetime import UTC, datetime, timedelta

import pytest

from app.portfolio.lots import (
    LotLinkError,
    OversellError,
    TxnRow,
    average_cost,
    build_lots,
)

T0 = datetime(2026, 1, 5, 12, tzinfo=UTC)


def txn(id, type, quantity, price=None, *, ts=None, fees=0.0, asset_id=1, account_id=1,
        currency="USD", lot_links=None):
    return TxnRow(
        id=id,
        account_id=account_id,
        asset_id=asset_id,
        type=type,
        ts=ts if ts is not None else T0 + timedelta(days=id),
        quantity=quantity,
        price=price,
        fees=fees,
        currency=currency,
        lot_links=lot_links,
    )


class TestFifo:
    def test_partial_sell_spans_lots_oldest_first(self):
        state = build_lots(
            [
                txn(1, "buy", 10, 100.0),
                txn(2, "buy", 10, 200.0),
                txn(3, "sell", 15, 300.0),
            ]
        )
        sale = state.realized[0]
        # 10 @ 100 + 5 @ 200 = 2000 basis; proceeds 15 * 300 = 4500
        assert sale.cost_basis == pytest.approx(2000.0)
        assert sale.proceeds == pytest.approx(4500.0)
        assert sale.realized_pnl == pytest.approx(2500.0)
        remaining = state.open_lots[(1, 1)]
        assert len(remaining) == 1
        assert remaining[0].remaining == pytest.approx(5.0)
        assert remaining[0].cost_per_unit == pytest.approx(200.0)

    def test_buy_fee_capitalized_and_sell_fee_reduces_proceeds(self):
        state = build_lots(
            [
                txn(1, "buy", 10, 100.0, fees=10.0),  # basis (1000+10)/10 = 101
                txn(2, "sell", 10, 110.0, fees=5.0),  # proceeds 1100-5 = 1095
            ]
        )
        sale = state.realized[0]
        assert sale.cost_basis == pytest.approx(1010.0)
        assert sale.proceeds == pytest.approx(1095.0)
        assert sale.realized_pnl == pytest.approx(85.0)

    def test_ordering_is_by_ts_then_id(self):
        # same timestamp: the lower id is the earlier event
        state = build_lots(
            [
                txn(2, "sell", 5, 120.0, ts=T0),
                txn(1, "buy", 5, 100.0, ts=T0),
            ]
        )
        assert not state.warnings
        assert state.realized[0].realized_pnl == pytest.approx(100.0)

    def test_average_cost(self):
        state = build_lots([txn(1, "buy", 10, 100.0), txn(2, "buy", 10, 300.0)])
        assert average_cost(state.open_lots[(1, 1)]) == pytest.approx(200.0)
        assert average_cost([]) is None

    def test_positions_keyed_per_account_and_asset(self):
        state = build_lots(
            [
                txn(1, "buy", 5, 100.0, account_id=1),
                txn(2, "buy", 7, 100.0, account_id=2),
                txn(3, "buy", 3, 50.0, account_id=1, asset_id=9),
            ]
        )
        assert sum(lot.remaining for lot in state.open_lots[(1, 1)]) == 5
        assert sum(lot.remaining for lot in state.open_lots[(2, 1)]) == 7
        assert sum(lot.remaining for lot in state.open_lots[(1, 9)]) == 3


class TestOversell:
    def test_oversell_clamps_and_warns_on_read_path(self):
        state = build_lots([txn(1, "buy", 5, 100.0), txn(2, "sell", 8, 100.0)])
        assert len(state.warnings) == 1
        assert state.warnings[0]["unmatched_quantity"] == pytest.approx(3.0)
        # matched part still realizes
        assert state.realized[0].cost_basis == pytest.approx(500.0)
        assert state.open_lots[(1, 1)] == []

    def test_oversell_raises_in_strict_mode(self):
        with pytest.raises(OversellError):
            build_lots([txn(1, "buy", 5, 100.0), txn(2, "sell", 8, 100.0)], strict=True)

    def test_sell_with_no_position_at_all(self):
        state = build_lots([txn(1, "sell", 3, 100.0)])
        assert state.warnings[0]["unmatched_quantity"] == pytest.approx(3.0)
        assert state.realized == []  # nothing matched, nothing realized


class TestSpecificId:
    def test_lot_links_pick_the_named_lot(self):
        state = build_lots(
            [
                txn(1, "buy", 10, 100.0),
                txn(2, "buy", 10, 200.0),
                # sell the newer, expensive lot on purpose (tax-loss style)
                txn(3, "sell", 10, 150.0, lot_links=[{"buy_transaction_id": 2, "quantity": 10}]),
            ]
        )
        sale = state.realized[0]
        assert sale.cost_basis == pytest.approx(2000.0)
        assert sale.realized_pnl == pytest.approx(-500.0)
        # the old cheap lot is untouched
        assert state.open_lots[(1, 1)][0].buy_transaction_id == 1

    def test_links_across_two_lots(self):
        state = build_lots(
            [
                txn(1, "buy", 10, 100.0),
                txn(2, "buy", 10, 200.0),
                txn(
                    3,
                    "sell",
                    6,
                    150.0,
                    lot_links=[
                        {"buy_transaction_id": 1, "quantity": 2},
                        {"buy_transaction_id": 2, "quantity": 4},
                    ],
                ),
            ]
        )
        assert state.realized[0].cost_basis == pytest.approx(2 * 100 + 4 * 200)

    def test_duplicate_links_to_one_lot_cannot_oversell_in_strict_mode(self):
        # each link alone fits the lot, but the aggregate is double what remains
        txns = [
            txn(1, "buy", 10, 100.0),
            txn(
                2,
                "sell",
                20,
                150.0,
                lot_links=[
                    {"buy_transaction_id": 1, "quantity": 10},
                    {"buy_transaction_id": 1, "quantity": 10},
                ],
            ),
        ]
        with pytest.raises(LotLinkError):
            build_lots(txns, strict=True)

    def test_duplicate_links_within_remaining_merge_into_one_match(self):
        state = build_lots(
            [
                txn(1, "buy", 10, 100.0),
                txn(
                    2,
                    "sell",
                    6,
                    150.0,
                    lot_links=[
                        {"buy_transaction_id": 1, "quantity": 2},
                        {"buy_transaction_id": 1, "quantity": 4},
                    ],
                ),
            ],
            strict=True,
        )
        sale = state.realized[0]
        assert len(sale.matches) == 1
        assert sale.matches[0].quantity == pytest.approx(6.0)
        assert sale.cost_basis == pytest.approx(600.0)
        assert state.open_lots[(1, 1)][0].remaining == pytest.approx(4.0)

    def test_links_across_two_lots_pass_strict_mode(self):
        state = build_lots(
            [
                txn(1, "buy", 10, 100.0),
                txn(2, "buy", 10, 200.0),
                txn(
                    3,
                    "sell",
                    6,
                    150.0,
                    lot_links=[
                        {"buy_transaction_id": 1, "quantity": 2},
                        {"buy_transaction_id": 2, "quantity": 4},
                    ],
                ),
            ],
            strict=True,
        )
        assert state.realized[0].cost_basis == pytest.approx(2 * 100 + 4 * 200)

    def test_invalid_link_falls_back_to_fifo_with_warning(self):
        state = build_lots(
            [
                txn(1, "buy", 10, 100.0),
                txn(2, "sell", 5, 150.0, lot_links=[{"buy_transaction_id": 99, "quantity": 5}]),
            ]
        )
        assert len(state.warnings) == 1
        assert "fell back" in state.warnings[0]["warning"]
        assert state.realized[0].cost_basis == pytest.approx(500.0)  # FIFO matched

    def test_invalid_link_raises_in_strict_mode(self):
        txns = [
            txn(1, "buy", 10, 100.0),
            txn(2, "sell", 5, 150.0, lot_links=[{"buy_transaction_id": 1, "quantity": 20}]),
        ]
        with pytest.raises(LotLinkError):
            build_lots(txns, strict=True)

    def test_links_must_cover_the_whole_quantity(self):
        txns = [
            txn(1, "buy", 10, 100.0),
            txn(2, "sell", 5, 150.0, lot_links=[{"buy_transaction_id": 1, "quantity": 3}]),
        ]
        with pytest.raises(LotLinkError):
            build_lots(txns, strict=True)


class TestTransfers:
    def test_transfer_preserves_basis_without_realizing(self):
        # Bitso buy -> move to Ledger; carried cost travels via transfer_in.price
        state = build_lots(
            [
                txn(1, "buy", 2.0, 30000.0, account_id=1),
                txn(2, "transfer_out", 2.0, account_id=1),
                txn(3, "transfer_in", 2.0, 30000.0, account_id=2),
            ]
        )
        assert state.realized == []  # moving is not selling
        assert state.open_lots[(1, 1)] == []
        ledger_lot = state.open_lots[(2, 1)][0]
        assert ledger_lot.remaining == pytest.approx(2.0)
        assert ledger_lot.cost_per_unit == pytest.approx(30000.0)

    def test_transfer_in_without_price_has_zero_basis(self):
        state = build_lots([txn(1, "transfer_in", 1.5, account_id=2)])
        assert state.open_lots[(2, 1)][0].cost_per_unit == 0.0

    def test_transfer_in_fees_are_not_capitalized(self):
        # network fees on a wallet move are a cost, not basis
        state = build_lots([txn(1, "transfer_in", 2.0, 100.0, fees=50.0)])
        assert state.open_lots[(1, 1)][0].cost_per_unit == pytest.approx(100.0)

    def test_cash_types_do_not_touch_lots(self):
        state = build_lots(
            [
                TxnRow(1, 1, None, "deposit", T0, 1000.0, None, 0.0, "USD"),
                TxnRow(2, 1, None, "fee", T0, 10.0, None, 0.0, "USD"),
                txn(3, "dividend", 25.0, asset_id=1),
            ]
        )
        assert state.open_lots == {}
        assert state.realized == []
