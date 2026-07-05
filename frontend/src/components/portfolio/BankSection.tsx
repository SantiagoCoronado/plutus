import { useState } from 'react'
import {
  api,
  fmtPct,
  type Account,
  type BankInvestment,
  type BankInvestmentBody,
  type RateTier,
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

const STATUS_STYLES: Record<string, string> = {
  active: 'bg-emerald-900/40 text-emerald-300',
  matured: 'bg-amber-900/40 text-amber-300',
  closed: 'bg-zinc-800 text-zinc-500',
}

export default function BankSection({
  investments,
  accounts,
  onChanged,
}: {
  investments: BankInvestment[]
  accounts: Account[]
  onChanged: () => void
}) {
  const [editing, setEditing] = useState<BankInvestment | 'new' | null>(null)
  const bankAccounts = accounts.filter((account) => account.type === 'bank')

  const remove = async (investment: BankInvestment) => {
    await api.deleteBankInvestment(investment.id)
    onChanged()
  }

  return (
    <div className="rounded border border-zinc-800 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-300">Bank investments</h2>
        <button
          onClick={() => setEditing('new')}
          disabled={bankAccounts.length === 0}
          title={bankAccounts.length === 0 ? 'add an account of type "bank" first' : undefined}
          className={buttonClass}
        >
          Add investment
        </button>
      </div>

      {investments.length === 0 ? (
        <p className="py-4 text-center text-sm text-zinc-600">
          CETES, pagarés, and interest-bearing balances live here — bookkeeping plus interest
          math, never a bank connection.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-xs text-zinc-500">
                <th className="px-2 py-2 font-normal">name</th>
                <th className="px-2 py-2 font-normal">account</th>
                <th className="px-2 py-2 text-right font-normal">principal</th>
                <th className="px-2 py-2 text-right font-normal">rate</th>
                <th className="px-2 py-2 text-right font-normal">accrued</th>
                <th className="px-2 py-2 text-right font-normal">value</th>
                <th className="px-2 py-2 text-right font-normal">matures</th>
                <th className="px-2 py-2 font-normal" />
              </tr>
            </thead>
            <tbody>
              {investments.map((investment) => (
                <tr key={investment.id} className="border-b border-zinc-900">
                  <td className="px-2 py-2">
                    <span className="font-medium">{investment.name}</span>
                    <span
                      className={`ml-2 rounded px-1.5 py-0.5 text-[10px] ${STATUS_STYLES[investment.status]}`}
                    >
                      {investment.status}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-xs text-zinc-500">{investment.account_name}</td>
                  <td className="px-2 py-2 text-right font-mono text-xs">
                    {fmtMoney(investment.principal)}{' '}
                    <span className="text-zinc-600">{investment.currency}</span>
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-xs">
                    {fmtPct(investment.effective_annual_rate, 2)}
                    {investment.rate_tiers ? ' (tiered)' : ''}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-xs text-emerald-400">
                    +{fmtMoney(investment.accrued_interest)}
                  </td>
                  <td className="px-2 py-2 text-right font-mono text-xs">
                    {fmtMoney(investment.current_value)}
                  </td>
                  <td className="px-2 py-2 text-right text-xs text-zinc-400">
                    {investment.kind === 'demand'
                      ? 'on demand'
                      : investment.days_to_maturity !== null
                        ? `${investment.maturity_date} (${investment.days_to_maturity}d)` +
                          (investment.auto_renew ? ' ↻' : '')
                        : (investment.maturity_date ?? '—')}
                  </td>
                  <td className="px-2 py-2 text-right">
                    <button
                      onClick={() => setEditing(investment)}
                      className="mr-2 text-xs text-zinc-500 hover:text-zinc-300"
                    >
                      edit
                    </button>
                    <button
                      onClick={() => remove(investment)}
                      className="text-xs text-zinc-600 hover:text-red-400"
                    >
                      delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing !== null && (
        <BankInvestmentForm
          investment={editing === 'new' ? null : editing}
          bankAccounts={bankAccounts}
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

function BankInvestmentForm({
  investment,
  bankAccounts,
  onClose,
  onSaved,
}: {
  investment: BankInvestment | null
  bankAccounts: Account[]
  onClose: () => void
  onSaved: () => void
}) {
  const [accountId, setAccountId] = useState(investment?.account_id ?? bankAccounts[0]?.id ?? 0)
  const [name, setName] = useState(investment?.name ?? '')
  const [kind, setKind] = useState<'demand' | 'fixed_term'>(investment?.kind ?? 'fixed_term')
  const [principal, setPrincipal] = useState(String(investment?.principal ?? ''))
  const [currency, setCurrencyValue] = useState(investment?.currency ?? 'MXN')
  const [annualRate, setAnnualRate] = useState(
    investment ? String(investment.annual_rate * 100) : '',
  )
  const [tiered, setTiered] = useState(Boolean(investment?.rate_tiers))
  const [tiers, setTiers] = useState<RateTier[]>(
    investment?.rate_tiers ?? [
      { up_to: 25000, annual_rate: 0.15 },
      { up_to: null, annual_rate: 0.05 },
    ],
  )
  const [dayCount, setDayCount] = useState(investment?.day_count ?? 'act360')
  const [compounding, setCompounding] = useState(investment?.compounding ?? 'at_maturity')
  const [startDate, setStartDate] = useState(
    investment?.start_date ?? new Date().toISOString().slice(0, 10),
  )
  const [termDays, setTermDays] = useState(String(investment?.term_days ?? '28'))
  const [autoRenew, setAutoRenew] = useState(investment?.auto_renew ?? false)
  const [status, setStatus] = useState(investment?.status ?? 'active')
  const [note, setNote] = useState(investment?.note ?? '')
  const [errors, setErrors] = useState<ServerError[]>([])
  const [saving, setSaving] = useState(false)

  const save = async () => {
    setSaving(true)
    setErrors([])
    const body: BankInvestmentBody = {
      account_id: accountId,
      name,
      kind,
      principal: Number(principal),
      currency,
      annual_rate: Number(annualRate) / 100,
      rate_tiers: tiered ? tiers : null,
      day_count: dayCount,
      compounding,
      start_date: startDate,
      term_days: kind === 'fixed_term' ? Number(termDays) : null,
      auto_renew: autoRenew,
      status,
      note: note || null,
    }
    try {
      if (investment) await api.updateBankInvestment(investment.id, body)
      else await api.createBankInvestment(body)
      onSaved()
    } catch (e) {
      setErrors(parseServerErrors(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title={investment ? `Edit "${investment.name}"` : 'New bank investment'} onClose={onClose}>
      <div className="grid grid-cols-2 gap-3">
        <Field label="account (bank)">
          <select
            value={accountId}
            onChange={(e) => setAccountId(Number(e.target.value))}
            className={`${inputClass} w-full`}
          >
            {bankAccounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Pagaré 28 días"
            className={`${inputClass} w-full`}
          />
        </Field>
        <Field label="kind">
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as 'demand' | 'fixed_term')}
            className={`${inputClass} w-full`}
          >
            <option value="fixed_term">fixed term (pagaré / CETES)</option>
            <option value="demand">demand (interest-bearing balance)</option>
          </select>
        </Field>
        <Field label="status">
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value as typeof status)}
            className={`${inputClass} w-full`}
          >
            <option value="active">active</option>
            <option value="matured">matured</option>
            <option value="closed">closed</option>
          </select>
        </Field>
        <Field label="principal">
          <input
            value={principal}
            onChange={(e) => setPrincipal(e.target.value)}
            placeholder="100000"
            className={`${inputClass} w-full`}
          />
        </Field>
        <Field label="currency">
          <select
            value={currency}
            onChange={(e) => setCurrencyValue(e.target.value)}
            className={`${inputClass} w-full`}
          >
            <option>MXN</option>
            <option>USD</option>
          </select>
        </Field>
        <Field label="annual rate (%)">
          <input
            value={annualRate}
            onChange={(e) => setAnnualRate(e.target.value)}
            placeholder="10.5"
            disabled={tiered}
            className={`${inputClass} w-full disabled:opacity-50`}
          />
        </Field>
        <Field label="day count / compounding">
          <div className="flex gap-2">
            <select
              value={dayCount}
              onChange={(e) => setDayCount(e.target.value as typeof dayCount)}
              className={`${inputClass} flex-1`}
            >
              <option value="act360">ACT/360</option>
              <option value="act365">ACT/365</option>
            </select>
            <select
              value={compounding}
              onChange={(e) => setCompounding(e.target.value as typeof compounding)}
              className={`${inputClass} flex-1`}
            >
              <option value="at_maturity">simple</option>
              <option value="monthly">monthly</option>
              <option value="daily">daily</option>
            </select>
          </div>
        </Field>
        <Field label="start date">
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className={`${inputClass} w-full`}
          />
        </Field>
        {kind === 'fixed_term' ? (
          <Field label="term (days)">
            <div className="flex items-center gap-3">
              <input
                value={termDays}
                onChange={(e) => setTermDays(e.target.value)}
                placeholder="28"
                className={`${inputClass} w-24`}
              />
              <label className="flex items-center gap-1.5 text-xs text-zinc-400">
                <input
                  type="checkbox"
                  checked={autoRenew}
                  onChange={(e) => setAutoRenew(e.target.checked)}
                />
                auto-renew
              </label>
            </div>
          </Field>
        ) : (
          <div />
        )}
      </div>

      <label className="flex items-center gap-2 text-xs text-zinc-400">
        <input type="checkbox" checked={tiered} onChange={(e) => setTiered(e.target.checked)} />
        tiered rate (e.g. 15% on the first 25,000 — 5% above)
      </label>
      {tiered && (
        <div className="space-y-2">
          {tiers.map((tier, i) => (
            <div key={i} className="flex items-center gap-2 text-xs">
              <span className="w-10 text-zinc-500">up to</span>
              <input
                value={tier.up_to ?? ''}
                placeholder="rest"
                onChange={(e) =>
                  setTiers(
                    tiers.map((t, j) =>
                      j === i ? { ...t, up_to: e.target.value ? Number(e.target.value) : null } : t,
                    ),
                  )
                }
                className={`${inputClass} w-32`}
              />
              <span className="text-zinc-500">earns</span>
              <input
                value={tier.annual_rate * 100}
                onChange={(e) =>
                  setTiers(
                    tiers.map((t, j) =>
                      j === i ? { ...t, annual_rate: Number(e.target.value) / 100 } : t,
                    ),
                  )
                }
                className={`${inputClass} w-20`}
              />
              <span className="text-zinc-500">% a year</span>
              <button
                onClick={() => setTiers(tiers.filter((_, j) => j !== i))}
                className="text-zinc-600 hover:text-red-400"
              >
                ×
              </button>
            </div>
          ))}
          <button
            onClick={() => setTiers([...tiers, { up_to: null, annual_rate: 0.05 }])}
            className="text-xs text-zinc-500 hover:text-zinc-300"
          >
            + add tier
          </button>
        </div>
      )}

      <Field label="note">
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          className={`${inputClass} w-full`}
        />
      </Field>

      <ErrorList errors={errors} />

      <div className="flex justify-end gap-2">
        <button onClick={onClose} className={buttonClass}>
          Cancel
        </button>
        <button
          onClick={save}
          disabled={saving || !name || !principal || accountId === 0}
          className="rounded bg-sky-700 px-4 py-1.5 text-sm text-white hover:bg-sky-600 disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}
