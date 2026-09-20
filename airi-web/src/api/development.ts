import { http } from './client'
import type {
  ApprovalRecord,
  DevelopmentResult,
  ExecutionRequest,
  MetaInfo,
  MetricTestReport,
  TestingResponse,
} from '../types/airi'

export async function fetchMeta(): Promise<MetaInfo> {
  const { data } = await http.get<MetaInfo>('/api/v1/meta')
  return data
}

/** Requirement -> Metric IR -> deterministic SQL draft. Review only, never executed. */
export async function generateMetric(
  requirement: string,
  anchorTime: string,
  scenario: string,
): Promise<DevelopmentResult> {
  const { data } = await http.post<DevelopmentResult>('/api/v1/development/generate', {
    requirement,
    scenario,
    execution_context: { anchor_time: anchorTime },
  })
  return data
}

export async function fetchArtifact(artifactId: string): Promise<{ code: string; status: string }> {
  const { data } = await http.get(`/api/v1/development/artifacts/${artifactId}`)
  return data
}

export async function submitReview(payload: {
  artifact_id: string
  metric_ir_hash: string
  artifact_hash: string
}): Promise<ApprovalRecord> {
  const { data } = await http.post<ApprovalRecord>('/api/v1/approvals', payload)
  return data
}

export async function decideReview(
  approvalId: string,
  decision: 'approve' | 'reject',
  reviewer: string,
  comment: string,
): Promise<ApprovalRecord> {
  const { data } = await http.post<ApprovalRecord>(`/api/v1/approvals/${approvalId}/${decision}`, {
    reviewer,
    comment,
  })
  return data
}

/** Deterministic test battery — the scenario's own declaration decides the checks. */
export async function runTests(payload: ExecutionRequest): Promise<TestingResponse> {
  const { data } = await http.post<TestingResponse>('/api/v1/tests/run', payload)
  return data
}

export async function fetchTestReport(testRunId: string): Promise<MetricTestReport> {
  const { data } = await http.get<MetricTestReport>(`/api/v1/tests/${testRunId}`)
  return data
}
