"""Financial Modeling Prep — STABLE routes only (legacy /api/v3 is unmaintained).

Free tier (verified 2026-07): 250 req/day, ~5y ANNUAL statements for US companies.
One full refresh costs 6 requests per symbol. Field names occasionally drift between
FMP revisions — the tolerant _pick() candidates plus the golden fixtures captured from
a live key pin the mapping contract.
"""

from typing import Any

from app.providers.base import (
    TTL_FUNDAMENTALS,
    ProviderError,
    ProviderNotConfigured,
)
from app.providers.http import RateLimitedClient
from app.schemas.fundamentals import FundamentalsPeriod

BASE_URL = "https://financialmodelingprep.com/stable"

STATEMENT_ENDPOINTS = {
    "income": "/income-statement",
    "balance": "/balance-sheet-statement",
    "cashflow": "/cash-flow-statement",
    "ratios": "/ratios",
    "key_metrics": "/key-metrics",
}


def _pick(row: dict, *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


class FMPProvider:
    name = "fmp"

    def __init__(self, client: RateLimitedClient, api_key: str) -> None:
        self._client = client
        self._api_key = api_key

    def _require_key(self) -> None:
        if not self._api_key:
            raise ProviderNotConfigured("fmp: FMP_API_KEY is not set")

    def _get(self, path: str, symbol: str, period: str | None, limit: int | None) -> list[dict]:
        params: dict[str, Any] = {"symbol": symbol, "apikey": self._api_key}
        if period:
            params["period"] = period
        if limit:
            params["limit"] = limit
        payload = self._client.get_json(path, params, cache_ttl=TTL_FUNDAMENTALS)
        if isinstance(payload, dict) and payload.get("Error Message"):
            raise ProviderError(f"fmp: {payload['Error Message']}")
        return payload or []

    def get_fundamentals(
        self, symbol: str, period: str = "annual", limit: int = 6
    ) -> list[FundamentalsPeriod]:
        self._require_key()
        raw = {
            name: self._get(path, symbol, period, limit)
            for name, path in STATEMENT_ENDPOINTS.items()
        }
        by_date: dict[str, dict[str, dict]] = {}
        for name, rows in raw.items():
            for row in rows:
                report_date = row.get("date")
                if report_date:
                    by_date.setdefault(report_date, {})[name] = row

        periods: list[FundamentalsPeriod] = []
        for report_date in sorted(by_date, reverse=True):
            group = by_date[report_date]
            income = group.get("income", {})
            cashflow = group.get("cashflow", {})
            ratios = group.get("ratios", {})
            key_metrics = group.get("key_metrics", {})
            periods.append(
                FundamentalsPeriod(
                    period=period,
                    report_date=report_date,
                    fiscal_year=(
                        int(income.get("fiscalYear"))
                        if str(income.get("fiscalYear", "")).isdigit()
                        else int(report_date[:4])
                    ),
                    currency=income.get("reportedCurrency") or "USD",
                    revenue=_pick(income, "revenue"),
                    eps=_pick(income, "epsDiluted", "epsdiluted", "eps"),
                    fcf=_pick(cashflow, "freeCashFlow"),
                    gross_margin=_pick(ratios, "grossProfitMargin"),
                    net_margin=_pick(ratios, "netProfitMargin"),
                    roe=_pick(ratios, "returnOnEquity"),
                    debt_to_equity=_pick(ratios, "debtToEquityRatio", "debtEquityRatio"),
                    pe=_pick(ratios, "priceToEarningsRatio", "priceEarningsRatio"),
                    ps=_pick(ratios, "priceToSalesRatio"),
                    ev_ebitda=_pick(
                        key_metrics, "evToEBITDA", "enterpriseValueOverEBITDA"
                    ),
                    metrics=group,
                )
            )
        return periods

    def get_profile(self, symbol: str) -> dict:
        self._require_key()
        rows = self._get("/profile", symbol, None, None)
        if not rows:
            return {}
        row = rows[0]
        return {
            "market_cap": _pick(row, "marketCap", "mktCap"),
            "sector": row.get("sector"),
            "industry": row.get("industry"),
            "description": row.get("description"),
            "website": row.get("website"),
        }
