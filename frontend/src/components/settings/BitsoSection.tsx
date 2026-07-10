import { useCallback, useEffect, useState } from 'react'
import {
  api,
  relTime,
  type BitsoTestResult,
  type ExchangeStatus,
} from '../../api/client'
import {
  ErrorList,
  Field,
  buttonClass,
  inputClass,
  parseServerErrors,
  type ServerError,
} from '../portfolio/shared'

const KEY_FIELDS: { name: 'api_key' | 'api_secret'; storeKey: string; label: string }[] = [
  { name: 'api_key', storeKey: 'bitso_api_key', label: 'Bitso API key' },
  { name: 'api_secret', storeKey: 'bitso_api_secret', label: 'Bitso API secret' },
]

const statusClass: Record<string, string> = {
  success: 'text-emerald-400',
  partial: 'text-amber-400',
  failed: 'text-red-400',
  running: 'text-sky-400',
}

export default function BitsoSection() {
  const [status, setStatus] = useState<ExchangeStatus | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<ServerError[]>([])
  const [testResult, setTestResult] = useState<BitsoTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    api.exchangesStatus().then(setStatus)
  }, [])

  useEffect(load, [load])

  const save = async (patch: { api_key?: string; api_secret?: string }) => {
    setSaving(true)
    setErrors([])
    try {
      const next = await api.putBitsoKeys(patch)
      setStatus(next)
      setDrafts({})
      setTestResult(null)
    } catch (e) {
      setErrors(parseServerErrors(e))
    } finally {
      setSaving(false)
    }
  }

  const runTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(await api.testBitso())
    } catch (e) {
      setErrors(parseServerErrors(e))
    } finally {
      setTesting(false)
    }
  }

  if (!status) return null

  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">Bitso exchange (read-only)</h1>
      <p className="text-xs text-zinc-500">
        Read-only API keys let Plutus import your trades, deposits and withdrawals. Create a key
        in Bitso with view permissions only — Plutus never places or cancels orders.
      </p>
      <ErrorList errors={errors} />

      <div className="space-y-4 rounded border border-zinc-800 p-4">
        {KEY_FIELDS.map((field) => (
          <Field key={field.name} label={field.label}>
            <div className="flex gap-2">
              <input
                type="password"
                className={inputClass}
                placeholder={status.keys[field.storeKey] ?? 'not set'}
                value={drafts[field.name] ?? ''}
                onChange={(e) => setDrafts((d) => ({ ...d, [field.name]: e.target.value }))}
              />
              <button
                className={buttonClass}
                disabled={saving || !(drafts[field.name] ?? '').trim()}
                onClick={() => save({ [field.name]: drafts[field.name] })}
              >
                Save
              </button>
            </div>
          </Field>
        ))}

        {!status.fernet_ready && (
          <p className="text-xs text-amber-400">
            FERNET_KEY is not set in .env — API keys can’t be stored until it is.
          </p>
        )}

        <div className="flex items-center gap-3">
          <button
            className={buttonClass}
            disabled={testing || !status.configured}
            onClick={runTest}
          >
            {testing ? 'Testing…' : 'Test connection'}
          </button>
          {!status.configured && (
            <span className="text-xs text-zinc-500">Save both keys to test.</span>
          )}
          {testResult && (
            <span className={`text-xs ${testResult.ok ? 'text-emerald-400' : 'text-red-400'}`}>
              {testResult.ok
                ? `Connected — ${testResult.currencies} balances visible.`
                : testResult.error}
            </span>
          )}
        </div>
      </div>

      {status.accounts.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold text-zinc-300">Exchange accounts</h2>
          <div className="overflow-x-auto rounded border border-zinc-800">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-zinc-800 text-left text-zinc-500">
                  <th className="px-3 py-2 font-normal">account</th>
                  <th className="px-3 py-2 font-normal">last synced</th>
                  <th className="px-3 py-2 font-normal">status</th>
                  <th className="px-3 py-2 font-normal">last run</th>
                  <th className="px-3 py-2 font-normal">pending items</th>
                </tr>
              </thead>
              <tbody>
                {status.accounts.map((account) => (
                  <tr key={account.account_id} className="border-b border-zinc-900">
                    <td className="px-3 py-2">
                      {account.name}
                      {account.provider && (
                        <span className="ml-1 rounded bg-zinc-800 px-1 text-[10px] text-zinc-500">
                          {account.provider}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap text-zinc-500">
                      {account.last_synced_at ? relTime(account.last_synced_at) : '—'}
                    </td>
                    <td
                      className={`px-3 py-2 ${
                        statusClass[account.last_status ?? ''] ?? 'text-zinc-500'
                      }`}
                    >
                      {account.last_status ?? 'never synced'}
                    </td>
                    <td className="px-3 py-2 text-zinc-400">
                      {account.last_run
                        ? `${account.last_run.trades_created} new · ${account.last_run.trades_skipped} skipped`
                        : '—'}
                    </td>
                    <td
                      className={`px-3 py-2 ${
                        account.unresolved_skips > 0 ? 'text-amber-400' : 'text-zinc-500'
                      }`}
                      title="items seen on Bitso that could not land yet (pending status or untracked symbol); they retry on every sync"
                    >
                      {account.unresolved_skips > 0 ? account.unresolved_skips : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  )
}
