import { http } from './client'
import type { RefinementReport } from '../types/airi'

export async function createRefinement(
  reflectionRunId: string,
  proposalId: string,
  windowDays: number,
): Promise<RefinementReport> {
  const { data } = await http.post<RefinementReport>('/api/v1/refinements', {
    reflection_run_id: reflectionRunId,
    proposal_id: proposalId,
    parameter_selection: { window_days: windowDays },
  })
  return data
}

export async function evaluateRefinement(runId: string): Promise<RefinementReport> {
  const { data } = await http.post<RefinementReport>(`/api/v1/refinements/${runId}/evaluate`)
  return data
}

export async function fetchRefinement(runId: string): Promise<RefinementReport> {
  const { data } = await http.get<RefinementReport>(`/api/v1/refinements/${runId}`)
  return data
}

export async function decideRefinement(
  runId: string,
  decision:
    | 'keep_baseline'
    | 'accept_candidate_for_further_validation'
    | 'need_more_evidence'
    | 'reject_candidate',
  reviewer: string,
  comment: string,
): Promise<RefinementReport> {
  const { data } = await http.post<RefinementReport>(`/api/v1/refinements/${runId}/decision`, {
    decision,
    reviewer,
    comment,
  })
  return data
}
