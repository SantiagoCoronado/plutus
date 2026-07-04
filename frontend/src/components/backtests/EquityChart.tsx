import { createChart, LineSeries, type IChartApi } from 'lightweight-charts'
import { useEffect, useRef } from 'react'

interface Props {
  portfolio: [string, number][]
  benchmark: [string, number][]
  benchmarkLabel?: string
}

/** Single-pane equity curve: portfolio vs benchmark (ChartContainer is candle-specific). */
export default function EquityChart({ portfolio, benchmark }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container || portfolio.length === 0) return

    const chart = createChart(container, {
      height: 320,
      layout: { background: { color: 'transparent' }, textColor: '#a1a1aa' },
      grid: {
        vertLines: { color: 'rgba(63,63,70,0.35)' },
        horzLines: { color: 'rgba(63,63,70,0.35)' },
      },
      rightPriceScale: { borderColor: '#3f3f46' },
      timeScale: { borderColor: '#3f3f46' },
    })
    chartRef.current = chart

    const toLine = (points: [string, number][]) =>
      points.map(([date, value]) => ({ time: date, value }))

    const portfolioSeries = chart.addSeries(LineSeries, {
      color: '#38bdf8',
      lineWidth: 2,
      priceLineVisible: false,
    })
    portfolioSeries.setData(toLine(portfolio))

    if (benchmark.length > 0) {
      const benchmarkSeries = chart.addSeries(LineSeries, {
        color: '#71717a',
        lineWidth: 1,
        priceLineVisible: false,
      })
      benchmarkSeries.setData(toLine(benchmark))
    }
    chart.timeScale().fitContent()

    const observer = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth })
      chart.timeScale().fitContent()
    })
    observer.observe(container)

    return () => {
      observer.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [portfolio, benchmark])

  return (
    <div>
      <div ref={containerRef} className="w-full" />
      <div className="mt-1 flex gap-4 text-xs text-zinc-500">
        <span>
          <span className="mr-1 inline-block h-2 w-2 rounded-full bg-sky-400" />
          portfolio
        </span>
        {benchmark.length > 0 && (
          <span>
            <span className="mr-1 inline-block h-2 w-2 rounded-full bg-zinc-500" />
            benchmark
          </span>
        )}
      </div>
    </div>
  )
}
