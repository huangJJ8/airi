import { http } from './client'
import type {
  DatasetSnapshotInfo,
  ExperimentReport,
  ExperimentSpec,
  LabelDefinition,
} from '../types/airi'

/**
 * Reads the seeded synthetic snapshot + label definition (see
 * scripts/seed_demo.py). Read-only on purpose: the browser never fabricates
 * dataset checksums. `name` pins the seeded demo snapshot so the later
 * temporal-slice snapshots do not shadow it.
 */
export async function fetchLatestDataset(name?: string): Promise<DatasetSnapshotInfo | null> {
  const { data } = await http.get<DatasetSnapshotInfo | null>('/api/v1/datasets/latest', {
    params: name ? { name } : undefined,
  })
  return data
}

/** `name` pins the seeded demo label for the same reason as the dataset. */
export async function fetchLatestLabel(name?: string): Promise<LabelDefinition | null> {
  const { data } = await http.get<LabelDefinition | null>('/api/v1/labels/latest', {
    params: name ? { name } : undefined,
  })
  return data
}

export async function createExperiment(payload: ExperimentSpec): Promise<{ experiment_spec_id: string }> {
  const { data } = await http.post('/api/v1/experiments', payload)
  return data
}

export async function runExperiment(specId: string): Promise<ExperimentReport> {
  const { data } = await http.post<ExperimentReport>(`/api/v1/experiments/${specId}/run`)
  return data
}

export async function fetchExperimentRun(runId: string): Promise<ExperimentReport> {
  const { data } = await http.get<ExperimentReport>(`/api/v1/experiments/runs/${runId}`)
  return data
}
