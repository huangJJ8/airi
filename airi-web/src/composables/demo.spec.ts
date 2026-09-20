import { describe, expect, it } from 'vitest'
import { DEMO_EXAMPLES } from './demo'

describe('DEMO_EXAMPLES', () => {
  it('covers the two shipped scenario families', () => {
    expect(DEMO_EXAMPLES.map((example) => example.scenario).sort()).toEqual([
      'enterprise_relation',
      'invoice_risk',
    ])
  })

  it('never hardcodes a metric outside the declared family vocabulary', () => {
    expect(DEMO_EXAMPLES.find((e) => e.scenario === 'invoice_risk')!.requirement).toContain('开票')
    expect(DEMO_EXAMPLES.find((e) => e.scenario === 'enterprise_relation')!.requirement).toContain('关联自然人')
  })

  it('keeps every entry shaped the same way so the picker has a stable contract', () => {
    for (const example of DEMO_EXAMPLES) {
      expect(typeof example.label).toBe('string')
      expect(example.label.length).toBeGreaterThan(0)
      expect(typeof example.requirement).toBe('string')
      expect(example.requirement.length).toBeGreaterThan(0)
    }
  })
})