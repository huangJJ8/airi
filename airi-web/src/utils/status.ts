/** Status -> display mapping. Status is never conveyed by color alone: every
 * badge carries its own text label. */

export type StatusTone = 'success' | 'warning' | 'danger' | 'info'

interface StatusDisplay {
  label: string
  tone: StatusTone
}

const STATUS_MAP: Record<string, StatusDisplay> = {
  // test results
  passed: { label: 'PASS', tone: 'success' },
  warning: { label: 'WARNING', tone: 'warning' },
  passed_with_warnings: { label: 'PASSED WITH WARNINGS', tone: 'warning' },
  failed: { label: 'FAIL', tone: 'danger' },
  skipped: { label: 'SKIPPED', tone: 'info' },
  // approvals
  pending: { label: 'PENDING REVIEW', tone: 'warning' },
  pending_review: { label: 'PENDING REVIEW', tone: 'warning' },
  approved: { label: 'APPROVED', tone: 'success' },
  rejected: { label: 'REJECTED', tone: 'danger' },
  // runs
  running: { label: 'RUNNING', tone: 'info' },
  completed: { label: 'COMPLETED', tone: 'success' },
  draft: { label: 'DRAFT', tone: 'info' },
  // registry versions
  registered: { label: 'REGISTERED', tone: 'info' },
  release_candidate: { label: 'RELEASE CANDIDATE', tone: 'warning' },
  active: { label: 'ACTIVE', tone: 'success' },
  retired: { label: 'RETIRED', tone: 'info' },
  // releases
  pending_staging_validation: { label: 'PENDING STAGING VALIDATION', tone: 'warning' },
  staging_validated: { label: 'STAGING VALIDATED', tone: 'info' },
  pending_release_review: { label: 'PENDING RELEASE REVIEW', tone: 'warning' },
  rolled_back: { label: 'ROLLED BACK', tone: 'warning' },
  // refinement outcomes
  improved: { label: 'IMPROVED', tone: 'success' },
  worse: { label: 'WORSE', tone: 'danger' },
  mixed: { label: 'MIXED', tone: 'warning' },
  inconclusive: { label: 'INCONCLUSIVE', tone: 'info' },
  // reflections
  unavailable: { label: 'UNAVAILABLE', tone: 'warning' },
  // decisions
  accepted_for_investigation: { label: 'ACCEPTED FOR INVESTIGATION', tone: 'info' },
  need_more_evidence: { label: 'NEED MORE EVIDENCE', tone: 'warning' },
  eligible_for_review: { label: 'ELIGIBLE FOR REVIEW', tone: 'success' },
}

const FALLBACK: StatusDisplay = { label: 'UNKNOWN', tone: 'info' }

export function statusDisplay(status: string | null | undefined): StatusDisplay {
  if (!status) return FALLBACK
  return STATUS_MAP[status] ?? { label: status.replaceAll('_', ' ').toUpperCase(), tone: 'info' }
}

/** Human labels for every test type a scenario can declare. The invoice family
 * declares the first six; the relation family declares the relationship set. */
export const TEST_TYPE_LABELS: Record<string, string> = {
  schema: 'Schema Validation',
  null: 'Null Entity',
  duplicate: 'Duplicate Entity',
  window: 'Window Semantics',
  boundary: 'Boundary Test',
  join: 'Join Path',
  distinct: 'Distinct Count',
  self_exclusion: 'Self-exclusion',
  missing_relation: 'Missing Relation',
  reconciliation: 'Reconciliation',
}
