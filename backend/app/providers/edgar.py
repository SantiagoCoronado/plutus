"""SEC EDGAR fundamentals — the default source (PROVIDER_FUNDAMENTALS=edgar).

Chosen over FMP because FMP's free tier is entitlement-capped to a symbol
whitelist: it answered HTTP 402 for 73 of the 102 tracked stocks, and no amount
of budget or retry tuning changes that. EDGAR covers every US SEC filer.

Measured over the full tracked universe: 102/102 stocks return data, none stale,
with revenue / roe / net_margin / debt_to_equity complete, eps on 100, fcf on 92
and gross_margin on 62 (the gaps are banks and insurers, where the tagged
subtotals genuinely do not exist). Against the 29 symbols FMP could still see,
the latest period agrees within 2% for 25; the four that differ are revenue
DEFINITION differences (JPM/BAC/WFC report net of interest expense, XOM includes
sales-based taxes), not extraction errors — EPS agrees throughout.

Limitations (documented, accepted):
- US SEC filers only (10-K annual frames)
- no market-priced ratios (pe/ps/ev_ebitda stay None — they need a quote)
- no profile: market_cap / sector / industry are not refreshed (get_profile is
  empty), so existing values persist and go stale
- figures are AS FILED, not split-adjusted: EPS from before a split reads on the
  pre-split share count (NVDA and NFLX both 10:1). The latest period — the only
  one asset_metrics reads — is unaffected.

SEC requires a User-Agent identifying the caller (anonymous UAs are rejected) and
caps clients at 10 req/s (PROVIDER_LIMITS["edgar"] allows 9/s including burst).
"""

from datetime import date

from app.providers.base import ProviderError
from app.providers.http import RateLimitedClient
from app.schemas.fundamentals import FundamentalsPeriod

BASE_URL = "https://data.sec.gov"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
USER_AGENT = "plutus/0.1 (santiago.coronado94@gmail.com)"

# A 10-K tags its Q4 columns with the same form and fiscal period as the annual
# ones, so a duration fact only counts as annual when it actually spans a year.
# The range tolerates 52/53-week retail calendars and short transition years.
ANNUAL_SPAN_DAYS = (330, 400)

# An explicit CIK beats the ticker map. Some tickers resolve to a registrant
# that holds no XBRL history: after ExxonMobil's 2026 reorganisation the SEC map
# points XOM at "ExxonMobil Holdings Corp" (CIK 2115436, zero us-gaap facts)
# while the financials stay under Exxon Mobil Corporation. Others are simply
# filed under a different ticker than the one they trade as (MMC -> MRSH).
CIK_PREFIX = "CIK"
SYMBOL_OVERRIDES = {
    "XOM": "CIK0000034088",  # Exxon Mobil Corporation, not the new holdco
    "MMC": "MRSH",  # Marsh & McLennan files as MRSH in the SEC ticker map
}

# us-gaap concept candidates per normalized field, in preference order. The
# merge is per REPORT DATE, not per concept: filers migrate between concepts
# (Duke's revenue moved to ...IncludingAssessedTax, Goldman reports
# RevenuesNetOfInterestExpense), and taking the first concept that has *any*
# data used to pin a company to whichever tag it abandoned earliest.
CONCEPTS = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "RevenuesNetOfInterestExpense",
        "SalesRevenueNet",
    ),
    "eps": (
        "EarningsPerShareDiluted",
        "EarningsPerShareBasic",
        "IncomeLossFromContinuingOperationsPerDilutedShare",
        "IncomeLossFromContinuingOperationsPerBasicShare",
    ),
    "net_income": (
        "NetIncomeLoss",
        # American Tower and other REIT-like filers stopped tagging NetIncomeLoss
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ),
    "gross_profit": ("GrossProfit",),
    # GrossProfit is a subtotal many filers never tag; cost of revenue almost
    # always is, so gross profit falls back to revenue - cost (see below)
    "cost_of_revenue": (
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfServices",
    ),
    "equity": (
        "StockholdersEquity",
        # J&J and Visa report only the consolidated figure including minority interest
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "liabilities": ("Liabilities",),
    # many filers tag only the balance-sheet total, so liabilities falls back to
    # the accounting identity: assets - equity (see get_fundamentals)
    "assets": ("Assets", "LiabilitiesAndStockholdersEquity"),
    "operating_cashflow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capex": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForCapitalImprovements",
        "PaymentsToAcquireOtherPropertyPlantAndEquipment",
    ),
}


def _covers_fiscal_year(start: str | None, end: str) -> bool:
    """Is this row a full-year figure?

    Instant facts (balance-sheet items: equity, liabilities) carry no `start`
    and are point-in-time, so they always qualify. Duration facts must span a
    fiscal year — without this check a 10-K's Q4 column is read as the annual
    figure, which reported Honeywell's revenue as $9.8B instead of $37.4B.
    """
    if start is None:
        return True
    try:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return False
    return ANNUAL_SPAN_DAYS[0] <= days <= ANNUAL_SPAN_DAYS[1]


