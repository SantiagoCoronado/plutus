import { useEffect, useMemo, useState } from 'react'
import {
  api,
  ApiError,
  type AssetClass,
  type AstErrorDetail,
  type FilterNode,
  type Screen,
  type ScreenField,
  type ScreenRunResult,
} from '../api/client'
import BacktestModal from '../components/screener/BacktestModal'
import JsonEditor from '../components/screener/JsonEditor'
import ResultsTable from '../components/screener/ResultsTable'
import RuleBuilder, { astToRows, rowsToAst, type BuilderRow } from '../components/screener/RuleBuilder'

const DEFAULT_ROWS: BuilderRow[] = [{ field: 'rsi_14', op: '<', value1: '40', value2: '' }]
const CLASSES: (AssetClass | '')[] = ['', 'stock', 'etf', 'crypto', 'forex']

function parseServerErrors(e: unknown): AstErrorDetail[] | null {
  if (!(e instanceof ApiError)) return null
  try {
    const detail = JSON.parse(e.message).detail
    return Array.isArray(detail?.errors) ? detail.errors : null
  } catch {
    return null
  }
}

export default function Screener() {
  const [fields, setFields] = useState<ScreenField[]>([])
  const [rows, setRows] = useState<BuilderRow[]>(DEFAULT_ROWS)
  const [rawAst, setRawAst] = useState<FilterNode | null>(null) // set when builder can't express it
  const [assetClass, setAssetClass] = useState<AssetClass | ''>('stock')

  const [screens, setScreens] = useState<Screen[]>([])
  const [selectedScreenId, setSelectedScreenId] = useState<number | ''>('')
  const [saveName, setSaveName] = useState('')

  const [result, setResult] = useState<ScreenRunResult | null>(null)
  const [running, setRunning] = useState(false)
  const [errors, setErrors] = useState<AstErrorDetail[] | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [showBacktest, setShowBacktest] = useState(false)

  const ast = useMemo(() => rawAst ?? rowsToAst(rows), [rawAst, rows])
  const builderLocked = rawAst !== null
  const selectedScreen = screens.find((s) => s.id === selectedScreenId) ?? null

  const loadScreens = () => api.screens().then(setScreens).catch(() => setScreens([]))

  useEffect(() => {
    api.screenFields().then(setFields).catch(() => setFields([]))
    loadScreens()
  }, [])

  const applyAst = (node: FilterNode) => {
    const asRows = astToRows(node)
    if (asRows) {
      setRows(asRows)
      setRawAst(null)
    } else {
      setRawAst(node)
    }
    setErrors(null)
  }

  const loadScreen = (screen: Screen) => {
    setSelectedScreenId(screen.id)
    setSaveName(screen.name)
    setAssetClass(screen.asset_class ?? '')
    applyAst(screen.ast)
    setResult(null)
  }

  const run = async () => {
    if (!ast) {
      setMessage('Add at least one complete condition first.')
      return
    }
    setRunning(true)
    setErrors(null)
    setMessage(null)
    try {
      setResult(await api.runScreen({ ast, asset_class: assetClass || null }))
    } catch (e) {
      setResult(null)
      setErrors(parseServerErrors(e) ?? [{ error: String(e) }])
    } finally {
      setRunning(false)
    }
  }

  const save = async () => {
    if (!ast || !saveName.trim()) {
      setMessage('Name the screen and add at least one condition.')
      return
    }
    setErrors(null)
    setMessage(null)
    const body = {
      name: saveName.trim(),
      asset_class: assetClass || null,
      ast,
    }
    try {
      if (selectedScreen && selectedScreen.name === saveName.trim()) {
        await api.updateScreen(selectedScreen.id, body)
        setMessage(`Updated "${body.name}".`)
      } else {
        const created = await api.createScreen(body)
        setSelectedScreenId(created.id)
        setMessage(`Saved "${body.name}".`)
      }
      await loadScreens()
    } catch (e) {
      const serverErrors = parseServerErrors(e)
      if (serverErrors) setErrors(serverErrors)
      else if (e instanceof ApiError && e.status === 409)
        setMessage('A screen with that name already exists — pick another name.')
      else setMessage(String(e))
    }
  }

  const removeScreen = async () => {
    if (!selectedScreen) return
    await api.deleteScreen(selectedScreen.id)
    setSelectedScreenId('')
    setSaveName('')
    await loadScreens()
  }

  return (
    <div className="max-w-5xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Screener</h1>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <select
            value={selectedScreenId}
            onChange={(e) => {
              const id = e.target.value ? Number(e.target.value) : ''
              setSelectedScreenId(id)
              const screen = screens.find((s) => s.id === id)
              if (screen) loadScreen(screen)
            }}
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 focus:border-zinc-500 focus:outline-none"
          >
            <option value="">Saved screens…</option>
            {screens.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          {selectedScreen && (
            <button
              type="button"
              onClick={removeScreen}
              className="text-xs text-zinc-500 hover:text-red-400"
            >
              delete
            </button>
          )}
        </div>
      </div>

      <div className="space-y-3 rounded border border-zinc-800 p-4">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <label className="text-zinc-400">
            Asset class{' '}
            <select
              value={assetClass}
              onChange={(e) => setAssetClass(e.target.value as AssetClass | '')}
              className="ml-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 focus:border-zinc-500 focus:outline-none"
            >
              {CLASSES.map((c) => (
                <option key={c} value={c}>
                  {c || 'all classes'}
                </option>
              ))}
            </select>
          </label>
          <span className="text-xs text-zinc-600">
            fields marked “no backtest” screen live but can’t be backtested (not point-in-time)
          </span>
        </div>

        {builderLocked ? (
          <p className="text-sm text-zinc-500">
            This filter uses <code>any</code>/<code>not</code> or field references — edit it as
            JSON below.
          </p>
        ) : (
          <RuleBuilder rows={rows} fields={fields} onChange={(next) => setRows(next)} />
        )}

        <JsonEditor ast={ast} builderLocked={builderLocked} serverErrors={errors} onApply={applyAst} />

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button
            type="button"
            onClick={run}
            disabled={running}
            className="rounded bg-sky-700 px-4 py-1.5 text-sm text-white hover:bg-sky-600 disabled:opacity-50"
          >
            {running ? 'Running…' : 'Run screen'}
          </button>
          <button
            type="button"
            onClick={() => setShowBacktest(true)}
            disabled={!ast || !assetClass}
            title={!assetClass ? 'pick a single asset class to backtest' : undefined}
            className="rounded border border-zinc-700 px-4 py-1.5 text-sm text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
          >
            Backtest…
          </button>
          <span className="mx-2 h-5 w-px bg-zinc-800" />
          <input
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
            placeholder="Screen name"
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm focus:border-zinc-500 focus:outline-none"
          />
          <button
            type="button"
            onClick={save}
            className="rounded border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-800"
          >
            {selectedScreen && selectedScreen.name === saveName.trim() ? 'Update' : 'Save'}
          </button>
          {message && <span className="text-xs text-zinc-500">{message}</span>}
        </div>
      </div>

      {result && <ResultsTable result={result} />}

      {showBacktest && ast && assetClass && (
        <BacktestModal
          ast={ast}
          assetClass={assetClass}
          screenId={
            selectedScreen && selectedScreen.name === saveName.trim() ? selectedScreen.id : null
          }
          onClose={() => setShowBacktest(false)}
        />
      )}
    </div>
  )
}
