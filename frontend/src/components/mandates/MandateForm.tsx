import { useMemo, useState } from 'react'
import {
  ApiError,
  api,
  type AssetClass,
  type FilterNode,
  type Mandate,
  type MandateBody,
  type NotifyMode,
  type ScreenField,
  type SignalInfo,
  type UniverseDef,
  type Watchlist,
} from '../../api/client'
import RuleBuilder, {
  astToRows,
  rowsToAst,
  type BuilderRow,
} from '../screener/RuleBuilder'

const inputClass =
  'rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200 placeholder-zinc-600 focus:border-zinc-500 focus:outline-none'

const SCHEDULE_PRESETS: { label: string; value: string }[] = [
  { label: 'Weekday mornings 07:30', value: '30 7 * * 1-5' },
  { label: 'Every day 07:30', value: '30 7 * * *' },
  { label: 'Mondays 07:30', value: '30 7 * * 1' },
  { label: 'Every hour', value: '0 * * * *' },
]

// starter mix for new mandates (validation needs at least one weight above zero)
const DEFAULT_WEIGHTS: Record<string, number> = {
  rsi_extreme: 1,
  breakout: 1,
  momentum_rank: 1,
  crypto_drawdown: 1,
}

interface ServerError {
  path?: string
  error: string
}

function parseServerErrors(e: unknown): ServerError[] | null {
  if (!(e instanceof ApiError)) return null
  try {
    const detail = JSON.parse(e.message).detail
    if (Array.isArray(detail?.errors)) return detail.errors
    if (typeof detail === 'string') return [{ error: detail }]
  } catch {
    /* not json */
  }
  return null
}

interface Props {
  mandate: Mandate | null
  signals: SignalInfo[]
  watchlists: Watchlist[]
  fields: ScreenField[]
  onClose: () => void
  onSaved: () => void
}

