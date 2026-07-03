import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.providers.base import RateLimit
from app.providers.edgar import EdgarProvider
from app.providers.finnhub import BASE_URL as FINNHUB_URL
from app.providers.finnhub import FinnhubNewsProvider
from app.providers.fmp import BASE_URL as FMP_URL
from app.providers.fmp import FMPProvider
from app.providers.http import RateLimitedClient

FIXTURES = Path(__file__).parent.parent / "fixtures"
WIDE_OPEN = RateLimit(capacity=1000, refill_amount=1000, refill_period_s=1)


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


def make_client(base_url, fake_redis, fake_clock):
    return RateLimitedClient(
        "test", base_url, fake_redis, WIDE_OPEN, clock=fake_clock, sleep=fake_clock.sleep
    )


@respx.mock(base_url=FMP_URL)
def test_fmp_field_mapping_contract(respx_mock, fake_redis, fake_clock):
    respx_mock.get("/income-statement").mock(
        return_value=httpx.Response(200, json=load("fmp_income.json"))
    )
    respx_mock.get("/balance-sheet-statement").mock(
        return_value=httpx.Response(200, json=load("fmp_balance.json"))
    )
    respx_mock.get("/cash-flow-statement").mock(
        return_value=httpx.Response(200, json=load("fmp_cashflow.json"))
    )
    respx_mock.get("/ratios").mock(return_value=httpx.Response(200, json=load("fmp_ratios.json")))
    respx_mock.get("/key-metrics").mock(
        return_value=httpx.Response(200, json=load("fmp_key_metrics.json"))
    )

    provider = FMPProvider(make_client(FMP_URL, fake_redis, fake_clock), "test-key")
    periods = provider.get_fundamentals("AAPL")

    assert len(periods) == 2
    latest = periods[0]  # sorted report_date desc
    assert latest.report_date == date(2025, 9, 27)
    assert latest.fiscal_year == 2025
    assert latest.revenue == 416161000000
    assert latest.eps == 7.46  # epsDiluted preferred over eps
    assert latest.fcf == 108807000000
    assert latest.gross_margin == 0.4691
    assert latest.net_margin == 0.2692
    assert latest.roe == 1.4534
    assert latest.debt_to_equity == 1.406
    assert latest.pe == 34.21
    assert latest.ps == 9.21
    assert latest.ev_ebitda == 26.53
    # raw statements preserved for the 5y table
    assert latest.metrics["income"]["netIncome"] == 112010000000
    assert "balance" in latest.metrics and "cashflow" in latest.metrics


@respx.mock(base_url=FMP_URL)
def test_fmp_profile_mapping(respx_mock, fake_redis, fake_clock):
    respx_mock.get("/profile").mock(
        return_value=httpx.Response(200, json=load("fmp_profile.json"))
    )
    provider = FMPProvider(make_client(FMP_URL, fake_redis, fake_clock), "test-key")
    profile = provider.get_profile("AAPL")
    assert profile["market_cap"] == 4561230000000
    assert profile["sector"] == "Technology"


@respx.mock(base_url=FINNHUB_URL)
def test_finnhub_news_mapping(respx_mock, fake_redis, fake_clock):
    respx_mock.get("/company-news").mock(
        return_value=httpx.Response(200, json=load("finnhub_company_news.json"))
    )
    provider = FinnhubNewsProvider(make_client(FINNHUB_URL, fake_redis, fake_clock), "test-key")
    items = provider.get_company_news("AAPL", date(2026, 7, 1), date(2026, 7, 3))

    assert len(items) == 2  # the url-less item is skipped
    first = items[0]
    assert first.ts == datetime.fromtimestamp(1782864000, tz=UTC)
    assert first.source == "Reuters"
    assert first.url == "https://example.com/apple-ai"


@respx.mock
def test_edgar_computes_ratios_from_facts(respx_mock, fake_redis, fake_clock):
    respx_mock.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(
            200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        )
    )

    def frames(concept_values: dict[str, float]):
        return {
            "units": {
                "USD": [
                    {"end": end, "val": val, "form": "10-K", "fp": "FY"}
                    for end, val in concept_values.items()
                ]
            }
        }

    companyfacts = {
        "facts": {
            "us-gaap": {
                "Revenues": frames({"2025-09-27": 400e9, "2024-09-28": 380e9}),
                "NetIncomeLoss": frames({"2025-09-27": 100e9, "2024-09-28": 90e9}),
                "GrossProfit": frames({"2025-09-27": 180e9}),
                "StockholdersEquity": frames({"2025-09-27": 80e9}),
                "Liabilities": frames({"2025-09-27": 280e9}),
                "EarningsPerShareDiluted": frames({"2025-09-27": 6.5}),
                "NetCashProvidedByUsedInOperatingActivities": frames({"2025-09-27": 120e9}),
                "PaymentsToAcquirePropertyPlantAndEquipment": frames({"2025-09-27": 11e9}),
            }
        }
    }
    respx_mock.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").mock(
        return_value=httpx.Response(200, json=companyfacts)
    )

    provider = EdgarProvider(
        make_client("https://data.sec.gov", fake_redis, fake_clock),
        make_client("https://www.sec.gov", fake_redis, fake_clock),
    )
    periods = provider.get_fundamentals("AAPL")

    assert len(periods) == 2
    latest = periods[0]
    assert latest.revenue == 400e9
    assert latest.gross_margin == pytest.approx(180e9 / 400e9)
    assert latest.net_margin == pytest.approx(100e9 / 400e9)
    assert latest.roe == pytest.approx(100e9 / 80e9)
    assert latest.debt_to_equity == pytest.approx(280e9 / 80e9)
    assert latest.fcf == pytest.approx(109e9)
    assert latest.pe is None  # market-priced ratios are out of EDGAR's scope
