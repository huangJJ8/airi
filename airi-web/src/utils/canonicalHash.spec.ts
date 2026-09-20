import { describe, expect, it } from 'vitest'
import { canonicalHash } from './canonicalHash'

describe('canonicalHash', () => {
  it('is key-order independent', async () => {
    const a = await canonicalHash({ name: 'm', window: { size: 30, unit: 'day' } })
    const b = await canonicalHash({ window: { unit: 'day', size: 30 }, name: 'm' })
    expect(a).toBe(b)
  })

  it('matches the documented backend algorithm shape (sha256 hex, 64 chars)', async () => {
    const hash = await canonicalHash({ name: 'invoice_amount_30d' })
    expect(hash).toMatch(/^[a-f0-9]{64}$/)
  })

  it('distinguishes different payloads', async () => {
    const a = await canonicalHash({ size: 30 })
    const b = await canonicalHash({ size: 90 })
    expect(a).not.toBe(b)
  })
})