export default function MandateForm({
  mandate,
  signals,
  watchlists,
  fields,
  onClose,
  onSaved,
}: Props) {
  const [name, setName] = useState(mandate?.name ?? '')
  const [description, setDescription] = useState(mandate?.description ?? '')
  const [assetClass, setAssetClass] = useState<AssetClass>(mandate?.asset_class ?? 'stock')

  const universe = mandate?.universe_def
  const [universeType, setUniverseType] = useState<UniverseDef['type']>(
    universe?.type ?? 'class',
  )
  const [watchlistId, setWatchlistId] = useState<number | ''>(
    universe?.type === 'watchlist' ? universe.watchlist_id : '',
  )
  const [minMarketCapB, setMinMarketCapB] = useState(
    universe?.type === 'market_cap_floor' ? String(universe.min_market_cap / 1e9) : '10',
  )
  const [topCount, setTopCount] = useState(
    universe?.type === 'top_by_market_cap' ? String(universe.count) : '20',
  )

  const initialRows = mandate?.rules ? astToRows(mandate.rules) : []
  const [rows, setRows] = useState<BuilderRow[]>(initialRows ?? [])
  // rules the builder can't represent fall back to raw JSON (Screener convention)
  const [rulesJson, setRulesJson] = useState<string | null>(
    mandate?.rules && initialRows === null ? JSON.stringify(mandate.rules, null, 2) : null,
  )

  const initialSchedule = mandate?.schedule ?? SCHEDULE_PRESETS[0].value
  const isPreset = SCHEDULE_PRESETS.some((p) => p.value === initialSchedule)
  const [preset, setPreset] = useState(isPreset ? initialSchedule : 'custom')
  const [customCron, setCustomCron] = useState(isPreset ? '' : initialSchedule)

  const [weights, setWeights] = useState<Record<string, string>>(() => {
    const source = mandate?.score_weights ?? DEFAULT_WEIGHTS
    return Object.fromEntries(
      signals.map((s) => [s.key, String(source[s.key] ?? 0)]),
    )
  })
  const [minScore, setMinScore] = useState(String(mandate?.min_score ?? 40))
  const [notifyMinScore, setNotifyMinScore] = useState(
    mandate?.notify_min_score != null ? String(mandate.notify_min_score) : '',
  )
  const [maxCandidates, setMaxCandidates] = useState(String(mandate?.max_candidates ?? 20))
  const [cooldownDays, setCooldownDays] = useState(String(mandate?.cooldown_days ?? 7))
  const [notify, setNotify] = useState<NotifyMode>(mandate?.notify ?? 'instant')

  const [errors, setErrors] = useState<ServerError[] | null>(null)
  const [message, setMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const applicable = useMemo(
    () => signals.filter((s) => s.asset_classes.includes(assetClass)),
    [signals, assetClass],
  )

  const buildBody = (): MandateBody | null => {
    let universeDef: UniverseDef
    if (universeType === 'watchlist') {
      if (watchlistId === '') {
        setMessage('pick a watchlist')
        return null
      }
      universeDef = { type: 'watchlist', watchlist_id: watchlistId }
    } else if (universeType === 'market_cap_floor') {
      universeDef = { type: 'market_cap_floor', min_market_cap: Number(minMarketCapB) * 1e9 }
    } else if (universeType === 'top_by_market_cap') {
      universeDef = { type: 'top_by_market_cap', count: Number(topCount) }
    } else {
      universeDef = { type: 'class' }
    }

    let rules: FilterNode | null
    if (rulesJson !== null) {
      if (rulesJson.trim() === '') {
        rules = null
      } else {
        try {
          rules = JSON.parse(rulesJson)
        } catch {
          setMessage('rules JSON does not parse')
          return null
        }
      }
    } else {
      rules = rowsToAst(rows)
    }

    const scoreWeights: Record<string, number> = {}
    for (const signal of applicable) {
      const weight = Number(weights[signal.key] ?? 0)
      if (weight > 0) scoreWeights[signal.key] = weight
    }

    return {
      name: name.trim(),
      description: description.trim() || null,
      asset_class: assetClass,
      universe_def: universeDef,
      rules,
      schedule: preset === 'custom' ? customCron.trim() : preset,
      score_weights: scoreWeights,
      min_score: Number(minScore),
      notify_min_score: notifyMinScore.trim() === '' ? null : Number(notifyMinScore),
      max_candidates: Number(maxCandidates),
      cooldown_days: Number(cooldownDays),
      notify,
    }
  }

  const save = async () => {
    setErrors(null)
    setMessage('')
    const body = buildBody()
    if (!body) return
    if (!body.name) {
      setMessage('name is required')
      return
    }
    setSubmitting(true)
    try {
      if (mandate) await api.updateMandate(mandate.id, body)
      else await api.createMandate(body)
      onSaved()
    } catch (e) {
      const serverErrors = parseServerErrors(e)
      if (serverErrors) setErrors(serverErrors)
      else if (e instanceof ApiError && e.status === 409) setMessage('that name already exists')
      else setMessage('could not save the mandate')
    } finally {
      setSubmitting(false)
    }
  }

  const field = (label: string, control: JSX.Element) => (
    <label className="block space-y-1">
      <span className="text-xs text-zinc-500">{label}</span>
      {control}
    </label>
  )

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 py-10"
      onClick={onClose}
    >
      <div
        className="w-[560px] space-y-4 rounded-lg border border-zinc-700 bg-zinc-950 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold">
          {mandate ? `Edit "${mandate.name}"` : 'New mandate'}
        </h2>

        <div className="grid grid-cols-2 gap-3">
          {field(
            'name',
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={`${inputClass} w-full`}
              placeholder="Oversold large caps"
            />,
          )}
          {field(
            'asset class',
            <select
              value={assetClass}
              onChange={(e) => setAssetClass(e.target.value as AssetClass)}
              className={`${inputClass} w-full`}
            >
              <option value="stock">stock</option>
              <option value="etf">etf</option>
              <option value="crypto">crypto</option>
              <option value="forex">forex</option>
            </select>,
          )}
        </div>
        {field(
          'description (optional)',
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className={`${inputClass} w-full`}
          />,
        )}

        <div className="space-y-2">
          <span className="text-xs text-zinc-500">universe</span>
          <div className="flex flex-wrap gap-3 text-sm">
            {(
              [
                ['class', 'Whole class'],
                ['watchlist', 'Watchlist'],
                ['market_cap_floor', 'Market cap above'],
                ['top_by_market_cap', 'Top N by size'],
              ] as const
            ).map(([value, label]) => (
              <label key={value} className="flex items-center gap-1.5">
                <input
                  type="radio"
                  checked={universeType === value}
                  onChange={() => setUniverseType(value)}
                />
                {label}
              </label>
            ))}
          </div>
          {universeType === 'watchlist' && (
            <select
              value={watchlistId}
              onChange={(e) => setWatchlistId(e.target.value ? Number(e.target.value) : '')}
              className={`${inputClass} w-full`}
            >
              <option value="">pick a watchlist…</option>
              {watchlists.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          )}
          {universeType === 'market_cap_floor' &&
            field(
              'minimum market cap ($ billions)',
              <input
                type="number"
                min="0.1"
                step="0.1"
                value={minMarketCapB}
                onChange={(e) => setMinMarketCapB(e.target.value)}
                className={`${inputClass} w-40`}
              />,
            )}
          {universeType === 'top_by_market_cap' &&
            field(
              'how many (largest first)',
              <input
                type="number"
                min="1"
                max="100"
                value={topCount}
                onChange={(e) => setTopCount(e.target.value)}
                className={`${inputClass} w-40`}
              />,
            )}
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-zinc-500">
              filter rules (optional — narrows the universe before signals run)
            </span>
            {rulesJson === null ? (
              <button
                onClick={() => setRulesJson(JSON.stringify(rowsToAst(rows) ?? {}, null, 2))}
                className="text-xs text-zinc-500 hover:text-zinc-300"
              >
                edit as JSON
              </button>
            ) : (
              <span className="text-xs text-zinc-600">JSON mode</span>
            )}
          </div>
          {rulesJson === null ? (
            <RuleBuilder rows={rows} fields={fields} onChange={setRows} />
          ) : (
            <textarea
              value={rulesJson}
              onChange={(e) => setRulesJson(e.target.value)}
              rows={5}
              spellCheck={false}
              className={`${inputClass} w-full font-mono text-xs`}
            />
          )}
        </div>

        <div className="space-y-1">
          <span className="text-xs text-zinc-500">schedule (times are local)</span>
          <div className="flex gap-2">
            <select
              value={preset}
              onChange={(e) => setPreset(e.target.value)}
              className={`${inputClass} flex-1`}
            >
              {SCHEDULE_PRESETS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
              <option value="custom">Custom cron…</option>
            </select>
            {preset === 'custom' && (
              <input
                value={customCron}
                onChange={(e) => setCustomCron(e.target.value)}
                placeholder="30 7 * * 1-5"
                className={`${inputClass} flex-1 font-mono`}
              />
            )}
          </div>
        </div>

        <div className="space-y-2">
          <span className="text-xs text-zinc-500">
            signal weights (0 = off; the score is the weighted average)
          </span>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
            {applicable.map((signal) => (
              <label
                key={signal.key}
                className="flex items-center justify-between gap-2 text-sm"
                title={signal.description}
              >
                <span className="truncate text-zinc-300">{signal.label}</span>
                <input
                  type="number"
                  min="0"
                  max="10"
                  step="0.5"
                  value={weights[signal.key] ?? '0'}
                  onChange={(e) =>
                    setWeights({ ...weights, [signal.key]: e.target.value })
                  }
                  className={`${inputClass} w-16 text-right`}
                />
              </label>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-4 gap-3">
          {field(
            'min score',
            <input
              type="number"
              min="0"
              max="100"
              value={minScore}
              onChange={(e) => setMinScore(e.target.value)}
              className={`${inputClass} w-full`}
            />,
          )}
          {field(
            'alert above (opt.)',
            <input
              type="number"
              min="0"
              max="100"
              value={notifyMinScore}
              onChange={(e) => setNotifyMinScore(e.target.value)}
              placeholder="= min"
              className={`${inputClass} w-full`}
            />,
          )}
          {field(
            'keep top N',
            <input
              type="number"
              min="1"
              max="100"
              value={maxCandidates}
              onChange={(e) => setMaxCandidates(e.target.value)}
              className={`${inputClass} w-full`}
            />,
          )}
          {field(
            'cooldown (days)',
            <input
              type="number"
              min="0"
              max="90"
              value={cooldownDays}
              onChange={(e) => setCooldownDays(e.target.value)}
              className={`${inputClass} w-full`}
            />,
          )}
        </div>

        {field(
          'alerts',
          <select
            value={notify}
            onChange={(e) => setNotify(e.target.value as NotifyMode)}
            className={`${inputClass} w-full`}
          >
            <option value="off">Off</option>
            <option value="instant">Right away (one message per scan)</option>
            <option value="digest">Daily summary</option>
          </select>,
        )}

        {errors && (
          <ul className="space-y-1 rounded border border-red-900/60 bg-red-950/30 px-3 py-2">
            {errors.map((err, i) => (
              <li key={i} className="text-xs text-red-300">
                {err.path ? <span className="font-mono">{err.path}: </span> : null}
                {err.error}
              </li>
            ))}
          </ul>
        )}
        {message && <p className="text-xs text-red-400">{message}</p>}

        <div className="flex justify-end gap-2 pt-1">
          <button
            onClick={onClose}
            className="rounded border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-800"
          >
            Cancel
          </button>
          <button
            onClick={save}
            disabled={submitting}
            className="rounded bg-sky-700 px-4 py-1.5 text-sm text-white hover:bg-sky-600 disabled:opacity-50"
          >
            {mandate ? 'Save changes' : 'Create mandate'}
          </button>
        </div>
      </div>
    </div>
  )
}