class EdgarProvider:
    name = "edgar"

    def __init__(self, client: RateLimitedClient, ticker_client: RateLimitedClient) -> None:
        self._client = client  # data.sec.gov
        self._tickers = ticker_client  # www.sec.gov (ticker map lives on the www host)

    def _cik_for(self, symbol: str) -> int:
        symbol = SYMBOL_OVERRIDES.get(symbol.upper(), symbol)
        if symbol.upper().startswith(CIK_PREFIX) and symbol[len(CIK_PREFIX) :].isdigit():
            return int(symbol[len(CIK_PREFIX) :])
        mapping = self._tickers.get_json("/files/company_tickers.json", cache_ttl=24 * 3600)
        for entry in mapping.values():
            if entry.get("ticker", "").upper() == symbol.upper():
                return int(entry["cik_str"])
        raise ProviderError(f"edgar: no CIK found for {symbol}")

    @staticmethod
    def _concept_annuals(gaap: dict, concept: str) -> dict[str, float]:
        """fiscal-year-end date -> value for one concept's 10-K annual rows.

        `fp` is deliberately NOT filtered on: current-year rows for several
        large filers (Boeing, Lockheed, Mastercard, Alphabet) carry a different
        fiscal-period marker, and requiring fp == 'FY' silently pinned them to
        figures years out of date. The span check does the real work instead.
        A period restated by a later 10-K wins on `filed`.
        """
        best: dict[str, tuple[str, float]] = {}
        for unit_values in gaap.get(concept, {}).get("units", {}).values():
            for row in unit_values:
                end = row.get("end")
                if row.get("form") != "10-K" or "val" not in row or not end:
                    continue
                if not _covers_fiscal_year(row.get("start"), end):
                    continue
                filed = row.get("filed") or ""
                previous = best.get(end)
                if previous is None or filed >= previous[0]:
                    best[end] = (filed, float(row["val"]))
        return {end: value for end, (_filed, value) in best.items()}

    def _annual_facts(self, facts: dict, concepts: tuple[str, ...]) -> dict[str, float]:
        """fiscal-year-end date -> value, merged across concepts PER DATE.

        Preference order still decides ties, but a concept a filer abandoned can
        no longer shadow the one it moved to: each date takes the first concept
        that actually reports it.
        """
        gaap = facts.get("facts", {}).get("us-gaap", {})
        merged: dict[str, float] = {}
        for concept in concepts:
            for report_date, value in self._concept_annuals(gaap, concept).items():
                merged.setdefault(report_date, value)
        return merged

    def get_fundamentals(
        self, symbol: str, period: str = "annual", limit: int = 6
    ) -> list[FundamentalsPeriod]:
        cik = self._cik_for(symbol)
        # Deliberately NOT cached. companyfacts payloads run 5-10MB each, and the
        # refresh touches every symbol once a week — a 24h TTL never lives long
        # enough to be read again, it just parks ~530MB of dead weight in the
        # Redis that also carries the Celery broker (which has no maxmemory).
        facts = self._client.get_json(f"/api/xbrl/companyfacts/CIK{cik:010d}.json")
        if not facts.get("facts", {}).get("us-gaap"):
            # a registrant with no XBRL history — usually the ticker resolved to a
            # holding shell. Fail loudly: silently returning [] reads as "this
            # company simply has no fundamentals" and never gets investigated.
            raise ProviderError(
                f"edgar: CIK {cik:010d} ({facts.get('entityName', '?')}) has no us-gaap "
                f"facts for {symbol} — add a SYMBOL_OVERRIDES entry pointing at the "
                "filing entity"
            )
        series = {name: self._annual_facts(facts, concepts) for name, concepts in CONCEPTS.items()}
        report_dates = sorted(series["revenue"] or series["net_income"], reverse=True)[:limit]

        periods = []
        for report_date in report_dates:
            revenue = series["revenue"].get(report_date)
            net_income = series["net_income"].get(report_date)
            gross_profit = series["gross_profit"].get(report_date)
            if gross_profit is None and revenue is not None:
                cost = series["cost_of_revenue"].get(report_date)
                if cost is not None:
                    gross_profit = revenue - cost
            equity = series["equity"].get(report_date)
            liabilities = series["liabilities"].get(report_date)
            if liabilities is None:
                # Assets = Liabilities + Equity. Filers that tag only the
                # balance-sheet total still yield a debt-to-equity this way.
                assets = series["assets"].get(report_date)
                if assets is not None and equity is not None:
                    liabilities = assets - equity
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
