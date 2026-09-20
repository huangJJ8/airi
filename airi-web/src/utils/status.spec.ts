import { describe, expect, it } from 'vitest'
import { statusDisplay, TEST_TYPE_LABELS } from './status'

describe('statusDisplay', () => {
  it('maps test statuses with semantic tones', () => {
    expect(statusDisplay('passed')).toEqual({ label: 'PASS', tone: 'success' })
    expect(statusDisplay('failed')).toEqual({ label: 'FAIL', tone: 'danger' })
    expect(statusDisplay('warning')).toEqual({ label: 'WARNING', tone: 'warning' })
  })

  it('maps registry lifecycle statuses', () => {
    expect(statusDisplay('active').tone).toBe('success')
    expect(statusDisplay('retired').tone).toBe('info')
    expect(statusDisplay('release_candidate').label).toBe('RELEASE CANDIDATE')
  })

  it('maps refinement outcomes without making everything green', () => {
    expect(statusDisplay('worse').tone).toBe('danger')
    expect(statusDisplay('mixed').tone).toBe('warning')
    expect(statusDisplay('improved').tone).toBe('success')
  })

  it('uppercases unknown statuses instead of crashing', () => {
    expect(statusDisplay('some_new_status')).toEqual({ label: 'SOME NEW STATUS', tone: 'info' })
    expect(statusDisplay(null).label).toBe('UNKNOWN')
  })
})

describe('TEST_TYPE_LABELS', () => {
  it('covers the six deterministic invoice tests', () => {
    expect(['boundary', 'duplicate', 'null', 'reconciliation', 'schema', 'window'].map((t) => TEST_TYPE_LABELS[t]).every(Boolean)).toBe(true)
  })

  it('covers the relationship-family test types Phase 11 adds', () => {
    expect(TEST_TYPE_LABELS.join).toBe('Join Path')
    expect(TEST_TYPE_LABELS.distinct).toBe('Distinct Count')
    expect(TEST_TYPE_LABELS.self_exclusion).toBe('Self-exclusion')
    expect(TEST_TYPE_LABELS.missing_relation).toBe('Missing Relation')
  })

  it('keeps exactly the declared types, invoice plus relationship', () => {
    expect(Object.keys(TEST_TYPE_LABELS).sort()).toEqual(
      [
        'boundary',
        'distinct',
        'duplicate',
        'join',
        'missing_relation',
        'null',
        'reconciliation',
        'schema',
        'self_exclusion',
        'window',
      ].sort(),
    )
  })
})
