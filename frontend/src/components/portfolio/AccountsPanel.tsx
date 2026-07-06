import { useCallback, useEffect, useState } from 'react'
import {
  api,
  relTime,
  type Account,
  type AccountBody,
  type AccountType,
  type ExchangeAccountStatus,
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

const ACCOUNT_TYPES: { value: AccountType; label: string }[] = [
  { value: 'exchange', label: 'exchange (Bitso, Binance…)' },
  { value: 'wallet', label: 'wallet (Ledger, cold storage)' },
  { value: 'bank', label: 'bank' },
  { value: 'brokerage', label: 'brokerage' },
  { value: 'manual', label: 'other / manual' },
]

// providers Plutus can sync read-only today
const EXCHANGE_PROVIDERS = ['bitso']

const statusClass: Record<string, string> = {
  success: 'text-emerald-400',
  partial: 'text-amber-400',
  failed: 'text-red-400',
  running: 'text-sky-400',
}

export default function AccountsPanel({
  accounts,
  onChanged,
}: {
  accounts: Account[]
  onChanged: () => void
}) {
  const [editing, setEditing] = useState<Account | 'new' | null>(null)
  const [exchange, setExchange] = useState<Record<number, ExchangeAccountStatus>>({})
  const [queued, setQueued] = useState<Record<number, boolean>>({})

  const hasExchange = accounts.some((a) => a.type === 'exchange')

  const loadExchange = useCallback(() => {
    if (!hasExchange) return
    api
      .exchangesStatus()
      .then((s) => setExchange(Object.fromEntries(s.accounts.map((a) => [a.account_id, a]))))
      .catch(() => {})
  }, [hasExchange])

  useEffect(loadExchange, [loadExchange])

  const syncNow = async (account: Account) => {
    setQueued((q) => ({ ...q, [account.id]: true }))
    try {
      await api.syncExchangeAccount(account.id)
    } catch {
      setQueued((q) => ({ ...q, [account.id]: false }))
      return
    }
    // the sync runs in the worker; refresh status a few times to catch the result
    let tries = 0
    const poll = setInterval(async () => {
      tries += 1
      loadExchange()
      if (tries >= 4) {
        clearInterval(poll)
        setQueued((q) => ({ ...q, [account.id]: false }))
      }
    }, 2500)
  }

  const remove = async (account: Account) => {
    if (
      account.transactions_count > 0 &&
      !window.confirm(
        `"${account.name}" has ${account.transactions_count} transactions — deleting the ` +
          'account deletes them too. Continue?',
      )
    ) {
      return
    }
    await api.deleteAccount(account.id)
    onChanged()
  }

  return (
    <div className="rounded border border-zinc-800 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-300">Accounts</h2>
        <button onClick={() => setEditing('new')} className={buttonClass}>
          Add account
        </button>
      </div>

      {accounts.length === 0 ? (
        <p className="py-4 text-center text-sm text-zinc-600">
          Start here: an account is where money or assets live — your exchange, your hardware
          wallet, each bank.
        </p>
      ) : (
        <ul className="space-y-2">
          {accounts.map((account) => {
            const sync = exchange[account.id]
            const isBitso = account.type === 'exchange' && account.provider === 'bitso'
            return (
              <li
                key={account.id}
                className="flex items-center justify-between gap-3 rounded border border-zinc-900 px-3 py-2"
              >
                <div className="min-w-0">
                  <span className="font-medium">{account.name}</span>
                  <span className="ml-2 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-500">
                    {account.type}
                  </span>
                  {account.provider && (
                    <span className="ml-1 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-500">
                      {account.provider}
                    </span>
                  )}
                  <p className="text-xs text-zinc-500">
                    {account.transactions_count} transactions
                    {account.bank_investments_count > 0 &&
                      ` · ${account.bank_investments_count} investments`}
                    {account.cash_balances.length > 0 &&
                      ' · cash ' +
                        account.cash_balances
                          .map((cash) => `${fmtMoney(cash.amount)} ${cash.currency}`)
                          .join(', ')}
                  </p>
                  {isBitso && (
                    <p className="mt-0.5 text-xs">
                      {queued[account.id] ? (
                        <span className="text-sky-400">sync queued…</span>
                      ) : sync?.last_status ? (
                        <>
                          <span className={statusClass[sync.last_status] ?? 'text-zinc-500'}>
                            {sync.last_status}
                          </span>
                          {sync.last_synced_at && (
                            <span className="text-zinc-500"> · {relTime(sync.last_synced_at)}</span>
                          )}
                          {sync.last_run && (
                            <span className="text-zinc-500">
                              {' · '}
                              {sync.last_run.trades_created} new · {sync.last_run.trades_skipped}{' '}
                              skipped
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="text-zinc-600">never synced</span>
                      )}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {isBitso && (
                    <button
                      onClick={() => syncNow(account)}
                      disabled={queued[account.id]}
                      className={buttonClass}
                    >
                      {queued[account.id] ? 'Syncing…' : 'Sync now'}
                    </button>
                  )}
                  <button
                    onClick={() => setEditing(account)}
                    className="text-xs text-zinc-500 hover:text-zinc-300"
                  >
                    edit
                  </button>
                  <button
                    onClick={() => remove(account)}
                    className="text-xs text-zinc-600 hover:text-red-400"
                  >
                    delete
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {editing !== null && (
        <AccountForm
          account={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            onChanged()
          }}
        />
      )}
    </div>
  )
}

function AccountForm({
  account,
  onClose,
  onSaved,
}: {
  account: Account | null
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(account?.name ?? '')
  const [type, setType] = useState<AccountType>(account?.type ?? 'exchange')
  const [provider, setProvider] = useState(account?.provider ?? '')
  const [currency, setCurrencyValue] = useState(account?.currency ?? 'USD')
  const [note, setNote] = useState(account?.note ?? '')
  const [errors, setErrors] = useState<ServerError[]>([])
  const [saving, setSaving] = useState(false)

  const save = async () => {
    setSaving(true)
    setErrors([])
    const body: AccountBody = {
      name,
      type,
      provider: type === 'exchange' ? provider || null : null,
      currency,
      note: note || null,
    }
    try {
      if (account) await api.updateAccount(account.id, body)
      else await api.createAccount(body)
      onSaved()
    } catch (e) {
      setErrors(parseServerErrors(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title={account ? `Edit "${account.name}"` : 'New account'} onClose={onClose}>
      <div className="grid grid-cols-2 gap-3">
        <Field label="name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Bitso"
            className={`${inputClass} w-full`}
          />
        </Field>
        <Field label="type">
          <select
            value={type}
            onChange={(e) => setType(e.target.value as AccountType)}
            className={`${inputClass} w-full`}
          >
            {ACCOUNT_TYPES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>
        {type === 'exchange' && (
          <Field label="provider (enables sync)">
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              className={`${inputClass} w-full`}
            >
              <option value="">none</option>
              {EXCHANGE_PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label="main currency">
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
          disabled={saving || !name}
          className="rounded bg-sky-700 px-4 py-1.5 text-sm text-white hover:bg-sky-600 disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}
