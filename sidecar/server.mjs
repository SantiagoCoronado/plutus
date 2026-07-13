// Plutus agent sidecar: claude-subscription auth for the AI layer (spec §13.1).
//
// Wraps @anthropic-ai/claude-agent-sdk — the Claude CLI runs the tool loop on
// the user's Claude plan (CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`)
// and this service streams normalized SSE back to the Python backend. Tools
// execute in Python via the /agent/tools/execute callback (see tools.mjs).
//
// ANTHROPIC_API_KEY is deleted unconditionally: if it leaked into this
// environment it would silently switch billing from subscription to API.

import { createServer } from 'node:http'
import { timingSafeEqual } from 'node:crypto'
import { query } from '@anthropic-ai/claude-agent-sdk'
import { buildPlutusServer } from './tools.mjs'

delete process.env.ANTHROPIC_API_KEY

const PORT = Number(process.env.PORT ?? 8787)
const PLUTUS_API_URL = process.env.PLUTUS_API_URL ?? 'http://localhost:8800'
const PLUTUS_API_TOKEN = process.env.PLUTUS_API_TOKEN ?? ''
// Fail-closed inbound auth (spec phase 9 M2): this process holds the Claude
// OAuth token AND the hub API token, so every request except /health must
// present the shared secret. No secret -> refuse to boot at all.
const SHARED_SECRET = process.env.SIDECAR_SHARED_SECRET ?? ''

export function authorized(header, secret = SHARED_SECRET) {
  if (!secret) return false
  const presented = Buffer.from(String(header ?? ''))
  const expected = Buffer.from(`Bearer ${secret}`)
  return presented.length === expected.length && timingSafeEqual(presented, expected)
}

// the CLI's built-in tools have no business in a research hub with its own registry
const DISALLOWED_TOOLS = [
  'Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep', 'Task',
  'WebFetch', 'WebSearch', 'NotebookEdit', 'TodoWrite', 'KillShell', 'BashOutput',
]

function sse(res, event, data) {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
}

async function readBody(req) {
  let raw = ''
  for await (const chunk of req) raw += chunk
  return JSON.parse(raw || '{}')
}

async function handleChatStream(req, res) {
  let body
  try {
    body = await readBody(req)
  } catch {
    res.writeHead(400, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ error: 'invalid JSON body' }))
    return
  }

  res.writeHead(200, {
    'content-type': 'text/event-stream',
    'cache-control': 'no-cache',
    connection: 'keep-alive',
  })

  const emit = (event, data) => sse(res, event, data)
  const { server, allowedTools } = buildPlutusServer(body.tools ?? [], {
    apiUrl: PLUTUS_API_URL,
    apiToken: PLUTUS_API_TOKEN,
    conversationId: body.conversation_id ?? null,
    emit,
  })

  let sessionId = body.session_id ?? null
  const heartbeat = setInterval(() => res.write(': ping\n\n'), 15000)

  try {
    const stream = query({
      prompt: body.user_message ?? '',
      options: {
        systemPrompt: body.system ?? undefined,
        model: body.model || undefined,
        resume: sessionId ?? undefined,
        maxTurns: body.max_turns ?? 15,
        mcpServers: { plutus: server },
        allowedTools,
        disallowedTools: DISALLOWED_TOOLS,
        // never load the user's global config: it could point tools at the
        // wrong hub or re-enable filesystem access (Kairos lesson)
        settingSources: [],
        strictMcpConfig: true,
        includePartialMessages: true,
      },
    })

    for await (const message of stream) {
      if (message.type === 'system' && message.subtype === 'init') {
        sessionId = message.session_id ?? sessionId
        emit('start', { session_id: sessionId, model: message.model ?? null })
      } else if (message.type === 'stream_event') {
        const event = message.event
        if (
          event?.type === 'content_block_delta' &&
          event.delta?.type === 'text_delta' &&
          event.delta.text
        ) {
          emit('text_delta', { text: event.delta.text })
        }
      } else if (message.type === 'result') {
        const usage = message.usage ?? {}
        emit('done', {
          session_id: sessionId,
          model: message.model ?? null,
          usage: {
            // cache reads/writes still occupy context — count them as input
            input_tokens:
              (usage.input_tokens ?? 0) +
              (usage.cache_read_input_tokens ?? 0) +
              (usage.cache_creation_input_tokens ?? 0),
            output_tokens: usage.output_tokens ?? 0,
          },
          cost_usd: message.total_cost_usd ?? null,
        })
      }
    }
  } catch (err) {
    const hint = /login|auth|credential|401|403|token/i.test(String(err?.message))
      ? ' — check CLAUDE_CODE_OAUTH_TOKEN (regenerate with `claude setup-token`)'
      : ''
    emit('error', { message: `sidecar: ${err?.message ?? err}${hint}` })
  } finally {
    clearInterval(heartbeat)
    res.end()
  }
}

const server = createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(
      JSON.stringify({
        ok: true,
        auth: process.env.CLAUDE_CODE_OAUTH_TOKEN ? 'oauth' : 'missing',
      }),
    )
    return
  }
  if (!authorized(req.headers.authorization)) {
    res.writeHead(401, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ error: 'missing or invalid sidecar shared secret' }))
    return
  }
  if (req.method === 'POST' && req.url === '/chat/stream') {
    await handleChatStream(req, res)
    return
  }
  res.writeHead(404, { 'content-type': 'application/json' })
  res.end(JSON.stringify({ error: 'not found' }))
})

// node --test imports this module to verify the API-key strip; don't hold its
// event loop open with a live listener
if (!process.env.NODE_TEST_CONTEXT) {
  if (!SHARED_SECRET) {
    console.error(
      'SIDECAR_SHARED_SECRET is not set — refusing to start. Generate one with ' +
        '`openssl rand -hex 32` and put it in .env (the backend sends it on every request).',
    )
    process.exit(1)
  }
  server.listen(PORT, () => {
    console.log(
      `plutus agent sidecar on :${PORT} (auth: ${
        process.env.CLAUDE_CODE_OAUTH_TOKEN ? 'oauth' : 'MISSING — run \`claude setup-token\`'
      })`,
    )
  })
}
