import { reactive, readonly } from 'vue'

/**
 * Guided demo context. Stores only Web navigation state (IDs returned by the
 * backend) so a visitor never copies a UUID. It is NOT a second workflow
 * database: refreshing the page starts a fresh guided run, and every fact
 * displayed is re-fetched from the backend.
 */
export interface DemoState {
  requirement: string
  artifactId: string | null
  metricName: string | null
  approvalId: string | null
  testRunId: string | null
  testStatus: string | null
  experimentRunId: string | null
  reflectionRunId: string | null
  proposalId: string | null
  refinementRuns: { windowDays: number; refinementRunId: string; outcome: string | null }[]
  currentStep: number
}

const DEFAULT_DEMO_REQUIREMENT = '统计企业近30天开票金额'

/** Guided demo examples. One per registered scenario family; the backend's
 * /api/v1/meta remains the source of truth for what exists — this list only
 * teaches the two shipped demo stories. */
export interface DemoExample {
  scenario: string
  label: string
  requirement: string
}

export const DEMO_EXAMPLES: DemoExample[] = [
  {
    scenario: 'invoice_risk',
    label: 'Invoice Risk — 近30天开票金额',
    requirement: '统计企业近30天开票金额',
  },
  {
    scenario: 'enterprise_relation',
    label: 'Enterprise Relation — 关联企业数量',
    requirement: '统计企业通过关联自然人间接关联的其他企业数量',
  },
]

const state = reactive<DemoState>({
  requirement: '',
  artifactId: null,
  metricName: null,
  approvalId: null,
  testRunId: null,
  testStatus: null,
  experimentRunId: null,
  reflectionRunId: null,
  proposalId: null,
  refinementRuns: [],
  currentStep: 0,
})

export const DEMO_STEPS = ['Development', 'Testing', 'Experiment', 'Reflection', 'Registry'] as const

export function useDemoState() {
  function reset(requirement: string = DEFAULT_DEMO_REQUIREMENT) {
    state.requirement = requirement
    state.artifactId = null
    state.metricName = null
    state.approvalId = null
    state.testRunId = null
    state.testStatus = null
    state.experimentRunId = null
    state.reflectionRunId = null
    state.proposalId = null
    state.refinementRuns = []
    state.currentStep = 1
  }

  return { state, reset, defaultRequirement: DEFAULT_DEMO_REQUIREMENT }
}

export const demoState = readonly(state)
