import { http } from './client'
import type { MetricDefinition, MetricReleaseEvent, MetricVersion, MetricVersionDiff } from '../types/airi'

export async function fetchMetricDefinitions(): Promise<MetricDefinition[]> {
  const { data } = await http.get<MetricDefinition[]>('/api/v1/metrics')
  return data
}

export async function fetchMetricDefinition(metricKey: string): Promise<MetricDefinition> {
  const { data } = await http.get<MetricDefinition>(`/api/v1/metrics/${metricKey}`)
  return data
}

export async function fetchVersions(metricKey: string): Promise<MetricVersion[]> {
  const { data } = await http.get<MetricVersion[]>(`/api/v1/metrics/${metricKey}/versions`)
  return data
}

export async function fetchActiveVersion(metricKey: string): Promise<MetricVersion | null> {
  const { data } = await http.get<MetricVersion | null>(`/api/v1/metrics/${metricKey}/active`)
  return data
}

export async function fetchVersion(metricKey: string, version: string): Promise<MetricVersion> {
  const { data } = await http.get<MetricVersion>(`/api/v1/metrics/${metricKey}/versions/${version}`)
  return data
}

export async function compareVersions(
  metricKey: string,
  fromVersion: string,
  toVersion: string,
): Promise<MetricVersionDiff> {
  const { data } = await http.get<MetricVersionDiff>(
    `/api/v1/metrics/${metricKey}/versions/${fromVersion}/compare/${toVersion}`,
  )
  return data
}

export async function fetchReleaseEvents(metricKey: string): Promise<MetricReleaseEvent[]> {
  const { data } = await http.get<MetricReleaseEvent[]>(`/api/v1/metrics/${metricKey}/events`)
  return data
}

export async function fetchAlerts(): Promise<unknown[]> {
  const { data } = await http.get('/api/v1/metric-alerts')
  return data
}
