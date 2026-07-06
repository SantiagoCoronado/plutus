"""Crossing-edge matrix for the price-alert evaluator.

should_fire is pure (no DB), so the whole matrix lives here: first-observation
baseline, above/below, exact-equality boundaries, and flap sequences. The sync
quote reader is exercised against fakeredis; the DB-backed evaluate_alerts path
is covered in tests/integration/test_alert_evaluator.py.
"""

import json
from decimal import Decimal

from app.alerts.evaluate import _fmt_amount, should_fire
from app.quotes.publisher import read_last_quotes_sync


def D(value) -> Decimal:
    return Decimal(str(value))


class TestFirstObservation:
    def test_none_last_price_never_fires_above(self):
        # the re-baseline contract: a fresh/re-armed rule records, never fires,
        # even when the current price is already well past the threshold
        assert should_fire("above", D(100), None, D(500)) is False

    def test_none_last_price_never_fires_below(self):
        assert should_fire("below", D(100), None, D(1)) is False


class TestAbove:
    def test_crosses_up_fires(self):
        assert should_fire("above", D(100), D(99), D(101)) is True

    def test_last_exactly_at_threshold_then_beyond_fires(self):
        # last side is inclusive (<=): sitting exactly on the line still crosses
        assert should_fire("above", D(100), D(100), D(101)) is True

    def test_current_exactly_at_threshold_does_not_fire(self):
        # current side is strict (>): merely touching the threshold is not beyond
        assert should_fire("above", D(100), D(99), D(100)) is False

    def test_already_above_does_not_refire(self):
        # last_price already beyond the threshold -> no new crossing
        assert should_fire("above", D(100), D(105), D(110)) is False

    def test_staying_below_does_not_fire(self):
        assert should_fire("above", D(100), D(90), D(95)) is False


class TestBelow:
    def test_crosses_down_fires(self):
        assert should_fire("below", D(100), D(101), D(99)) is True

    def test_last_exactly_at_threshold_then_beyond_fires(self):
        assert should_fire("below", D(100), D(100), D(99)) is True

    def test_current_exactly_at_threshold_does_not_fire(self):
        assert should_fire("below", D(100), D(101), D(100)) is False

    def test_already_below_does_not_refire(self):
        assert should_fire("below", D(100), D(95), D(90)) is False

    def test_staying_above_does_not_fire(self):
        assert should_fire("below", D(100), D(110), D(105)) is False


class TestUnknownCondition:
    def test_unknown_condition_never_fires(self):
        assert should_fire("sideways", D(100), D(90), D(200)) is False


class TestFlapSequence:
    def test_above_flap_fires_only_on_the_upcross(self):
        # threshold 100; walk a price that flaps around it. Fire only on the
        # first strict up-cross; last_price is threaded like the evaluator does.
        threshold = D(100)
        walk = [D(95), D(101), D(98), D(102)]
        last = None  # first observation baselines silently
        fires = []
        for price in walk:
            fired = should_fire("above", threshold, last, price)
            fires.append(fired)
            last = price  # last_price is always updated afterwards
        # 95 baselines (no fire); 95->101 fires; 101->98 no; 98->102 fires again
        assert fires == [False, True, False, True]

    def test_below_flap_fires_only_on_the_downcross(self):
        threshold = D(100)
        walk = [D(105), D(99), D(102), D(98)]
        last = None
        fires = []
        for price in walk:
            fires.append(should_fire("below", threshold, last, price))
            last = price
        # 105 baselines (no fire); 105->99 fires; 99->102 no; 102->98 fires again
        assert fires == [False, True, False, True]


class TestReadLastQuotesSync:
    def test_returns_uppercased_and_skips_missing(self, fake_redis):
        tick = {"symbol": "BTC", "price": 50000.0, "change_pct": 1.0,
                "ts": "2026-07-06T00:00:00+00:00", "source": "binance"}
        fake_redis.setex("quote:last:BTC", 120, json.dumps(tick))

        result = read_last_quotes_sync(fake_redis, ["btc", "ETH"])

        assert set(result) == {"BTC"}  # ETH has no key -> skipped (stale)
        assert result["BTC"]["price"] == 50000.0

    def test_empty_symbols_returns_empty(self, fake_redis):
        assert read_last_quotes_sync(fake_redis, []) == {}


class TestFmtAmount:
    def test_trims_numeric_trailing_zeros(self):
        assert _fmt_amount(Decimal("120000.00000000")) == "120000"
        assert _fmt_amount(Decimal("120.25000000")) == "120.25"
        assert _fmt_amount(Decimal("0.50000000")) == "0.5"
