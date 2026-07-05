import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type Account,
  type SearchResultItem,
  type Transaction,
  type TransactionBody,
  type TransactionType,
} from '../../api/client'
import {
  ErrorList,
  Field,
  Modal,
  buttonClass,
  fmtMoney,
  inputClass,
  parseServerErrors,
  type ServerError,
} from './shared'

const PAGE_SIZE = 25

const TYPE_OPTIONS: { value: TransactionType; label: string }[] = [
  { value: 'buy', label: 'buy' },
  { value: 'sell', label: 'sell' },
  { value: 'deposit', label: 'deposit (cash in)' },
  { value: 'withdrawal', label: 'withdrawal (cash out)' },
  { value: 'dividend', label: 'dividend' },
  { value: 'interest', label: 'interest' },
  { value: 'fee', label: 'fee' },
  { value: 'transfer_in', label: 'transfer in (asset arrives)' },
  { value: 'transfer_out', label: 'transfer out (asset leaves)' },
]

const ASSET_TYPES = new Set(['buy', 'sell', 'dividend', 'transfer_in', 'transfer_out'])
const PRICE_TYPES = new Set(['buy', 'sell', 'transfer_in'])

export default function TransactionsLedger({
  accounts,
  refreshKey,
  onChanged,
}: {
  accounts: Account[]
  refreshKey: number
  onChanged: () => void
}) {
  const [items, setItems] = useState<Transaction[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [accountFilter, setAccountFilter] = useState<number | ''>('')
  const [adding, setAdding] = useState(false)
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    const params: Parameters<typeof api.transactions>[0] = { limit: PAGE_SIZE, offset }
    if (accountFilter !== '') params.account_id = accountFilter
    const page = await api.transactions(params)
    setItems(page.items)
    setTotal(page.total)
  }, [offset, accountFilter])

  useEffect(() => {
    load().catch(() => setItems([]))
  }, [load, refreshKey])

  const remove = async (txn: Transaction) => {
    setMessage('')
    try {
      await api.deleteTransaction(txn.id)
      onChanged()
    } catch (e) {
      const [first] = parseServerErrors(e)
      setMessage(first.error)
    }
  }

  return (
    <div className="rounded border border-zinc-800 p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-300">Transactions</h2>
        <div className="flex items-center gap-2">
          <select
            value={accountFilter}
            onChange={(e) => {
              setOffset(0)
              setAccountFilter(e.target.value ? Number(e.target.value) : '')
            }}
            className={`${inputClass} text-xs`}
          >
            <option value="">all accounts</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
          <button
            onClick={() => setAdding(true)}
            disabled={accounts.length === 0}
            className={buttonClass}
          >
            Add transaction
          </button>
        </div>
      </div>

      {message && <p className="mb-2 text-xs text-red-400">{message}</p>}

      {items.length === 0 ? (
        <p className="py-4 text-center text-sm text-zinc-600">no transactions yet</p>
      ) : (
        <>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-xs text-zinc-500">
                <th className="px-2 py-2 font-normal">date</th>
                <th className="px-2 py-2 font-normal">type</th>
                <th className="px-2 py-2 font-normal">asset</th>
                <th className="px-2 py-2 font-normal">account</th>
                <th className="px-2 py-2 text-right font-normal">quantity</th>
                <th className="px-2 py-2 text-right font-normal">price</th>
                <th className="px-2 py-2 text-right font-normal">fees</th>
                <th className="px-2 py-2 font-normal" />
              </tr>
            </thead>
            <tbody>
              {items.map((txn) => (
                <tr key={txn.id} className="border-b border-zinc-900 text-xs">
                  <td className="px-2 py-1.5 text-zinc-400">{txn.ts.slice(0, 10)}</td>
                  <td className="px-2 py-1.5">{txn.type.replace('_', ' ')}</td>
                  <td className="px-2 py-1.5 font-medium">{txn.symbol ?? '—'}</td>
                  <td className="px-2 py-1.5 text-zinc-500">{txn.account_name}</td>
                  <td className="px-2 py-1.5 text-right font-mono">
                    {txn.quantity.toLocaleString(undefined, { maximumFractionDigits: 8 })}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-zinc-400">
                    {txn.price !== null ? `${fmtMoney(txn.price)} ${txn.currency}` : txn.currency}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-zinc-500">
                    {txn.fees > 0 ? fmtMoney(txn.fees) : '—'}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <button
                      onClick={() => remove(txn)}
                      className="text-zinc-600 hover:text-red-400"
                    >
                      delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {total > PAGE_SIZE && (
            <div className="mt-2 flex items-center justify-between text-xs text-zinc-500">
              <span>
                {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  disabled={offset === 0}
                  className={buttonClass}
                >
                  ← newer
                </button>
                <button
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                  disabled={offset + PAGE_SIZE >= total}
                  className={buttonClass}
                >
                  older →
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {adding && (
        <TransactionForm
          accounts={accounts}
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false)
            onChanged()
          }}
        />
      )}
    </div>
  )
}

function TransactionForm({
  accounts,
  onClose,
  onSaved,
}: {
  accounts: Account[]
  onClose: () => void
  onSaved: () => void
}) {
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? 0)
  const [type, setType] = useState<TransactionType>('buy')
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [quantity, setQuantity] = useState('')
  const [price, setPrice] = useState('')
  const [fees, setFees] = useState('')
  const [currency, setCurrencyValue] = useState('USD')
  const [note, setNote] = useState('')
  const [symbolQuery, setSymbolQuery] = useState('')
  const [assetId, setAssetId] = useState<number | null>(null)
  const [suggestions, setSuggestions] = useState<SearchResultItem[]>([])
  const [errors, setErrors] = useState<ServerError[]>([])
  const [saving, setSaving] = useState(false)

  const needsAsset = ASSET_TYPES.has(type)
  const needsPrice = PRICE_TYPES.has(type)

  useEffect(() => {
    if (!needsAsset || assetId !== null || symbolQuery.length < 1) {
      setSuggestions([])
      return
    }
    const handle = setTimeout(() => {
      api
        .search(symbolQuery)
        .then(({ results }) => setSuggestions(results.filter((r) => r.tracked).slice(0, 6)))
        .catch(() => setSuggestions([]))
    }, 250)
    return () => clearTimeout(handle)
  }, [symbolQuery, assetId, needsAsset])

  const save = async () => {
    setSaving(true)
    setErrors([])
    const body: TransactionBody = {
      account_id: accountId,
      asset_id: needsAsset ? assetId : null,
      type,
      ts: `${date}T12:00:00Z`,
      quantity: Number(quantity),
      price: price === '' ? null : Number(price),
      fees: fees === '' ? 0 : Number(fees),
      currency,
      note: note || null,
    }
    try {
      await api.createTransaction(body)
      onSaved()
    } catch (e) {
      setErrors(parseServerErrors(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title="New transaction" onClose={onClose}>
      <div className="grid grid-cols-2 gap-3">
        <Field label="account">
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
        <Field label="type">
          <select
            value={type}
            onChange={(e) => setType(e.target.value as TransactionType)}
            className={`${inputClass} w-full`}
          >
            {TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>
        {needsAsset && (
          <Field label="asset (tracked symbols only)">
            <div className="relative">
              <input
                value={symbolQuery}
                onChange={(e) => {
                  setSymbolQuery(e.target.value.toUpperCase())
                  setAssetId(null)
                }}
                placeholder="BTC, AAPL…"
                className={`${inputClass} w-full ${assetId !== null ? 'border-emerald-700' : ''}`}
              />
              {suggestions.length > 0 && (
                <ul className="absolute z-10 mt-1 w-full rounded border border-zinc-700 bg-zinc-900 text-xs">
                  {suggestions.map((item) => (
                    <li key={`${item.symbol}-${item.asset_class}`}>
                      <button
                        onClick={() => {
                          setAssetId(item.asset_id)
                          setSymbolQuery(item.symbol)
                          setSuggestions([])
                        }}
                        className="flex w-full justify-between px-2 py-1.5 text-left hover:bg-zinc-800"
                      >
                        <span className="font-medium">{item.symbol}</span>
                        <span className="text-zinc-500">{item.asset_class}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Field>
        )}
        <Field label="date">
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className={`${inputClass} w-full`}
          />
        </Field>
        <Field label={needsAsset ? 'quantity (units)' : 'amount'}>
          <input
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            placeholder={needsAsset ? '0.05' : '10000'}
            className={`${inputClass} w-full`}
          />
        </Field>
        {needsPrice && (
          <Field label={type === 'transfer_in' ? 'original cost per unit (keeps basis)' : 'price per unit'}>
            <input
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              placeholder="1800000"
              className={`${inputClass} w-full`}
            />
          </Field>
        )}
        <Field label="currency of the money side">
          <select
            value={currency}
            onChange={(e) => setCurrencyValue(e.target.value)}
            className={`${inputClass} w-full`}
          >
            <option>USD</option>
            <option>MXN</option>
            <option>EUR</option>
          </select>
        </Field>
        <Field label="fees">
          <input
            value={fees}
            onChange={(e) => setFees(e.target.value)}
            placeholder="0"
            className={`${inputClass} w-full`}
          />
        </Field>
        <Field label="note">
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className={`${inputClass} w-full`}
          />
        </Field>
      </div>

      <ErrorList errors={errors} />

      <div className="flex justify-end gap-2">
        <button onClick={onClose} className={buttonClass}>
          Cancel
        </button>
        <button
          onClick={save}
          disabled={saving || !quantity || (needsAsset && assetId === null)}
          className="rounded bg-sky-700 px-4 py-1.5 text-sm text-white hover:bg-sky-600 disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}
