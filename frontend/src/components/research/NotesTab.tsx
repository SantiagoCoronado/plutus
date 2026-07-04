import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, relTime, type Note } from '../../api/client'

function Editor({
  initial,
  onSave,
  onCancel,
}: {
  initial?: Note
  onSave: (title: string | null, body: string) => Promise<void>
  onCancel: () => void
}) {
  const [title, setTitle] = useState(initial?.title ?? '')
  const [body, setBody] = useState(initial?.body_md ?? '')
  const [preview, setPreview] = useState(false)
  const [busy, setBusy] = useState(false)

  return (
    <div className="rounded border border-zinc-700 p-3">
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Title (optional)"
        className="mb-2 w-full rounded border border-zinc-800 bg-zinc-900 px-2 py-1 text-sm focus:border-zinc-600 focus:outline-none"
      />
      {preview ? (
        <div className="prose prose-invert prose-sm max-h-72 min-h-32 overflow-auto rounded bg-zinc-900 p-3">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body || '*nothing yet*'}</ReactMarkdown>
        </div>
      ) : (
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={8}
          placeholder="Markdown research notes…"
          className="w-full rounded border border-zinc-800 bg-zinc-900 px-2 py-1 font-mono text-sm focus:border-zinc-600 focus:outline-none"
        />
      )}
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          disabled={busy || !body.trim()}
          onClick={async () => {
            setBusy(true)
            try {
              await onSave(title.trim() || null, body)
            } finally {
              setBusy(false)
            }
          }}
          className="rounded bg-sky-800 px-3 py-1 text-sm text-sky-100 hover:bg-sky-700 disabled:opacity-50"
        >
          Save
        </button>
        <button
          type="button"
          onClick={() => setPreview(!preview)}
          className="rounded border border-zinc-700 px-3 py-1 text-sm text-zinc-300 hover:bg-zinc-800"
        >
          {preview ? 'Edit' : 'Preview'}
        </button>
        <button type="button" onClick={onCancel} className="px-2 py-1 text-sm text-zinc-500">
          Cancel
        </button>
      </div>
    </div>
  )
}

export default function NotesTab({ assetId }: { assetId: number }) {
  const [notes, setNotes] = useState<Note[] | null>(null)
  const [editing, setEditing] = useState<Note | 'new' | null>(null)

  const load = () => api.notes(assetId).then(setNotes).catch(() => setNotes([]))

  useEffect(() => {
    setNotes(null)
    setEditing(null)
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assetId])

  if (notes === null) return <p className="text-sm text-zinc-500">Loading…</p>

  return (
    <div className="space-y-4">
      {editing === 'new' && (
        <Editor
          onCancel={() => setEditing(null)}
          onSave={async (title, body) => {
            await api.createNote(assetId, { title, body_md: body })
            setEditing(null)
            await load()
          }}
        />
      )}
      {editing === null && (
        <button
          type="button"
          onClick={() => setEditing('new')}
          className="rounded border border-zinc-700 px-3 py-1 text-sm text-zinc-300 hover:bg-zinc-800"
        >
          + New note
        </button>
      )}

      {notes.length === 0 && editing === null && (
        <p className="text-sm text-zinc-500">No notes yet.</p>
      )}

      {notes.map((note) =>
        editing !== 'new' && editing?.id === note.id ? (
          <Editor
            key={note.id}
            initial={note}
            onCancel={() => setEditing(null)}
            onSave={async (title, body) => {
              await api.updateNote(assetId, note.id, { title, body_md: body })
              setEditing(null)
              await load()
            }}
          />
        ) : (
          <article key={note.id} className="rounded border border-zinc-800 p-3">
            <div className="mb-1 flex items-center justify-between">
              <div className="flex items-center gap-2">
                {note.title && <h4 className="text-sm font-medium">{note.title}</h4>}
                {note.source === 'ai' && (
                  <span className="rounded bg-violet-950 px-1.5 py-0.5 text-[10px] text-violet-300">
                    AI-generated
                  </span>
                )}
                <span className="text-xs text-zinc-600">updated {relTime(note.updated_at)}</span>
              </div>
              <div className="flex gap-2 text-xs">
                <button
                  type="button"
                  onClick={() => setEditing(note)}
                  className="text-zinc-400 hover:text-zinc-200"
                >
                  edit
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    await api.deleteNote(assetId, note.id)
                    await load()
                  }}
                  className="text-zinc-500 hover:text-red-400"
                >
                  delete
                </button>
              </div>
            </div>
            <div className="prose prose-invert prose-sm max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{note.body_md}</ReactMarkdown>
            </div>
          </article>
        ),
      )}
    </div>
  )
}
