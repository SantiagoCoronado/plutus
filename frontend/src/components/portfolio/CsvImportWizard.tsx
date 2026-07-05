import { useState } from 'react'
import { api, type Account, type CsvCommitResult, type CsvPreview } from '../../api/client'
import {
  ErrorList,
  Field,
  Modal,
  buttonClass,
  inputClass,
  parseServerErrors,
  type ServerError,
} from './shared'

const TARGETS = [
  { key: 'ts', label: 'date', required: true },
  { key: 'type', label: 'type', required: true },
  { key: 'quantity', label: 'quantity / amount', required: true },
  { key: 'symbol', label: 'symbol' },
  { key: 'book', label: 'pair/book (btc_mxn)' },
  { key: 'currency', label: 'currency' },
  { key: 'price', label: 'price' },
  { key: 'fees', label: 'fees' },
  { key: 'external_id', label: 'transaction id' },
  { key: 'note', label: 'note' },
]

export default function CsvImportWizard({
  accounts,
  onClose,
  onImported,
}: {
  accounts: Account[]
  onClose: () => void
  onImported: () => void
}) {
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? 0)
  const [content, setContent] = useState('')
  const [preview, setPreview] = useState<CsvPreview | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [result, setResult] = useState<CsvCommitResult | null>(null)
  const [errors, setErrors] = useState<ServerError[]>([])
  const [busy, setBusy] = useState(false)

  const readFile = (file: File) => {
    const reader = new FileReader()
    reader.onload = () => setContent(String(reader.result ?? ''))
    reader.readAsText(file)
  }

  const runPreview = async () => {
    setBusy(true)
    setErrors([])
    try {
      const parsed = await api.csvPreview(content)
      setPreview(parsed)
      setMapping(parsed.suggested_mapping)
    } catch (e) {
      setErrors(parseServerErrors(e))
    } finally {
      setBusy(false)
    }
  }

  const commit = async () => {
    setBusy(true)
    setErrors([])
    try {
      const cleaned = Object.fromEntries(
        Object.entries(mapping).filter(([, column]) => column !== ''),
      )
      setResult(await api.csvCommit({ account_id: accountId, content, mapping: cleaned }))
      onImported()
    } catch (e) {
      setErrors(parseServerErrors(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Import transactions from CSV" onClose={onClose} wide>
      {result === null ? (
        <>
          <div className="grid grid-cols-2 gap-3">
            <Field label="into account">
              <select
                value={accountId}
                onChange={(e) => setAccountId(Number(e.target.value))}
                className={`${inputClass} w-full`}
              >
                {accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="file">
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => e.target.files?.[0] && readFile(e.target.files[0])}
                className="block w-full text-xs text-zinc-400 file:mr-2 file:rounded file:border file:border-zinc-700 file:bg-zinc-900 file:px-2 file:py-1 file:text-xs file:text-zinc-300"
              />
            </Field>
          </div>
          <Field label="…or paste the CSV text">
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={5}
              placeholder={'date,type,symbol,quantity,price\n2026-03-02,buy,BTC,0.05,1800000'}
              className={`${inputClass} w-full font-mono text-xs`}
            />
          </Field>

          {preview === null ? (
            <div className="flex justify-end gap-2">
              <button onClick={onClose} className={buttonClass}>
                Cancel
              </button>
              <button
                onClick={runPreview}
                disabled={busy || !content.trim()}
                className="rounded bg-sky-700 px-4 py-1.5 text-sm text-white hover:bg-sky-600 disabled:opacity-50"
              >
                {busy ? 'Reading…' : 'Preview'}
              </button>
            </div>
          ) : (
            <>
              <p className="text-xs text-zinc-500">
                {preview.row_count} rows
                {preview.preset && (
                  <span className="ml-2 rounded bg-emerald-900/40 px-1.5 py-0.5 text-emerald-300">
                    {preview.preset} format detected
                  </span>
                )}
              </p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                {TARGETS.map((target) => (
                  <label key={target.key} className="flex items-center gap-2 text-xs">
                    <span className="w-32 text-zinc-500">
                      {target.label}
                      {target.required && <span className="text-red-400"> *</span>}
                    </span>
                    <select
                      value={mapping[target.key] ?? ''}
                      onChange={(e) =>
                        setMapping({ ...mapping, [target.key]: e.target.value })
                      }
                      className={`${inputClass} flex-1`}
                    >
                      <option value="">—</option>
                      {preview.columns.map((column) => (
                        <option key={column} value={column}>
                          {column}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
              {preview.sample_rows.length > 0 && (
                <div className="overflow-x-auto rounded border border-zinc-900">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="border-b border-zinc-800 text-left text-zinc-500">
                        {preview.columns.map((column) => (
                          <th key={column} className="px-2 py-1 font-normal">
                            {column}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {preview.sample_rows.map((row, i) => (
                        <tr key={i} className="border-b border-zinc-900 text-zinc-400">
                          {preview.columns.map((column) => (
                            <td key={column} className="px-2 py-1">
                              {row[column]}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <ErrorList errors={errors} />
              <div className="flex justify-end gap-2">
                <button onClick={() => setPreview(null)} className={buttonClass}>
                  Back
                </button>
                <button
                  onClick={commit}
                  disabled={busy}
                  className="rounded bg-sky-700 px-4 py-1.5 text-sm text-white hover:bg-sky-600 disabled:opacity-50"
                >
                  {busy ? 'Importing…' : `Import ${preview.row_count} rows`}
                </button>
              </div>
            </>
          )}
          {preview === null && <ErrorList errors={errors} />}
        </>
      ) : (
        <>
          <div className="space-y-1 text-sm">
            <p className="text-emerald-300">{result.created} transactions imported</p>
            {result.skipped_duplicates > 0 && (
              <p className="text-zinc-400">
                {result.skipped_duplicates} skipped (already imported earlier)
              </p>
            )}
            {result.errors.length > 0 && (
              <div className="rounded border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-xs text-amber-300">
                <p className="mb-1">{result.errors.length} rows could not be imported:</p>
                {result.errors.slice(0, 8).map((err, i) => (
                  <p key={i}>
                    {err.row !== undefined ? `line ${err.row}: ` : ''}
                    {err.error}
                  </p>
                ))}
                {result.errors.length > 8 && <p>… and {result.errors.length - 8} more</p>}
              </div>
            )}
          </div>
          <div className="flex justify-end">
            <button
              onClick={onClose}
              className="rounded bg-sky-700 px-4 py-1.5 text-sm text-white hover:bg-sky-600"
            >
              Done
            </button>
          </div>
        </>
      )}
    </Modal>
  )
}
