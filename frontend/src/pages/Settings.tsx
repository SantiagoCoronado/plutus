import { useCallback, useEffect, useState } from 'react'
import {
  api,
  fmtNum,
  relTime,
  type AgentAction,
  type AgentUsage,
  type LLMProviderName,
  type LLMSettings,
  type TestConnectionResult,
} from '../api/client'
import { ErrorList, Field, inputClass, buttonClass, parseServerErrors, type ServerError } from '../components/portfolio/shared'
import BitsoSection from '../components/settings/BitsoSection'
import MorningBriefSection from '../components/settings/MorningBriefSection'
import IngestionHealthSection from '../components/settings/IngestionHealthSection'

const PROVIDERS: { value: LLMProviderName; label: string; hint: string }[] = [
  {
    value: 'claude-subscription',
    label: 'Claude (subscription)',
    hint: 'Uses your Claude plan via the agent sidecar — no per-token cost.',
  },
  { value: 'anthropic-api', label: 'Anthropic API', hint: 'Pay per token with an API key.' },
  { value: 'openai', label: 'OpenAI', hint: 'GPT models with an OpenAI API key.' },
  { value: 'google', label: 'Google Gemini', hint: 'Gemini models with a Google AI key.' },
  { value: 'openrouter', label: 'OpenRouter', hint: 'Many models behind one key.' },
  { value: 'ollama', label: 'Ollama (local)', hint: 'Free local models; quality varies.' },
]

const KEY_FIELDS: { name: string; label: string; provider: LLMProviderName }[] = [
  { name: 'anthropic_api_key', label: 'Anthropic API key', provider: 'anthropic-api' },
  { name: 'openai_api_key', label: 'OpenAI API key', provider: 'openai' },
  { name: 'google_api_key', label: 'Google API key', provider: 'google' },
  { name: 'openrouter_api_key', label: 'OpenRouter API key', provider: 'openrouter' },
]

const sourceBadge: Record<AgentAction['source'], string> = {
  app: 'bg-sky-950 text-sky-300',
  task: 'bg-violet-950 text-violet-300',
  mcp: 'bg-amber-950 text-amber-300',
}

