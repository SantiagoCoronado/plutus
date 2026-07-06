/**
 * App-shell attribution footer. The CoinGecko credit is a ToS requirement
 * (spec §10) — kept as a real external link. Deliberately muted (zinc-500) so
 * it sits quietly at the bottom of the layout without crowding content.
 */
export default function AttributionFooter() {
  return (
    <footer className="border-t border-zinc-900 px-6 py-4 text-xs text-zinc-500">
      <p>
        Crypto market data by{' '}
        <a
          href="https://www.coingecko.com"
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:text-zinc-400"
        >
          CoinGecko
        </a>
        . Market data also by Tiingo, Binance, Twelve Data, FMP, and Finnhub.
      </p>
      <p className="mt-0.5">Informational only — not financial advice.</p>
    </footer>
  )
}
