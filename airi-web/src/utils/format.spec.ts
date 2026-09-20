import { describe, expect, it } from 'vitest'
import { formatDateTime, formatDelta, formatPercent, formatScore, riskDirection } from './format'

describe('experiment metric formatting', () => {
  it('renders percents from backend fractions only', () => {
    expect(formatPercent(0.92)).toBe('92.0%')
    expect(formatPercent(0.5714, 2)).toBe('57.14%')
    expect(formatPercent(null)).toBe('—')
  })

  it('renders scores and deltas with sign awareness', () => {
    expect(formatScore(0.577)).toBe('0.58')
    expect(formatScore(null)).toBe('—')
    expect(formatDelta(0.02)).toBe('+0.02')
    expect(formatDelta(-0.11)).toBe('-0.11')
    expect(formatDelta(null)).toBe('—')
  })

  it('phrases KS risk direction from backend enums', () => {
    expect(riskDirection('higher_is_riskier')).toBe('Higher is Riskier')
    expect(riskDirection('lower_is_riskier')).toBe('Lower is Riskier')
    expect(riskDirection('undetermined')).toBe('Undetermined')
    expect(riskDirection(null)).toBe('—')
  })
})

describe('formatDateTime', () => {
  it('returns the raw value when unparseable', () => {
    expect(formatDateTime('not-a-date')).toBe('not-a-date')
    expect(formatDateTime(null)).toBe('—')
  })

  it('formats valid ISO timestamps', () => {
    expect(formatDateTime('2026-09-09T00:00:00+08:00')).not.toBe('—')
  })
})