export default function Settings() {
  const [settings, setSettings] = useState<LLMSettings | null>(null)
  const [usage, setUsage] = useState<AgentUsage | null>(null)
  const [actions, setActions] = useState<AgentAction[]>([])
  const [model, setModel] = useState('')
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<ServerError[]>([])
  const [testResult, setTestResult] = useState<TestConnectionResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    api.agentSettings().then((s) => {
      setSettings(s)
      setModel(s.model)
    })
    api.agentUsage().then(setUsage)
    api.agentActions({ limit: 30 }).then(setActions)
  }, [])

  useEffect(load, [load])

  const save = async (patch: Parameters<typeof api.updateAgentSettings>[0]) => {
    setSaving(true)
    setErrors([])
    try {
      const next = await api.updateAgentSettings(patch)
      setSettings(next)
      setModel(next.model)
      setKeyDrafts({})
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
      setTestResult(await api.testAgentConnection())
    } catch (e) {
      setErrors(parseServerErrors(e))
    } finally {
      setTesting(false)
    }
  }

  if (!settings) return <p className="p-6 text-sm text-zinc-500">Loading settings…</p>

  const active = PROVIDERS.find((p) => p.value === settings.provider)
  const usedPct = usage ? Math.min(100, (usage.tokens_used / usage.daily_token_budget) * 100) : 0

  return (
    <div className="mx-auto max-w-3xl space-y-8 p-6">
      <section className="space-y-4">
        <h1 className="text-lg font-semibold">AI agent settings</h1>
        <ErrorList errors={errors} />

        <div className="space-y-4 rounded border border-zinc-800 p-4">
          <Field label="LLM provider">
            <select
              className={inputClass}
              value={settings.provider}
              onChange={(e) => save({ provider: e.target.value as LLMProviderName })}
            >
              {PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
            {active && <p className="mt-1 text-xs text-zinc-500">{active.hint}</p>}
          </Field>

          {settings.provider === 'claude-subscription' && (
            <div
              className={`rounded border px-3 py-2 text-xs ${
                settings.sidecar.reachable && settings.sidecar.auth_ok
                  ? 'border-emerald-900/60 bg-emerald-950/30 text-emerald-300'
                  : 'border-amber-900/60 bg-amber-950/30 text-amber-300'
              }`}
            >
              {settings.sidecar.reachable && settings.sidecar.auth_ok
                ? 'Sidecar healthy — subscription auth present.'
                : settings.sidecar.reachable
                  ? 'Sidecar is up but missing CLAUDE_CODE_OAUTH_TOKEN — run `claude setup-token` and add it to .env.'
                  : `Sidecar not reachable at ${settings.sidecar.url} — start the agent-sidecar service.`}
            </div>
          )}

          <Field label="Model override (optional)">
            <div className="flex gap-2">
              <input
                className={inputClass}
                value={model}
                placeholder="provider default"
                onChange={(e) => setModel(e.target.value)}
              />
              <button
                className={buttonClass}
                disabled={saving || model === settings.model}
                onClick={() => save({ model })}
              >
                Save
              </button>
            </div>
          </Field>

          {KEY_FIELDS.map((field) => (
            <Field key={field.name} label={field.label}>
              <div className="flex gap-2">
                <input
                  type="password"
                  className={inputClass}
                  placeholder={settings.keys[field.name] ?? 'not set'}
                  value={keyDrafts[field.name] ?? ''}
                  onChange={(e) =>
                    setKeyDrafts((d) => ({ ...d, [field.name]: e.target.value }))
                  }
                />
                <button
                  className={buttonClass}
                  disabled={saving || !(keyDrafts[field.name] ?? '').trim()}
                  onClick={() => save({ keys: { [field.name]: keyDrafts[field.name] } })}
                >
                  Save
                </button>
              </div>
            </Field>
          ))}
          {!settings.fernet_configured && (
            <p className="text-xs text-amber-400">
              FERNET_KEY is not set in .env — API keys can’t be stored until it is.
            </p>
          )}

          <div className="flex items-center gap-3">
            <button className={buttonClass} disabled={testing} onClick={runTest}>
              {testing ? 'Testing…' : 'Test connection'}
            </button>
            {testResult && (
              <span
                className={`text-xs ${testResult.ok ? 'text-emerald-400' : 'text-red-400'}`}
              >
                {testResult.detail}
              </span>
            )}
          </div>
        </div>
      </section>

      {usage && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-zinc-300">Today’s token usage</h2>
          <div className="rounded border border-zinc-800 p-4">
            <div className="mb-2 flex justify-between text-xs text-zinc-400">
              <span>
                {fmtNum(usage.tokens_used, 0)} of {fmtNum(usage.daily_token_budget, 0)} tokens
              </span>
              <span>{usage.date}</span>
            </div>
            <div className="h-2 overflow-hidden rounded bg-zinc-900">
              <div
                className={`h-full ${usedPct > 90 ? 'bg-red-500' : usedPct > 70 ? 'bg-amber-500' : 'bg-sky-600'}`}
                style={{ width: `${usedPct}%` }}
              />
            </div>
          </div>
        </section>
      )}

      <section className="space-y-2">
        <h2 className="text-sm font-semibold text-zinc-300">Recent agent actions</h2>
        {actions.length === 0 ? (
          <p className="rounded border border-dashed border-zinc-800 p-6 text-center text-xs text-zinc-500">
            No agent actions yet — they appear here whenever the agent uses a tool
            (chat, research tasks, or MCP).
          </p>
        ) : (
          <div className="overflow-x-auto rounded border border-zinc-800">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-zinc-800 text-left text-zinc-500">
                  <th className="px-3 py-2 font-normal">when</th>
                  <th className="px-3 py-2 font-normal">source</th>
                  <th className="px-3 py-2 font-normal">tool</th>
                  <th className="px-3 py-2 font-normal">summary</th>
                  <th className="px-3 py-2 font-normal">status</th>
                </tr>
              </thead>
              <tbody>
                {actions.map((action) => (
                  <tr key={action.id} className="border-b border-zinc-900">
                    <td className="px-3 py-2 whitespace-nowrap text-zinc-500">
                      {relTime(action.created_at)}
                    </td>
                    <td className="px-3 py-2">
                      <span className={`rounded px-1.5 py-0.5 ${sourceBadge[action.source]}`}>
                        {action.source}
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono">
                      {action.name}
                      {action.tier === 'write' && (
                        <span className="ml-1 rounded bg-zinc-800 px-1 text-[10px] text-zinc-400">
                          write
                        </span>
                      )}
                    </td>
                    <td className="max-w-md truncate px-3 py-2 text-zinc-400">
                      {action.error ?? action.result_summary ?? '—'}
                    </td>
                    <td
                      className={`px-3 py-2 ${
                        action.status === 'error'
                          ? 'text-red-400'
                          : action.status === 'pending_confirmation'
                            ? 'text-amber-400'
                            : 'text-zinc-400'
                      }`}
                    >
                      {action.status}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <MorningBriefSection />

      <BitsoSection />

      <IngestionHealthSection />
    </div>
  )
}
