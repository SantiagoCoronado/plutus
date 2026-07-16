import { describe, expect, it } from 'vitest'
import { ApiError } from '../../api/client'
import { tileColor } from '../dashboard/HeatmapTreemap'
import { parseServerErrors } from './shared'

describe('parseServerErrors', () => {
  it('extracts structured field errors from a 422 body', () => {
    const e = new ApiError(
      422,
      JSON.stringify({ detail: { errors: [{ path: 'quantity', error: 'must be positive' }] } }),
    )
    expect(parseServerErrors(e)).toEqual([{ path: 'quantity', error: 'must be positive' }])
  })

  it('wraps a plain-string detail', () => {
    const e = new ApiError(422, JSON.stringify({ detail: 'nope' }))
    expect(parseServerErrors(e)).toEqual([{ error: 'nope' }])
  })

  it('falls back to the status for non-JSON bodies', () => {
    expect(parseServerErrors(new ApiError(500, '<html>oops</html>'))).toEqual([
      { error: 'request failed (500)' },
    ])
  })

  it('handles non-ApiError values', () => {
    expect(parseServerErrors(new TypeError('fetch failed'))).toEqual([
      { error: 'request failed' },
    ])
  })
})

describe('tileColor', () => {
  it('is neutral zinc at 0%', () => {
    expect(tileColor(0)).toBe('rgb(63,63,70)')
  })

  it('is fully green at +3% and clamps beyond', () => {
    expect(tileColor(3)).toBe('rgb(22,163,74)')
    expect(tileColor(12)).toBe(tileColor(3))
  })

  it('is fully red at -3% and clamps beyond', () => {
    expect(tileColor(-3)).toBe('rgb(220,38,38)')
    expect(tileColor(-99)).toBe(tileColor(-3))
  })

  it('interpolates between neutral and the extremes', () => {
    expect(tileColor(1.5)).not.toBe(tileColor(0))
    expect(tileColor(1.5)).not.toBe(tileColor(3))
  })
})
