"""EDGAR fact extraction — the defects found when validating a live switch.

A live run of the old adapter over all 102 tracked stocks produced materially
wrong fundamentals for ~15% of them, all silently plausible:

  HON    revenue  $9.8B   (actual $37.4B — the 10-K's Q4 column read as annual)
  ACN    revenue  $17.6B  (actual $69.7B — same cause)
  DUK    dated 2012       (locked onto a concept the filer abandoned)
  BA/LMT dated 2019       (current rows excluded by the fp == "FY" filter)
  XOM    nothing at all   (ticker resolves to a holdco with no XBRL history)

Each class of failure gets a test here so a regression is loud rather than
plausible.
"""

import pytest

from app.providers.base import ProviderError
from app.providers.edgar import (
    ANNUAL_SPAN_DAYS,
    CONCEPTS,
    SYMBOL_OVERRIDES,
    EdgarProvider,
    _covers_fiscal_year,
)


def row(val, *, start=None, end="2025-12-31", form="10-K", filed="2026-02-17", fp="FY"):
    out = {"val": val, "end": end, "form": form, "filed": filed, "fp": fp}
    if start is not None:
        out["start"] = start
    return out


def facts_for(concept_rows: dict) -> dict:
    return {
        "facts": {
            "us-gaap": {
                name: {"units": {"USD": rows}} for name, rows in concept_rows.items()
            }
        }
    }


class TestCoversFiscalYear:
    def test_full_year_duration_qualifies(self):
        assert _covers_fiscal_year("2025-01-01", "2025-12-31") is True

    def test_quarter_does_not(self):
        # the Honeywell bug: a Q4 column inside the 10-K
        assert _covers_fiscal_year("2025-10-01", "2025-12-31") is False

    def test_instant_fact_qualifies(self):
        # balance-sheet items carry no start and are point-in-time
        assert _covers_fiscal_year(None, "2025-12-31") is True

    @pytest.mark.parametrize("days", [ANNUAL_SPAN_DAYS[0], ANNUAL_SPAN_DAYS[1]])
    def test_span_boundaries_are_inclusive(self, days):
        from datetime import date, timedelta

        end = date(2025, 12, 31)
        assert _covers_fiscal_year(str(end - timedelta(days=days)), str(end)) is True

    def test_53_week_retail_year_qualifies(self):
        assert _covers_fiscal_year("2025-02-02", "2026-02-01") is True

    def test_malformed_dates_are_rejected_not_raised(self):
        assert _covers_fiscal_year("not-a-date", "2025-12-31") is False


class TestConceptAnnuals:
    def test_quarter_does_not_overwrite_the_annual_figure(self):
        """Honeywell, exactly: both rows share an end date and form."""
        gaap = facts_for(
            {
                "Revenues": [
                    row(37_442_000_000, start="2025-01-01"),
                    row(9_758_000_000, start="2025-10-01"),  # Q4, listed last
                ]
            }
        )["facts"]["us-gaap"]
        assert EdgarProvider._concept_annuals(gaap, "Revenues") == {
            "2025-12-31": 37_442_000_000
        }

    def test_ignores_non_10k_forms(self):
        gaap = facts_for(
            {"Revenues": [row(1, start="2025-01-01", form="10-Q")]}
        )["facts"]["us-gaap"]
        assert EdgarProvider._concept_annuals(gaap, "Revenues") == {}

    def test_fp_is_not_filtered_on(self):
        """Boeing/Lockheed/Mastercard/Alphabet carry a non-FY marker on current
        rows; requiring fp == 'FY' pinned them to figures years out of date."""
        gaap = facts_for(
            {"Revenues": [row(89_463_000_000, start="2025-01-01", fp="CY")]}
        )["facts"]["us-gaap"]
        assert EdgarProvider._concept_annuals(gaap, "Revenues") == {
            "2025-12-31": 89_463_000_000
        }

    def test_later_filing_wins_a_restated_period(self):
        gaap = facts_for(
            {
                "Revenues": [
                    row(100, start="2025-01-01", filed="2026-02-01"),
                    row(110, start="2025-01-01", filed="2027-02-01"),  # restated
                ]
            }
        )["facts"]["us-gaap"]
        assert EdgarProvider._concept_annuals(gaap, "Revenues") == {"2025-12-31": 110}

    def test_earlier_filing_does_not_clobber_a_later_one(self):
        gaap = facts_for(
            {
                "Revenues": [
                    row(110, start="2025-01-01", filed="2027-02-01"),
                    row(100, start="2025-01-01", filed="2026-02-01"),  # older, listed last
                ]
            }
        )["facts"]["us-gaap"]
        assert EdgarProvider._concept_annuals(gaap, "Revenues") == {"2025-12-31": 110}


