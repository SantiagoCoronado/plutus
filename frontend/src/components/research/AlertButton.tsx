import { useEffect, useState } from 'react'
import { api, type AlertRule } from '../../api/client'
import AlertModal from '../alerts/AlertModal'

/** Bell button in the Research header: shows the armed-rule count, opens AlertModal. */
export default function AlertButton({ assetId, symbol }: { assetId: number; symbol: string }) {
  const [rules, setRules] = useState<AlertRule[]>([])
  const [open, setOpen] = useState(false)

  const refresh = () => api.alerts({ asset_id: assetId }).then(setRules).catch(() => {})

  useEffect(() => {
    refresh()
  }, [assetId])

  const armed = rules.filter((r) => r.status === 'armed').length

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Price alerts"
        className={`rounded border px-2.5 py-1 text-sm transition-colors ${
          armed > 0
            ? 'border-sky-700 bg-sky-950 text-sky-300'
            : 'border-zinc-700 text-zinc-400 hover:border-zinc-500'
        }`}
      >
        {armed > 0 ? `🔔 ${armed}` : '🔔 alert'}
      </button>
      {open && (
        <AlertModal
          assetId={assetId}
          symbol={symbol}
          onClose={() => {
            setOpen(false)
            refresh()
          }}
          onChanged={refresh}
        />
      )}
    </>
  )
}
