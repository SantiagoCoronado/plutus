import { useEffect, useState } from 'react'
import type { AstErrorDetail, FilterNode } from '../../api/client'

interface Props {
  ast: FilterNode | null
  builderLocked: boolean
  serverErrors: AstErrorDetail[] | null
  onApply: (ast: FilterNode) => void
}

/** Raw AST editor for anything the flat builder can't express (any/not, field refs). */
export default function JsonEditor({ ast, builderLocked, serverErrors, onApply }: Props) {
  const [open, setOpen] = useState(builderLocked)
  const [text, setText] = useState('')
  const [parseError, setParseError] = useState<string | null>(null)

  useEffect(() => {
    setText(ast ? JSON.stringify(ast, null, 2) : '')
  }, [ast])

  useEffect(() => {
    if (builderLocked) setOpen(true)
  }, [builderLocked])

  return (
    <div className="rounded border border-zinc-800">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-3 py-2 text-xs text-zinc-500 hover:text-zinc-300"
      >
        <span>
          Filter JSON{' '}
          {builderLocked && (
            <span className="ml-2 rounded bg-amber-900/40 px-1.5 py-0.5 text-amber-400">
              builder locked — this filter uses any/not or field references
            </span>
          )}
        </span>
        <span>{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-zinc-800 p-3">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={Math.min(14, Math.max(4, text.split('\n').length))}
            spellCheck={false}
            className="w-full rounded border border-zinc-700 bg-zinc-950 p-2 font-mono text-xs text-zinc-300 focus:border-zinc-500 focus:outline-none"
          />
          {parseError && <p className="text-xs text-red-400">{parseError}</p>}
          {serverErrors?.map((err, i) => (
            <p key={i} className="text-xs text-red-400">
              {err.path && <code className="mr-1 text-red-300">{err.path}</code>}
              {err.error}
            </p>
          ))}
          <button
            type="button"
            onClick={() => {
              try {
                const parsed = JSON.parse(text) as FilterNode
                setParseError(null)
                onApply(parsed)
              } catch (e) {
                setParseError(`Invalid JSON: ${String(e)}`)
              }
            }}
            className="rounded border border-zinc-700 px-3 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
          >
            Apply JSON
          </button>
        </div>
      )}
    </div>
  )
}
