import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { relTime, type AgentBrief } from '../../api/client'

/** The latest agent memo (Phase 6), always labeled "AI-generated". Hidden when null. */
export default function DailyBriefCard({ brief }: { brief: AgentBrief | null }) {
  if (!brief) return null
  return (
    <div className="rounded border border-zinc-800 p-4">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-zinc-300">{brief.subject}</h2>
        <span className="rounded bg-violet-950 px-1.5 py-0.5 text-[10px] text-violet-300">
          AI-generated
        </span>
        <span className="ml-auto text-xs text-zinc-600">{relTime(brief.sent_at)}</span>
      </div>
      {brief.body && (
        <div className="prose prose-invert prose-sm max-w-none text-sm text-zinc-300">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{brief.body}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}
