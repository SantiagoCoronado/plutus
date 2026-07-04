import type { FilterLeaf, FilterNode, ScreenField } from '../../api/client'

export interface BuilderRow {
  field: string
  op: string
  value1: string
  value2: string
}

export const BUILDER_OPS = ['>', '<', '>=', '<=', '==', '!=', 'between', 'is_null', 'not_null']

export function rowsToAst(rows: BuilderRow[]): FilterNode | null {
  const leaves: FilterLeaf[] = []
  for (const row of rows) {
    if (!row.field) continue
    if (row.op === 'is_null' || row.op === 'not_null') {
      leaves.push({ field: row.field, op: row.op })
    } else if (row.op === 'between') {
      const lo = Number(row.value1)
      const hi = Number(row.value2)
      if (row.value1 === '' || row.value2 === '' || Number.isNaN(lo) || Number.isNaN(hi)) continue
      leaves.push({ field: row.field, op: row.op, value: [lo, hi] })
    } else {
      const value = Number(row.value1)
      if (row.value1 === '' || Number.isNaN(value)) continue
      leaves.push({ field: row.field, op: row.op, value })
    }
  }
  if (leaves.length === 0) return null
  return { all: leaves }
}

/** rows if the AST is a flat AND of scalar leaves the builder can represent, else null */
export function astToRows(ast: FilterNode): BuilderRow[] | null {
  if (!('all' in ast)) return null
  const rows: BuilderRow[] = []
  for (const child of ast.all) {
    if (!('field' in child) || !BUILDER_OPS.includes(child.op)) return null
    const value = child.value
    if (child.op === 'is_null' || child.op === 'not_null') {
      rows.push({ field: child.field, op: child.op, value1: '', value2: '' })
    } else if (child.op === 'between' && Array.isArray(value)) {
      rows.push({ field: child.field, op: child.op, value1: String(value[0]), value2: String(value[1]) })
    } else if (typeof value === 'number') {
      rows.push({ field: child.field, op: child.op, value1: String(value), value2: '' })
    } else {
      return null // field refs and nested nodes need the JSON editor
    }
  }
  return rows
}

interface Props {
  rows: BuilderRow[]
  fields: ScreenField[]
  onChange: (rows: BuilderRow[]) => void
}

export default function RuleBuilder({ rows, fields, onChange }: Props) {
  const update = (i: number, patch: Partial<BuilderRow>) =>
    onChange(rows.map((row, j) => (j === i ? { ...row, ...patch } : row)))

  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="flex flex-wrap items-center gap-2 text-sm">
          {i > 0 && <span className="w-8 text-right text-xs text-zinc-600">AND</span>}
          {i === 0 && <span className="w-8" />}
          <select
            value={row.field}
            onChange={(e) => update(i, { field: e.target.value })}
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 focus:border-zinc-500 focus:outline-none"
          >
            <option value="">field…</option>
            {fields.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name}
                {f.fundamental ? ' (no backtest)' : ''}
              </option>
            ))}
          </select>
          <select
            value={row.op}
            onChange={(e) => update(i, { op: e.target.value })}
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 focus:border-zinc-500 focus:outline-none"
          >
            {BUILDER_OPS.map((op) => (
              <option key={op} value={op}>
                {op}
              </option>
            ))}
          </select>
          {row.op !== 'is_null' && row.op !== 'not_null' && (
            <input
              type="number"
              step="any"
              value={row.value1}
              onChange={(e) => update(i, { value1: e.target.value })}
              placeholder="value"
              className="w-28 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 focus:border-zinc-500 focus:outline-none"
            />
          )}
          {row.op === 'between' && (
            <>
              <span className="text-xs text-zinc-600">and</span>
              <input
                type="number"
                step="any"
                value={row.value2}
                onChange={(e) => update(i, { value2: e.target.value })}
                placeholder="high"
                className="w-28 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 focus:border-zinc-500 focus:outline-none"
              />
            </>
          )}
          <button
            type="button"
            onClick={() => onChange(rows.filter((_, j) => j !== i))}
            className="text-xs text-zinc-600 hover:text-red-400"
            title="remove condition"
          >
            ✕
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...rows, { field: '', op: '<', value1: '', value2: '' }])}
        className="ml-10 rounded border border-dashed border-zinc-700 px-2 py-1 text-xs text-zinc-500 hover:border-zinc-500 hover:text-zinc-300"
      >
        + add condition
      </button>
    </div>
  )
}
