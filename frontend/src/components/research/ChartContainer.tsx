import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import type { Candle, IndicatorPoint, IndicatorSeries } from '../../api/client'

const OVERLAY_COLORS: Record<string, string> = {
  sma_20: '#38bdf8',
  sma_50: '#a78bfa',
  sma_200: '#f59e0b',
  ema_12: '#34d399',
  ema_26: '#fb7185',
  ema_50: '#eab308',
  wma_20: '#94a3b8',
  vwap_20: '#e879f9',
  bb_upper: '#64748b',
  bb_middle: '#475569',
  bb_lower: '#64748b',
}

function lwcTime(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp
}

function toLine(points: IndicatorPoint[]) {
  return points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value }))
}

interface Props {
  candles: Candle[]
  series: IndicatorSeries
  showVolume: boolean
  showRsi: boolean
  showMacd: boolean
}

/** All lightweight-charts calls live here (v5 panes API). The chart is rebuilt on
 * data changes — datasets are small and this dodges series-lifecycle bookkeeping. */
export default function ChartContainer({ candles, series, showVolume, showRsi, showMacd }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el || candles.length === 0) return

    const chart = createChart(el, {
      layout: {
        background: { color: '#09090b' },
        textColor: '#a1a1aa',
        panes: { separatorColor: '#27272a' },
      },
      grid: {
        vertLines: { color: '#18181b' },
        horzLines: { color: '#18181b' },
      },
      crosshair: { mode: 0 },
      timeScale: { borderColor: '#27272a' },
      rightPriceScale: { borderColor: '#27272a' },
      autoSize: false,
      width: el.clientWidth,
      height: 460,
    })

    const candleSeries = chart.addSeries(
      CandlestickSeries,
      {
        upColor: '#22c55e',
        downColor: '#ef4444',
        borderVisible: false,
        wickUpColor: '#22c55e',
        wickDownColor: '#ef4444',
      },
      0,
    )
    candleSeries.setData(
      candles.map((c) => ({
        time: lwcTime(c.ts),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    )

    if (showVolume && candles.some((c) => c.volume !== null)) {
      const volumeSeries = chart.addSeries(
        HistogramSeries,
        { priceScaleId: 'volume', priceFormat: { type: 'volume' }, color: '#3f3f46' },
        0,
      )
      chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
      volumeSeries.setData(
        candles.map((c) => ({
          time: lwcTime(c.ts),
          value: c.volume ?? 0,
          color: c.close >= c.open ? '#14532d' : '#7f1d1d',
        })),
      )
    }

    // overlays on the price pane
    for (const [key, data] of Object.entries(series)) {
      if (key === 'rsi_14' || key === 'macd') continue
      if (Array.isArray(data)) {
        chart
          .addSeries(
            LineSeries,
            {
              color: OVERLAY_COLORS[key] ?? '#71717a',
              lineWidth: 1,
              priceLineVisible: false,
              lastValueVisible: false,
            },
            0,
          )
          .setData(toLine(data))
      } else {
        // multi-column overlay (bollinger bands)
        for (const [col, points] of Object.entries(data)) {
          chart
            .addSeries(
              LineSeries,
              {
                color: OVERLAY_COLORS[col] ?? '#71717a',
                lineWidth: 1,
                lineStyle: col === 'bb_middle' ? 1 : 0,
                priceLineVisible: false,
                lastValueVisible: false,
              },
              0,
            )
            .setData(toLine(points))
        }
      }
    }

    let paneIndex = 1
    if (showRsi && Array.isArray(series.rsi_14)) {
      const rsi = chart.addSeries(
        LineSeries,
        { color: '#38bdf8', lineWidth: 1, priceLineVisible: false },
        paneIndex,
      )
      rsi.setData(toLine(series.rsi_14))
      rsi.createPriceLine({ price: 70, color: '#52525b', lineWidth: 1, lineStyle: 2, title: '70' })
      rsi.createPriceLine({ price: 30, color: '#52525b', lineWidth: 1, lineStyle: 2, title: '30' })
      chart.panes()[paneIndex]?.setHeight(110)
      paneIndex += 1
    }
    const macd = series.macd
    if (showMacd && macd && !Array.isArray(macd)) {
      chart
        .addSeries(
          HistogramSeries,
          { color: '#3f3f46', priceLineVisible: false, lastValueVisible: false },
          paneIndex,
        )
        .setData(
          (macd.macd_hist ?? []).map((p) => ({
            time: p.time as UTCTimestamp,
            value: p.value,
            color: p.value >= 0 ? '#14532d' : '#7f1d1d',
          })),
        )
      chart
        .addSeries(
          LineSeries,
          { color: '#38bdf8', lineWidth: 1, priceLineVisible: false, lastValueVisible: false },
          paneIndex,
        )
        .setData(toLine(macd.macd ?? []))
      chart
        .addSeries(
          LineSeries,
          { color: '#fb923c', lineWidth: 1, priceLineVisible: false, lastValueVisible: false },
          paneIndex,
        )
        .setData(toLine(macd.macd_signal ?? []))
      chart.panes()[paneIndex]?.setHeight(110)
    }

    chart.timeScale().fitContent()

    const resize = new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth })
    })
    resize.observe(el)

    return () => {
      resize.disconnect()
      chart.remove()
    }
  }, [candles, series, showVolume, showRsi, showMacd])

  return <div ref={containerRef} className="w-full" />
}
