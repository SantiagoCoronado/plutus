// Run with: npm test (node --test). Kept out of the Python suites on purpose —
// Node never blocks a backend run.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { schemaToShape } from './tools.mjs'

test('ANTHROPIC_API_KEY is stripped at server import', async () => {
  process.env.ANTHROPIC_API_KEY = 'sk-must-die'
  process.env.PORT = '0' // ephemeral port; the listener is irrelevant here
  await import('./server.mjs')
  assert.equal(process.env.ANTHROPIC_API_KEY, undefined)
})

test('LCD JSON schema converts to a zod shape with required/optional split', () => {
  const shape = schemaToShape({
    type: 'object',
    properties: {
      symbol: { type: 'string' },
      interval: { type: 'string', enum: ['1d', '1w'] },
      days: { type: 'integer', description: 'lookback' },
      spec: { type: 'object' },
      flag: { type: 'boolean' },
    },
    required: ['symbol'],
  })
  assert.deepEqual(Object.keys(shape).sort(), ['days', 'flag', 'interval', 'spec', 'symbol'])
  assert.equal(shape.symbol.isOptional(), false)
  assert.equal(shape.days.isOptional(), true)
  assert.equal(shape.interval.unwrap().parse('1d'), '1d')
  assert.throws(() => shape.interval.unwrap().parse('1M'))
  assert.deepEqual(shape.spec.unwrap().parse({ nested: { anything: 1 } }), {
    nested: { anything: 1 },
  })
})

test('sidecar auth: fail-closed shared-secret check', async () => {
  const { authorized } = await import('./server.mjs')
  // no secret configured -> nothing is authorized, ever
  assert.equal(authorized('Bearer anything', ''), false)
  assert.equal(authorized('', ''), false)
  // exact match required, timing-safe
  assert.equal(authorized('Bearer s3cret', 's3cret'), true)
  assert.equal(authorized('Bearer wrong', 's3cret'), false)
  assert.equal(authorized('s3cret', 's3cret'), false) // missing Bearer prefix
  assert.equal(authorized(undefined, 's3cret'), false)
})
