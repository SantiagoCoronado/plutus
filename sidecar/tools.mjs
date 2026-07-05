// Bridges the Python tool registry into the agent SDK: each tool's handler
// POSTs back to the hub's /agent/tools/execute, so gating and auditing are
// identical to the in-app loop. Tool schemas arrive as the registry's
// lowest-common-denominator JSON Schema and convert 1:1 to zod shapes.

import { createSdkMcpServer, tool } from '@anthropic-ai/claude-agent-sdk'
import { z } from 'zod'

function propToZod(prop) {
  let schema
  switch (prop.type) {
    case 'string':
      schema = prop.enum ? z.enum(prop.enum) : z.string()
      break
    case 'integer':
      schema = z.number().int()
      break
    case 'number':
      schema = z.number()
      break
    case 'boolean':
      schema = z.boolean()
      break
    case 'array':
      schema = z.array(prop.items ? propToZod(prop.items) : z.any())
      break
    default: // nested free-form objects (specs, records, ASTs) — Python re-validates
      schema = z.record(z.string(), z.any())
  }
  return prop.description ? schema.describe(prop.description) : schema
}

export function schemaToShape(schema) {
  const required = new Set(schema.required ?? [])
  const shape = {}
  for (const [key, prop] of Object.entries(schema.properties ?? {})) {
    const zodSchema = propToZod(prop)
    shape[key] = required.has(key) ? zodSchema : zodSchema.optional()
  }
  return shape
}

let callCounter = 0

/**
 * @param toolDefs [{name, description, input_schema}] from the hub
 * @param ctx {apiUrl, apiToken, conversationId, emit} — emit(event, data) pushes SSE
 */
export function buildPlutusServer(toolDefs, ctx) {
  const tools = toolDefs.map((def) =>
    tool(def.name, def.description, schemaToShape(def.input_schema), async (args) => {
      const callId = `sc_${++callCounter}`
      ctx.emit('tool_call', { tool_call_id: callId, name: def.name, arguments: args })

      let payload
      try {
        const resp = await fetch(`${ctx.apiUrl}/api/v1/agent/tools/execute`, {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            authorization: `Bearer ${ctx.apiToken}`,
          },
          body: JSON.stringify({
            name: def.name,
            arguments: args,
            conversation_id: ctx.conversationId,
          }),
        })
        if (!resp.ok) {
          payload = {
            status: 'error',
            error: `hub returned ${resp.status}: ${(await resp.text()).slice(0, 300)}`,
            _meta: { ok: false },
          }
        } else {
          payload = await resp.json()
        }
      } catch (err) {
        payload = {
          status: 'error',
          error: `could not reach the hub API: ${err.message}`,
          _meta: { ok: false },
        }
      }

      const meta = payload._meta ?? {}
      ctx.emit('tool_result', {
        tool_call_id: callId,
        name: def.name,
        arguments: args,
        ok: meta.ok ?? payload.status === 'ok',
        summary: meta.summary ?? null,
        error: payload.error ?? null,
        needs_confirmation: meta.needs_confirmation ?? false,
        confirmation_id: meta.confirmation_id ?? null,
      })

      // the model sees the same shape the Python loop feeds back
      const { _meta, ...modelView } = payload
      return { content: [{ type: 'text', text: JSON.stringify(modelView) }] }
    }),
  )

  return {
    server: createSdkMcpServer({ name: 'plutus', version: '1.0.0', tools }),
    allowedTools: toolDefs.map((def) => `mcp__plutus__${def.name}`),
  }
}
