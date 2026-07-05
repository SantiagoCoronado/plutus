import { useCallback, useEffect, useState } from 'react'
import {
  api,
  fmtPct,
  getCurrency,
  pctClass,
  type Account,
  type BankInvestment,
  type PositionsReport,
} from '../api/client'
import AccountsPanel from '../components/portfolio/AccountsPanel'
import AllocationDonut from '../components/portfolio/AllocationDonut'
import BankSection from '../components/portfolio/BankSection'
import CsvImportWizard from '../components/portfolio/CsvImportWizard'
import CurrencyToggle from '../components/portfolio/CurrencyToggle'
import PerformanceChart from '../components/portfolio/PerformanceChart'
import PositionsTable from '../components/portfolio/PositionsTable'
import TransactionsLedger from '../components/portfolio/TransactionsLedger'
import { buttonClass, fmtMoney } from '../components/portfolio/shared'

function SummaryBar({ report }: { report: PositionsReport }) {
  const totals = report.totals
  const cards: { label: string; value: string; className?: string }[] = [
    { label: `total value (${report.currency})`, value: fmtMoney(totals.value) },
    { label: 'invested', value: fmtMoney(totals.positions_value) },
    { label: 'cash & fixed income', value: fmtMoney((totals.cash_value ?? 0) + (totals.bank_value ?? 0)) },
    {
      label: 'unrealized P&L',
      value: `${fmtMoney(totals.unrealized_pnl)}${
        totals.unrealized_pnl_pct !== null ? ` (${fmtPct(totals.unrealized_pnl_pct, 1)})` : ''
      }`,
      className: pctClass(totals.unrealized_pnl),
    },
    {
      label: 'realized P&L',
      value: fmtMoney(totals.realized_pnl),
      className: pctClass(totals.realized_pnl),
    },
  ]
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
      {cards.map((card) => (
        <div key={card.label} className="rounded border border-zinc-800 px-3 py-2">
          <p className="text-[11px] text-zinc-500">{card.label}</p>
          <p className={`font-mono text-sm ${card.className ?? ''}`}>{card.value}</p>
        </div>
      ))}
    </div>
  )
}

export default function Portfolio() {
  const [currency, setCurrencyState] = useState(getCurrency())
  const [report, setReport] = useState<PositionsReport | null>(null)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [investments, setInvestments] = useState<BankInvestment[]>([])
  const [importing, setImporting] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)

  const load = useCallback(async () => {
    const [positions, accountList, investmentList] = await Promise.all([
      api.portfolioPositions(currency),
      api.accounts(),
      api.bankInvestments(),
    ])
    setReport(positions)
    setAccounts(accountList)
    setInvestments(investmentList)
  }, [currency])

  useEffect(() => {
    load().catch(() => setReport(null))
  }, [load, refreshKey])

  const refresh = () => setRefreshKey((k) => k + 1)

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Portfolio</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setImporting(true)}
            disabled={accounts.length === 0}
            title={accounts.length === 0 ? 'add an account first' : undefined}
            className={buttonClass}
          >
            Import CSV
          </button>
          <CurrencyToggle currency={currency} onChange={setCurrencyState} />
        </div>
      </div>

      {report === null ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : (
        <>
          <SummaryBar report={report} />

          <div className="grid gap-4 lg:grid-cols-5">
            <div className="lg:col-span-3">
              <PerformanceChart currency={currency} refreshKey={refreshKey} />
            </div>
            <div className="lg:col-span-2">
              <AllocationDonut currency={currency} refreshKey={refreshKey} />
            </div>
          </div>

          <PositionsTable report={report} currency={currency} />

          <BankSection investments={investments} accounts={accounts} onChanged={refresh} />

          <div className="grid gap-4 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <TransactionsLedger
                accounts={accounts}
                refreshKey={refreshKey}
                onChanged={refresh}
              />
            </div>
            <AccountsPanel accounts={accounts} onChanged={refresh} />
          </div>
        </>
      )}

      {importing && (
        <CsvImportWizard
          accounts={accounts}
          onClose={() => setImporting(false)}
          onImported={refresh}
        />
      )}
    </div>
  )
}
