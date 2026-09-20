import { http } from './client'
import type { ProposalView, ReflectionReport } from '../types/airi'

export async function createReflection(experimentRunIds: string[]): Promise<ReflectionReport> {
  const { data } = await http.post<ReflectionReport>('/api/v1/reflections', {
    experiment_run_ids: experimentRunIds,
  })
  return data
}

export async function fetchReflection(reflectionRunId: string): Promise<ReflectionReport> {
  const { data } = await http.get<ReflectionReport>(`/api/v1/reflections/${reflectionRunId}`)
  return data
}

export async function fetchProposals(reflectionId: string): Promise<ProposalView[]> {
  const { data } = await http.get<ProposalView[]>(`/api/v1/reflections/${reflectionId}/proposals`)
  return data
}

export async function decideProposal(
  reflectionId: string,
  proposalId: string,
  decision: 'accepted_for_investigation' | 'rejected' | 'need_more_evidence',
  reviewer: string,
  comment: string,
): Promise<unknown> {
  const { data } = await http.post(
    `/api/v1/reflections/${reflectionId}/proposals/${proposalId}/decision`,
    { decision, reviewer, comment },
  )
  return data
}
