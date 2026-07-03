"""SEC EDGAR fundamentals — keyless fallback behind PROVIDER_FUNDAMENTALS=edgar.

Survival path if FMP's free tier degrades. Limitations (documented, accepted):
- US SEC filers only (10-K annual frames)
- no market-priced ratios (pe/ps/ev_ebitda stay None — they need a quote)
- margins/roe/d2e computed from raw us-gaap facts where present

SEC requires a User-Agent identifying the caller (anonymous UAs are rejected) and
caps clients at 10 req/s (PROVIDER_LIMITS["edgar"] = 8/s).
"""

from app.providers.base import ProviderError
from app.providers.http import RateLimitedClient
from app.schemas.fundamentals import FundamentalsPeriod

BASE_URL = "https://data.sec.gov"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
USER_AGENT = "plutus/0.1 (santiago.coronado94@gmail.com)"

# us-gaap concept candidates per normalized field (first hit wins)
CONCEPTS = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "eps": ("EarningsPerShareDiluted", "EarningsPerShareBasic"),
    "net_income": ("NetIncomeLoss",),
    "gross_profit": ("GrossProfit",),
    "equity": ("StockholdersEquity",),
    "liabilities": ("Liabilities",),
    "operating_cashflow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
}


class EdgarProvider:
    name = "edgar"

    def __init__(self, client: RateLimitedClient, ticker_client: RateLimitedClient) -> None:
        self._client = client  # data.sec.gov
        self._tickers = ticker_client  # www.sec.gov (ticker map lives on the www host)

    def _cik_for(self, symbol: str) -> int:
        mapping = self._tickers.get_json("/files/company_tickers.json", cache_ttl=24 * 3600)
        for entry in mapping.values():
            if entry.get("ticker", "").upper() == symbol.upper():
                return int(entry["cik_str"])
        raise ProviderError(f"edgar: no CIK found for {symbol}")

    def _annual_facts(self, facts: dict, concepts: tuple[str, ...]) -> dict[str, float]:
        """fiscal-year-end date -> value for the first concept that has 10-K frames."""
        gaap = facts.get("facts", {}).get("us-gaap", {})
        for concept in concepts:
            units = gaap.get(concept, {}).get("units", {})
            for unit_values in units.values():
                annual = {
                    row["end"]: float(row["val"])
                    for row in unit_values
                    if row.get("form") == "10-K" and row.get("fp") == "FY" and "val" in row
                }
                if annual:
                    return annual
        return {}

    def get_fundamentals(
        self, symbol: str, period: str = "annual", limit: int = 6
    ) -> list[FundamentalsPeriod]:
        cik = self._cik_for(symbol)
        facts = self._client.get_json(
            f"/api/xbrl/companyfacts/CIK{cik:010d}.json", cache_ttl=24 * 3600
        )
        series = {name: self._annual_facts(facts, concepts) for name, concepts in CONCEPTS.items()}
        report_dates = sorted(series["revenue"] or series["net_income"], reverse=True)[:limit]

        periods = []
        for report_date in report_dates:
            revenue = series["revenue"].get(report_date)
            net_income = series["net_income"].get(report_date)
            gross_profit = series["gross_profit"].get(report_date)
            equity = series["equity"].get(report_date)
            liabilities = series["liabilities"].get(report_date)
            ocf = series["operating_cashflow"].get(report_date)
            capex = series["capex"].get(report_date)
            periods.append(
                FundamentalsPeriod(
                    period="annual",
                    report_date=report_date,
                    fiscal_year=int(report_date[:4]),
                    revenue=revenue,
                    eps=series["eps"].get(report_date),
                    fcf=(ocf - capex) if ocf is not None and capex is not None else None,
                    gross_margin=(gross_profit / revenue) if gross_profit and revenue else None,
                    net_margin=(net_income / revenue) if net_income and revenue else None,
                    roe=(net_income / equity) if net_income and equity else None,
                    debt_to_equity=(liabilities / equity) if liabilities and equity else None,
                    # market-priced ratios need a quote — out of EDGAR's scope
                    pe=None,
                    ps=None,
                    ev_ebitda=None,
                    metrics={"source": "edgar-companyfacts", "cik": cik},
                )
            )
        return periods

    def get_profile(self, symbol: str) -> dict:
        return {}  # EDGAR has no market-cap/sector profile endpoint
