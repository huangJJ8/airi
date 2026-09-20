/** Formatting helpers. All numeric results come from the backend; the UI only
 * renders them. */

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function formatScore(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—'
  return value.toFixed(digits)
}

export function formatDelta(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—'
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}`
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

/** Risk direction phrasing for the KS result, phrased from backend data only. */
export function riskDirection(ksDirection: string | null | undefined): string {
  switch (ksDirection) {
    case 'higher_is_riskier':
      return 'Higher is Riskier'
    case 'lower_is_riskier':
      return 'Lower is Riskier'
    case 'undetermined':
      return 'Undetermined'
    default:
      return ksDirection ? ksDirection.replaceAll('_', ' ') : '—'
  }
}
