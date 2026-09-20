<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { describeApiError } from '../api/client'
import { fetchActiveVersion, fetchAlerts, fetchMetricDefinitions } from '../api/registry'
import { fetchMeta } from '../api/development'
import type { MetaInfo, MetricDefinition, MetricVersion } from '../types/airi'
import StatusTag from '../components/StatusTag.vue'
import { formatDateTime } from '../utils/format'

const router = useRouter()

const loading = ref(true)
const error = ref('')
const meta = ref<MetaInfo | null>(null)
const definitions = ref<MetricDefinition[]>([])
const activeVersion = ref<MetricVersion | null>(null)
const alertCount = ref<number | null>(null)

const metricsCount = computed(() => definitions.value.length)
/** Recomputed from /api/v1/meta — the backend's registered scenarios, never a constant. */
const scenariosCount = computed(() => meta.value?.scenarios?.length ?? 0)

interface FlowNode {
  label: string
  route: string
  role: 'llm' | 'python' | 'human'
  desc: string
}

/** The single product story, told as a clickable workflow. */
const flow: FlowNode[] = [
  { label: 'Requirement', route: '/development', role: 'llm', desc: 'Natural language, parsed by the LLM' },
  { label: 'Metric IR', route: '/development', role: 'llm', desc: 'Structured understanding' },
  { label: 'Development', route: '/development', role: 'python', desc: 'Skills + tools generate deterministic SQL' },
  { label: 'Testing', route: '/testing', role: 'python', desc: 'Scenario-declared automated checks' },
  { label: 'Experiment', route: '/experiments', role: 'python', desc: 'Coverage / KS / IV / Lift' },
  { label: 'Reflection', route: '/reflection', role: 'llm', desc: 'Hypotheses from evidence' },
  { label: 'Refinement', route: '/reflection', role: 'python', desc: 'Candidates re-tested by experiment' },
  { label: 'Registry', route: '/registry', role: 'human', desc: 'Governed, versioned release' },
]

const ROLE_LABEL: Record<FlowNode['role'], string> = {
  llm: 'LLM',
  python: 'Python',
  human: 'Human',
}

const roleTone: Record<FlowNode['role'], 'primary' | 'success' | 'warning'> = {
  llm: 'primary',
  python: 'success',
  human: 'warning',
}

const recent = computed(() => {
  const v = activeVersion.value
  if (!v) return []
  return [
    { title: 'Active metric version', detail: `${v.metric_key} ${v.version} · ${v.metric_name}`, time: v.created_at },
    { title: 'Metric IR hash', detail: v.metric_ir_hash.slice(0, 24) + '…', time: v.created_at },
  ]
})

