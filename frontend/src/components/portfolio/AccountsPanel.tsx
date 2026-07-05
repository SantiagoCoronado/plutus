import { useState } from 'react'
import { api, type Account, type AccountBody, type AccountType } from '../../api/client'
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

export default function AccountsPanel({
  accounts,
  onChanged,
}: {
  accounts: Account[]
  onChanged: () => void
}) {
  const [editing, setEditing] = useState<Account | 'new' | null>(null)

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
          {accounts.map((account) => (
            <li
              key={account.id}
              className="flex items-center justify-between gap-3 rounded border border-zinc-900 px-3 py-2"
            >
              <div className="min-w-0">
                <span className="font-medium">{account.name}</span>
                <span className="ml-2 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-500">
                  {account.type}
                </span>
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
              </div>
              <div className="flex shrink-0 gap-2">
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
          ))}
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
  const [currency, setCurrencyValue] = useState(account?.currency ?? 'USD')
  const [note, setNote] = useState(account?.note ?? '')
  const [errors, setErrors] = useState<ServerError[]>([])
  const [saving, setSaving] = useState(false)

  const save = async () => {
    setSaving(true)
    setErrors([])
    const body: AccountBody = { name, type, currency, note: note || null }
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
