import { useEffect, useState } from 'react'
import { api, type BriefSettings, type BriefTestResult } from '../../api/client'
import { buttonClass } from '../portfolio/shared'

export default function MorningBriefSection() {
  const [settings, setSettings] = useState<BriefSettings | null>(null)
  const [failed, setFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<BriefTestResult | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .briefSettings()
      .then((s) => {
        if (!cancelled) setSettings(s)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const toggle = async () => {
    if (!settings || saving) return
    setSaving(true)
    try {
      setSettings(await api.putBriefSettings({ enabled: !settings.enabled }))
    } catch {
      // leave the switch as-is; the global 401 banner covers auth problems
    } finally {
      setSaving(false)
    }
  }

  const runTest = async () => {
    if (testing) return
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(await api.testBrief())
    } catch (e) {
      setTestResult({
        ok: false,
        subject: '',
        sections: [],
        channels: [],
        error: e instanceof Error ? e.message : String(e),
      })
    } finally {
      setTesting(false)
    }
  }

  if (failed) return null
  if (!settings) return null

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold text-zinc-300">Morning brief</h2>
      <p className="text-xs text-zinc-500">
        One daily message at {settings.scheduled_at} (local) with your portfolio snapshot, new
        candidates, overnight AI memos, upcoming maturities, an alert recap, and a system line.
        While enabled, the separate digest/memo/maturity notifications stay quiet — price alerts
        and failure warnings still arrive instantly.
      </p>
      <div className="flex items-center gap-4 rounded border border-zinc-800 p-3">
        <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-300">
          <input
            type="checkbox"
            checked={settings.enabled}
            disabled={saving}
            onChange={toggle}
            className="accent-sky-500"
          />
          enabled
        </label>
        <button className={buttonClass} disabled={testing} onClick={runTest}>
          {testing ? 'Sending…' : 'Send test brief'}
        </button>
        {settings.channels.length === 0 && (
          <span className="text-xs text-amber-400">
            No alert channels configured — the brief has nowhere to go (set SMTP or Telegram in
            .env).
          </span>
        )}
        {testResult && (
          <span className={`text-xs ${testResult.ok ? 'text-emerald-400' : 'text-red-400'}`}>
            {testResult.ok
              ? `Sent via ${testResult.channels.join(' + ')} — sections: ${
                  testResult.sections.join(', ') || 'all quiet'
                }`
              : testResult.error}
          </span>
        )}
      </div>
    </section>
  )
}