onMounted(async () => {
  try {
    const [metaInfo, defs, alerts] = await Promise.all([
      fetchMeta(),
      fetchMetricDefinitions(),
      fetchAlerts().catch(() => null),
    ])
    meta.value = metaInfo
    definitions.value = defs
    if (Array.isArray(alerts)) alertCount.value = alerts.length
    if (defs.length > 0) {
      activeVersion.value = await fetchActiveVersion(defs[0].metric_key)
    }
  } catch (e) {
    error.value = describeApiError(e)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-loading="loading" class="page">
    <h1 class="page-title">Dashboard</h1>
    <p class="page-subtitle">
      AIRI turns a natural-language risk requirement into a governed, versioned metric —
      <strong>LLMs reason, Python verifies, humans govern.</strong>
    </p>

    <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" class="err" />

    <template v-if="!error">
      <el-row :gutter="16" class="kpis">
        <el-col :span="6">
          <el-card shadow="never" class="kpi">
            <div class="kpi-value">{{ scenariosCount || '—' }}</div>
            <div class="kpi-label">Scenario families</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" class="kpi">
            <div class="kpi-value">{{ metricsCount }}</div>
            <div class="kpi-label">Metric families</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" class="kpi">
            <div class="kpi-value">
              <StatusTag v-if="activeVersion" :status="activeVersion.status" />
              <span v-else>—</span>
            </div>
            <div class="kpi-label">Active version</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" class="kpi">
            <div class="kpi-value">{{ alertCount ?? '—' }}</div>
            <div class="kpi-label">Open alerts</div>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="section">
        <template #header>
          <div class="section-header">
            <span>How AIRI works</span>
            <span class="legend">
              <el-tag size="small" type="primary" effect="plain">LLM — understand & reflect</el-tag>
              <el-tag size="small" type="success" effect="plain">Python — verify & decide</el-tag>
              <el-tag size="small" type="warning" effect="plain">Human — govern</el-tag>
            </span>
          </div>
        </template>
        <div class="flow">
          <template v-for="(node, index) in flow" :key="node.label">
            <div class="flow-node" role="button" tabindex="0" @click="router.push(node.route)" @keyup.enter="router.push(node.route)">
              <div class="flow-label">{{ node.label }}</div>
              <el-tag size="small" :type="roleTone[node.role]" effect="light">{{ ROLE_LABEL[node.role] }}</el-tag>
              <div class="flow-desc">{{ node.desc }}</div>
            </div>
            <div v-if="index < flow.length - 1" class="flow-arrow">→</div>
          </template>
        </div>
        <p class="flow-note">
          Click any step to walk the demo. AIRI never promotes an AI suggestion only because the model suggested it —
          every candidate faces the same experiments, and humans decide what ships.
        </p>
      </el-card>

      <el-row :gutter="16" class="section">
        <el-col :span="14">
          <el-card shadow="never" class="fill">
            <template #header>Recent activity</template>
            <el-empty v-if="recent.length === 0" description="No seeded metric yet — run scripts/seed_demo.py" />
            <div v-for="item in recent" :key="item.title" class="activity">
              <div class="activity-title">{{ item.title }}</div>
              <div class="activity-detail mono">{{ item.detail }}</div>
              <div class="activity-time">{{ formatDateTime(item.time) }}</div>
            </div>
          </el-card>
        </el-col>
        <el-col :span="10">
          <el-card shadow="never" class="fill">
            <template #header>Runtime / Governance</template>
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="Runtime mode">{{ meta?.execution_mode ?? '—' }}</el-descriptions-item>
              <el-descriptions-item label="Environment">{{ meta?.environment ?? '—' }}</el-descriptions-item>
              <el-descriptions-item label="LLM mode">{{ meta?.llm_mode ?? '—' }}</el-descriptions-item>
              <el-descriptions-item label="Scenario families">
                {{ meta?.scenarios?.length ? meta.scenarios.join(' · ') : '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="Real production verified">
                <el-tag size="small" type="warning" effect="plain">NO — not production verified</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="Active version">
                {{ activeVersion ? `${activeVersion.metric_key} ${activeVersion.version}` : '—' }}
              </el-descriptions-item>
            </el-descriptions>
            <p class="honest-banner-note">
              AIRI's production governance (Phase 7–9) is fully implemented in the backend but is <strong>not</strong>
              verified against a real cluster in this demo.
            </p>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<style scoped>
.err {
  margin-bottom: 16px;
}

.kpis {
  margin-bottom: 16px;
}

.kpi {
  text-align: left;
}

.kpi-value {
  font-size: 26px;
  font-weight: 700;
}

.kpi-value.small {
  font-size: 16px;
}

.kpi-label {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
  margin-top: 4px;
}

.section {
  margin-bottom: 16px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 650;
}

.legend {
  display: flex;
  gap: 8px;
}

.flow {
  display: flex;
  align-items: stretch;
  gap: 4px;
  overflow-x: auto;
  padding: 4px 0;
}

.flow-node {
  min-width: 118px;
  flex: 1;
  border: 1px solid var(--airi-border);
  border-radius: 8px;
  padding: 10px;
  cursor: pointer;
  background: #fbfcfe;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.flow-node:hover,
.flow-node:focus {
  border-color: var(--airi-primary);
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.12);
  outline: none;
}

.flow-label {
  font-weight: 650;
  margin-bottom: 6px;
}

.flow-desc {
  color: var(--airi-text-secondary);
  font-size: 11.5px;
  margin-top: 6px;
  line-height: 1.4;
}

.flow-arrow {
  align-self: center;
  color: var(--airi-text-secondary);
  font-size: 16px;
}

.flow-note {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
  margin: 12px 0 0;
}

.fill {
  height: 100%;
}

.activity {
  padding: 10px 0;
  border-bottom: 1px solid var(--airi-border);
}

.activity:last-child {
  border-bottom: none;
}

.activity-title {
  font-weight: 600;
}

.activity-detail {
  color: var(--airi-text-secondary);
  margin-top: 2px;
}

.activity-time {
  color: var(--airi-text-secondary);
  font-size: 12px;
  margin-top: 2px;
}

.honest-banner-note {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
  margin: 12px 0 0;
}
</style>