class TestAnnualFactsMergesPerDate:
    def test_abandoned_concept_cannot_shadow_the_current_one(self):
        """Duke: the first candidate stops in 2012, the real figure is elsewhere."""
        provider = EdgarProvider(None, None)
        facts = facts_for(
            {
                "Revenues": [row(5_695_000_000, start="2012-01-01", end="2012-12-31")],
                "RevenueFromContractWithCustomerIncludingAssessedTax": [
                    row(31_741_000_000, start="2025-01-01")
                ],
            }
        )
        merged = provider._annual_facts(facts, CONCEPTS["revenue"])
        assert merged["2025-12-31"] == 31_741_000_000
        assert merged["2012-12-31"] == 5_695_000_000  # history is kept, not discarded

    def test_preference_order_decides_when_both_report_a_date(self):
        provider = EdgarProvider(None, None)
        facts = facts_for(
            {
                "RevenueFromContractWithCustomerExcludingAssessedTax": [
                    row(100, start="2025-01-01")
                ],
                "Revenues": [row(999, start="2025-01-01")],
            }
        )
        merged = provider._annual_facts(facts, CONCEPTS["revenue"])
        assert merged["2025-12-31"] == 100  # first candidate wins the tie

    def test_missing_concepts_yield_empty_not_error(self):
        assert EdgarProvider(None, None)._annual_facts({}, CONCEPTS["revenue"]) == {}


class TestSymbolOverrides:
    def test_explicit_cik_bypasses_the_ticker_map(self):
        provider = EdgarProvider(None, None)  # no client: must not be consulted
        assert provider._cik_for("CIK0000034088") == 34088

    def test_xom_routes_to_the_filing_entity(self):
        # the SEC map points XOM at a holdco with zero us-gaap facts
        assert provider_cik(SYMBOL_OVERRIDES["XOM"]) == 34088

    def test_overrides_are_applied_before_lookup(self):
        assert SYMBOL_OVERRIDES["MMC"] == "MRSH"  # filed under a different ticker


def provider_cik(value: str) -> int:
    return EdgarProvider(None, None)._cik_for(value)


class TestEmptyEntityFailsLoudly:
    def test_no_us_gaap_facts_raises(self):
        class FakeClient:
            def get_json(self, *a, **k):
                return {"entityName": "ExxonMobil Holdings Corp", "facts": {}}

        provider = EdgarProvider(FakeClient(), None)
        with pytest.raises(ProviderError, match="no us-gaap facts"):
            provider.get_fundamentals("CIK0002115436")

    def test_error_names_the_remedy(self):
        class FakeClient:
            def get_json(self, *a, **k):
                return {"entityName": "Shell Co", "facts": {}}

        with pytest.raises(ProviderError, match="SYMBOL_OVERRIDES"):
            EdgarProvider(FakeClient(), None).get_fundamentals("CIK0000000001")


class TestDerivedFields:
    """Fallbacks that lift coverage where a filer skips the tagged subtotal."""

    def _provider_with(self, concept_rows):
        payload = facts_for(concept_rows)

        class FakeClient:
            def get_json(self, *a, **k):
                return payload

        return EdgarProvider(FakeClient(), None)

    def test_liabilities_fall_back_to_assets_minus_equity(self):
        provider = self._provider_with(
            {
                "Revenues": [row(1000, start="2025-01-01")],
                "StockholdersEquity": [row(400)],
                "Assets": [row(1000)],
            }
        )
        period = provider.get_fundamentals("CIK0000000001")[0]
        assert period.debt_to_equity == pytest.approx((1000 - 400) / 400)

    def test_tagged_liabilities_win_over_the_identity(self):
        provider = self._provider_with(
            {
                "Revenues": [row(1000, start="2025-01-01")],
                "StockholdersEquity": [row(400)],
                "Liabilities": [row(550)],
                "Assets": [row(1000)],
            }
        )
        period = provider.get_fundamentals("CIK0000000001")[0]
        assert period.debt_to_equity == pytest.approx(550 / 400)

    def test_gross_profit_falls_back_to_revenue_minus_cost(self):
        provider = self._provider_with(
            {
                "Revenues": [row(1000, start="2025-01-01")],
                "CostOfRevenue": [row(600, start="2025-01-01")],
            }
        )
        period = provider.get_fundamentals("CIK0000000001")[0]
        assert period.gross_margin == pytest.approx(0.4)

    def test_tagged_gross_profit_wins_over_the_derivation(self):
        provider = self._provider_with(
            {
                "Revenues": [row(1000, start="2025-01-01")],
                "GrossProfit": [row(350, start="2025-01-01")],
                "CostOfRevenue": [row(600, start="2025-01-01")],
            }
        )
        period = provider.get_fundamentals("CIK0000000001")[0]
        assert period.gross_margin == pytest.approx(0.35)


class TestCompanyfactsIsNotCached:
    def test_no_cache_ttl_is_passed(self):
        """5-10MB per symbol x ~100 symbols parked 24h in the Redis that also
        carries the Celery broker. The weekly cadence never reads it back."""
        seen = {}

        class FakeClient:
            def get_json(self, path, params=None, **kwargs):
                seen[path] = kwargs
                return facts_for({"Revenues": [row(1, start="2025-01-01")]})

        EdgarProvider(FakeClient(), None).get_fundamentals("CIK0000000001")
        companyfacts = next(p for p in seen if "companyfacts" in p)
        assert seen[companyfacts].get("cache_ttl") is None
