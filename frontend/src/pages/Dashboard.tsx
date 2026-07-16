import { useEffect, useState } from 'react'
import { api, getCurrency, type Dashboard as DashboardData } from '../api/client'
import DailyBriefCard from '../components/dashboard/DailyBriefCard'
import HeatmapTreemap from '../components/dashboard/HeatmapTreemap'
import InboxPreview from '../components/dashboard/InboxPreview'
import MarketStrip from '../components/dashboard/MarketStrip'
import MetricCards from '../components/dashboard/MetricCards'
import StatusFooter from '../components/dashboard/StatusFooter'
import WatchlistPanel from '../components/dashboard/WatchlistPanel'
import AllocationDonut from '../components/portfolio/AllocationDonut'
import CurrencyToggle from '../components/portfolio/CurrencyToggle'

export default function Dashboard() {
  const [currency, setCurrency] = useState(getCurrency())
  const [data, setData] = useState<DashboardData | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    // cancelled-flag: a fast USD→MXN→USD toggle must not render MXN figures last
    let cancelled = false
    setFailed(false)
    api
      .dashboard(currency)
      .then((d) => {
        if (!cancelled) setData(d)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [currency])

  if (failed) {
    return <p className="text-sm text-red-400">Couldn't load the dashboard — is the API reachable?</p>
  }
  if (!data) {
    return <p className="text-sm text-zinc-500">Loading…</p>
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Dashboard</h1>
        <CurrencyToggle currency={currency} onChange={setCurrency} />
      </div>

      <MarketStrip entries={data.market_strip} />

      <MetricCards data={data} />

      <HeatmapTreemap currency={currency} />

      <div className="grid gap-4 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <InboxPreview candidates={data.candidates.top} />
        </div>
        <div className="space-y-4 lg:col-span-2">
          <WatchlistPanel />
          <AllocationDonut currency={currency} refreshKey={0} />
        </div>
      </div>

      <DailyBriefCard brief={data.agent_brief} />

      <StatusFooter
        lastScanAt={data.last_scan_at}
        ingestionStatus={data.ingestion_status}
        armedAlerts={data.armed_alerts}
      />
    </div>
  )
}
