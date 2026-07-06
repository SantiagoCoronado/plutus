import { useEffect, useState } from 'react'
import { api, type AlertCondition, type AlertRule } from '../../api/client'
import {
  ErrorList,
  Field,
  Modal,
  buttonClass,
  inputClass,
  parseServerErrors,
  type ServerError,
} from '../portfolio/shared'

const STATUS_STYLES: Record<string, string> = {
  armed: 'bg-emerald-900/40 text-emerald-300',
  triggered: 'bg-amber-900/40 text-amber-300',
  disabled: 'bg-zinc-800 text-zinc-500',
}

export default function AlertModal({
  assetId,
  symbol,
  onClose,
  onChanged,
}: {
  assetId: number
  symbol: string
  onClose: () => void
  onChanged?: () => void
}) {
  const [rules, setRules] = useState<AlertRule[]>([])
  const [condition, setCondition] = useState<AlertCondition>('above')
  const [threshold, setThreshold] = useState('')
  const [note, setNote] = useState('')
  const [errors, setErrors] = useState<ServerError[]>([])
  const [saving, setSaving] = useState(false)

  const load = () => {
    api.alerts({ asset_id: assetId }).then(setRules).catch(() => {})
  }
  const refresh = () => {
    load()
    onChanged?.()
  }

  useEffect(load, [assetId])

  const create = async () => {
    setSaving(true)
    setErrors([])
    try {
      await api.createAlert({
        asset_id: assetId,
        condition,
        threshold: Number(threshold),
        note: note || null,
      })
      setThreshold('')
      setNote('')
      refresh()
    } catch (e) {
      setErrors(parseServerErrors(e))
    } finally {
      setSaving(false)
    }
  }

  const rearm = async (rule: AlertRule) => {
    await api.updateAlert(rule.id, { status: 'armed' })
    refresh()
  }
  const disable = async (rule: AlertRule) => {
    await api.updateAlert(rule.id, { status: 'disabled' })
    refresh()
  }
  const remove = async (rule: AlertRule) => {
    await api.deleteAlert(rule.id)
    refresh()
  }

  return (
    <Modal title={`Price alerts — ${symbol}`} onClose={onClose}>
      <div className="grid grid-cols-[auto_1fr_auto] items-end gap-3">
        <Field label="when price is">
          <select
            value={condition}
            onChange={(e) => setCondition(e.target.value as AlertCondition)}
            className={`${inputClass} w-full`}
          >
            <option value="above">above</option>
            <option value="below">below</option>
          </select>
        </Field>
        <Field label="threshold">
          <input
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            placeholder="0.00"
            inputMode="decimal"
            className={`${inputClass} w-full`}
          />
        </Field>
        <button
          onClick={create}
          disabled={saving || !threshold || Number(threshold) <= 0}
          className="rounded bg-sky-700 px-4 py-1.5 text-sm text-white hover:bg-sky-600 disabled:opacity-50"
        >
          {saving ? 'Adding…' : 'Add alert'}
        </button>
      </div>

      <Field label="note (optional)">
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="why this level matters"
          className={`${inputClass} w-full`}
        />
      </Field>

      <ErrorList errors={errors} />

      {rules.length === 0 ? (
        <p className="py-3 text-center text-sm text-zinc-600">
          No alerts yet. Fires once when the price crosses your level — never places a trade.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {rules.map((rule) => (
            <li
              key={rule.id}
              className="flex items-center justify-between gap-2 rounded border border-zinc-800 px-3 py-2 text-sm"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded px-1.5 py-0.5 text-[10px] ${STATUS_STYLES[rule.status]}`}>
                  {rule.status}
                </span>
                <span className="text-zinc-300">
                  {rule.condition} <span className="font-mono">{rule.threshold}</span>
                </span>
                {rule.note && <span className="text-xs text-zinc-600">{rule.note}</span>}
              </div>
              <div className="flex shrink-0 items-center gap-3">
                {rule.status !== 'armed' && (
                  <button
                    onClick={() => rearm(rule)}
                    className="text-xs text-emerald-400 hover:text-emerald-300"
                  >
                    re-arm
                  </button>
                )}
                {rule.status !== 'disabled' && (
                  <button
                    onClick={() => disable(rule)}
                    className="text-xs text-zinc-500 hover:text-zinc-300"
                  >
                    disable
                  </button>
                )}
                <button
                  onClick={() => remove(rule)}
                  className="text-xs text-zinc-600 hover:text-red-400"
                >
                  delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="flex justify-end">
        <button onClick={onClose} className={buttonClass}>
          Close
        </button>
      </div>
    </Modal>
  )
}
