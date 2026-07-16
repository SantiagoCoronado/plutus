import { useEffect, useRef, type ReactNode } from 'react'
import { ApiError } from '../../api/client'

export const inputClass =
  'rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200 placeholder-zinc-600 focus:border-zinc-500 focus:outline-none'

export const buttonClass =
  'rounded border border-zinc-700 px-2.5 py-1 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-50'

/** Full money amounts (positions, cash): 1,234.56 — fmtNum abbreviates too aggressively. */
export function fmtMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export interface ServerError {
  path?: string
  error: string
}

export function parseServerErrors(e: unknown): ServerError[] {
  if (!(e instanceof ApiError)) return [{ error: 'request failed' }]
  try {
    const detail = JSON.parse(e.message).detail
    if (Array.isArray(detail?.errors)) return detail.errors
    if (typeof detail === 'string') return [{ error: detail }]
  } catch {
    /* not json */
  }
  return [{ error: `request failed (${e.status})` }]
}

export function Modal({
  title,
  onClose,
  children,
  wide,
  guardClose = false,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  wide?: boolean
  /** when the modal holds unsaved work (CSV mapping mid-import), a stray
   * backdrop click must not discard it — only Escape/the explicit close ask */
  guardClose?: boolean
}) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      previouslyFocused?.focus()
    }
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 py-10"
      onClick={guardClose ? undefined : onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`${wide ? 'w-[680px]' : 'w-[560px]'} space-y-4 rounded-lg border border-zinc-700 bg-zinc-950 p-5 focus:outline-none`}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold">{title}</h2>
        {children}
      </div>
    </div>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="space-y-1 text-xs text-zinc-500">
      <span>{label}</span>
      {children}
    </label>
  )
}

export function ErrorList({ errors }: { errors: ServerError[] }) {
  if (errors.length === 0) return null
  return (
    <ul className="space-y-1 rounded border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-300">
      {errors.map((err, i) => (
        <li key={i}>
          {err.path ? <span className="font-mono">{err.path}: </span> : null}
          {err.error}
        </li>
      ))}
    </ul>
  )
}
