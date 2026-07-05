import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  api,
  fmtPct,
  pctClass,
  type Backtest,
  type StrategyTranslation,
} from '../../api/client'

const POLL_MS = 2500
const MAX_POLLS = 120

/** Spec §13.5: paste content → fidelity report → explicit confirm → verdict. */
export default function TranslateStrategyCard({ onRan }: { onRan: () => void }) {
  const [open, setOpen] = useState(false)
  const [content, setContent] = useState('')
  const [symbol, setSymbol] = useState('')
  const [translating, setTranslating] = useState(false)
  const [draft, setDraft] = useState<StrategyTranslation | null>(null)
  const [result, setResult] = useState<Backtest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollCount = useRef(0)

  const translate = async () => {
    setTranslating(true)
    setError(null)
    setDraft(null)
    setResult(null)
    try {
      setDraft(await api.translateStrategy(content, symbol.trim() || undefined))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setTranslating(false)
    }
  }

  const confirm = async () => {
    if (!draft) return
    setError(null)
    try {
      const { backtest_id } = await api.confirmTranslation(draft.id)
      setDraft({ ...draft, status: 'confirmed', backtest_id })
      onRan()
      pollCount.current = 0
      poll(backtest_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const poll = async (backtestId: number) => {
    const backtest = await api.backtest(backtestId)
    if (backtest.status === 'done' || backtest.status === 'failed') {
      setResult(backtest)
      onRan()
      return
    }
    if (pollCount.current++ < MAX_POLLS) setTimeout(() => poll(backtestId), POLL_MS)
  }

  const discard = async () => {
    if (!draft) return
    await api.discardTranslation(draft.id)
    setDraft(null)
  }

  useEffect(() => () => {
    pollCount.current = MAX_POLLS // stop polling on unmount
  }, [])

  return (
    <div className="rounded border border-violet-900/50 bg-violet-950/10">
      <button
        className="flex w-full items-center justify-between px-4 py-3 text-left"
        onClick={() => setOpen(!open)}
      >
        <div>
          <span className="text-sm font-semibold text-violet-300">Test a strategy</span>
          <span className="ml-2 text-xs text-zinc-500">
            paste an article, transcript, or your own description — the AI translates it
            into a backtest you approve first
          </span>
        </div>
        <span className="text-zinc-500">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-violet-900/30 p-4">
          {!draft && (
            <>
              <textarea
                className="h-32 w-full rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-violet-700"
                placeholder="Paste the strategy description here… (e.g. “buy when the 50-day average crosses above the 200-day, sell on a close below the 50-day, 10% stop loss”)"
                value={content}
                onChange={(e) => setContent(e.target.value)}
              />
              <div className="flex items-center gap-2">
                <input
                  className="w-40 rounded border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-sm outline-none focus:border-violet-700"
                  placeholder="symbol (optional)"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                />
                <button
                  className="rounded bg-violet-800 px-4 py-1.5 text-sm hover:bg-violet-700 disabled:opacity-40"
                  disabled={translating || content.trim().length < 20}
                  onClick={translate}
                >
                  {translating ? 'Translating…' : 'Translate'}
                </button>
              </div>
            </>
          )}

          {error && (
            <p className="rounded border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
              {error}
            </p>
          )}

          {draft && <FidelityReport draft={draft} onConfirm={confirm} onDiscard={discard} />}

          {draft?.status === 'confirmed' && !result && (
            <p className="text-xs text-zinc-500">
              Backtest #{draft.backtest_id} running… results appear here.
            </p>
          )}
          {result && <Verdict backtest={result} />}
        </div>
      )}
    </div>
  )
}

function FidelityReport({
  draft,
  onConfirm,
  onDiscard,
}: {
  draft: StrategyTranslation
  onConfirm: () => void
  onDiscard: () => void
}) {
  if (draft.status === 'failed') {
    return (
      <div className="rounded border border-red-900/60 bg-red-950/20 p-3 text-xs text-red-300">
        <p className="font-semibold">Translation failed</p>
        <p className="mt-1">{draft.error}</p>
      </div>
    )
  }
  const blocked = !draft.translatable || draft.asset_id === null

  return (
    <div className="space-y-3">
      <div className="rounded border border-zinc-800 bg-zinc-900/60 p-3">
        <p className="mb-1 text-xs font-semibold text-zinc-300">
          What the AI understood{' '}
          <span className="ml-1 rounded bg-violet-950 px-1.5 py-0.5 text-[10px] text-violet-300">
            AI-translated{draft.symbol ? ` · ${draft.symbol}` : ''}
          </span>
        </p>
        <div className="prose prose-invert prose-sm max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {draft.understanding_md ?? '*no summary produced*'}
          </ReactMarkdown>
        </div>
      </div>

      <div className="rounded border border-amber-900/60 bg-amber-950/20 p-3">
        <p className="mb-1 text-xs font-semibold text-amber-300">
          What could NOT be expressed{' '}
          <span className="font-normal text-amber-500/80">
            — read before running; a silent approximation would be a bug
          </span>
        </p>
        {(draft.limitations ?? []).length === 0 ? (
          <p className="text-xs text-zinc-400">
            Nothing — the source translated fully into daily-bar rules.
          </p>
        ) : (
          <ul className="list-inside list-disc space-y-1 text-xs text-amber-200/90">
            {(draft.limitations ?? []).map((limitation, i) => (
              <li key={i}>{limitation}</li>
            ))}
          </ul>
        )}
      </div>

      {draft.spec && (
        <details className="rounded border border-zinc-800 p-3">
          <summary className="cursor-pointer text-xs text-zinc-500">
            machine spec (what actually runs)
          </summary>
          <pre className="mt-2 max-h-56 overflow-auto rounded bg-zinc-950 p-2 text-[11px] text-zinc-400">
            {JSON.stringify(draft.spec, null, 2)}
          </pre>
        </details>
      )}

      {draft.status === 'draft' && (
        <div className="flex items-center gap-2">
          <button
            className="rounded bg-emerald-800 px-4 py-1.5 text-sm hover:bg-emerald-700 disabled:opacity-40"
            disabled={blocked}
            onClick={onConfirm}
            title={
              blocked
                ? 'untranslatable content or untracked symbol — see the limitations'
                : 'run the backtest exactly as shown above'
            }
          >
            Confirm &amp; run backtest
          </button>
          <button
            className="rounded bg-zinc-800 px-4 py-1.5 text-sm hover:bg-zinc-700"
            onClick={onDiscard}
          >
            Discard
          </button>
        </div>
      )}
    </div>
  )
}

function Verdict({ backtest }: { backtest: Backtest }) {
  if (backtest.status === 'failed') {
    return (
      <p className="rounded border border-red-900/60 bg-red-950/20 px-3 py-2 text-xs text-red-300">
        Backtest failed: {backtest.error}
      </p>
    )
  }
  const stats = backtest.stats
  if (!stats) return null
  const excess = stats.excess_return
  const beat = excess !== null && excess !== undefined && excess >= 0
  const verdict =
    excess === null || excess === undefined
      ? 'No benchmark comparison available.'
      : beat
        ? 'It would have beaten simply buying and holding — worth a closer look, not a guarantee.'
        : 'Simply buying and holding would have done better over this period.'

  return (
    <div className="rounded border border-zinc-800 bg-zinc-900/60 p-3 text-sm">
      <p className="mb-2 text-xs font-semibold text-zinc-300">Plain-English result</p>
      <ul className="space-y-1 text-xs text-zinc-300">
        <li>
          Strategy return:{' '}
          <span className={pctClass(stats.total_return)}>{fmtPct(stats.total_return, 1)}</span>
          {excess !== null && excess !== undefined && (
            <>
              {' '}
              vs buy &amp; hold:{' '}
              <span className={pctClass(excess)}>
                {excess >= 0 ? '+' : ''}
                {fmtPct(excess, 1)} {beat ? 'ahead' : 'behind'}
              </span>
            </>
          )}
        </li>
        <li>
          Worst drawdown:{' '}
          <span className="text-red-400">{fmtPct(stats.max_drawdown, 1)}</span>
        </li>
        <li>
          Won {stats.win_rate !== null ? fmtPct(stats.win_rate, 0) : '—'} of{' '}
          {stats.n_trades} trades
        </li>
      </ul>
      <p className="mt-2 text-xs text-zinc-400">{verdict}</p>
      <Link
        to={`/backtests/${backtest.id}`}
        className="mt-2 inline-block text-xs text-sky-400 hover:text-sky-300"
      >
        full detail + quantstats report ↗
      </Link>
    </div>
  )
}
