<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { describeApiError } from '../api/client'
import { runTests } from '../api/development'
import { useDemoState } from '../composables/demo'
import type { TestingResponse } from '../types/airi'
import StatusTag from '../components/StatusTag.vue'
import { TEST_TYPE_LABELS } from '../utils/status'

const route = useRoute()
const router = useRouter()
const { state, reset } = useDemoState()

const artifactId = computed(
  () => (route.query.artifact_id as string | undefined) ?? state.artifactId ?? '',
)
const approvalId = computed(
  () => (route.query.approval_id as string | undefined) ?? state.approvalId ?? '',
)
const metricName = computed(
  () => (route.query.metric_name as string | undefined) ?? state.metricName ?? '',
)

const running = ref(false)
const error = ref('')
const response = ref<TestingResponse | null>(null)
const expanded = ref<string | null>(null)

const report = computed(() => response.value?.report ?? null)
const passedCount = computed(() => report.value?.passed ?? 0)
const total = computed(() => report.value?.total ?? 0)
const canProceed = computed(
  () => report.value?.status === 'passed' || report.value?.status === 'passed_with_warnings',
)

async function start() {
  if (!artifactId.value || !approvalId.value) {
    error.value = 'Missing artifact / approval context — start from Metric Development.'
    return
  }
  if (state.currentStep === 0) reset()
  running.value = true
  error.value = ''
  try {
    response.value = await runTests({
      artifact_id: artifactId.value,
      approval_id: approvalId.value,
      max_rows: 1000,
    })
  } catch (e) {
    error.value = describeApiError(e)
  } finally {
    running.value = false
  }
}

function toggleDetails(testCaseId: string) {
  expanded.value = expanded.value === testCaseId ? null : testCaseId
}

function toExperiment() {
  if (!report.value) return
  router.push({
    path: '/experiments',
    query: {
      artifact_id: artifactId.value,
      approval_id: approvalId.value,
      test_run_id: report.value.test_run_id ?? report.value.execution_run_id,
      metric_name: metricName.value,
      anchor_time: (route.query.anchor_time as string | undefined) ?? '',
    },
  })
}
</script>

<template>
  <div class="page">
    <h1 class="page-title">Metric Testing</h1>
    <p class="page-subtitle">
      The scenario's own declaration decides which deterministic verifications run — six for the
      invoice family, eight for the relationship family. The SQL executed here comes from
      the approved artifact — the browser never sends SQL.
    </p>

    <el-card shadow="never" class="section">
      <el-descriptions :column="3" size="small" border>
        <el-descriptions-item label="Metric">
          <span class="mono">{{ metricName || '—' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="Artifact">
          <span class="mono">{{ artifactId ? artifactId.slice(0, 18) + '…' : '—' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="Approval">
          <span class="mono">{{ approvalId ? approvalId.slice(0, 18) + '…' : '—' }}</span>
        </el-descriptions-item>
      </el-descriptions>
      <div class="run-row">
        <el-button type="primary" :loading="running" :disabled="!artifactId || !approvalId" @click="start">
          Run Automated Tests
        </el-button>
        <el-button v-if="!artifactId || !approvalId" tag="router-link" to="/development">
          Go to Metric Development
        </el-button>
      </div>
    </el-card>

    <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" class="section" />

    <template v-if="report">
      <el-card shadow="never" class="section">
        <div class="summary">
          <div class="summary-score" :class="report.status === 'failed' ? 'bad' : 'good'">
            {{ passedCount }} / {{ total }} Passed
          </div>
          <StatusTag :status="report.status" />
          <span v-if="report.warnings > 0" class="summary-warn">{{ report.warnings }} warning(s)</span>
        </div>
      </el-card>

      <el-card
        v-for="item in report.results"
        :key="item.type + (item.test_case_id ?? '')"
        shadow="never"
        class="test-card"
        :data-status="item.status"
      >
        <div class="test-line" role="button" tabindex="0" @click="toggleDetails(item.type)" @keyup.enter="toggleDetails(item.type)">
          <span class="test-icon" :class="item.status">{{ item.status === 'passed' ? '✓' : item.status === 'failed' ? '✗' : '!' }}</span>
          <span class="test-name">{{ TEST_TYPE_LABELS[item.type] ?? item.type }}</span>
          <StatusTag :status="item.status" />
        </div>
        <div class="test-evidence">
          <span class="evidence-label">Expected:</span> <span class="mono">{{ item.expected_summary }}</span>
        </div>
        <div class="test-evidence">
          <span class="evidence-label">Actual:</span> <span class="mono">{{ item.actual_summary }}</span>
        </div>
        <el-collapse v-if="item.details && Object.keys(item.details).length > 0" class="test-details">
          <el-collapse-item :title="expanded === item.type ? 'Hide details' : 'Details'" :name="item.type">
            <pre class="mono details-pre">{{ JSON.stringify(item.details, null, 2) }}</pre>
          </el-collapse-item>
        </el-collapse>
      </el-card>

      <el-card v-if="report.classifications && report.classifications.length > 0" shadow="never" class="section">
        <template #header><span class="card-title">Failure classification</span></template>
        <div v-for="c in report.classifications" :key="c.category" class="classification">
          <el-tag size="small" type="danger" effect="plain">{{ c.category }}</el-tag>
          <span class="mono causes">{{ c.possible_causes.join(' · ') }}</span>
          <div class="actions-hint">Recommended: {{ c.recommended_actions.join('; ') }}</div>
        </div>
      </el-card>

      <el-card shadow="never" class="section">
        <template #header><span class="card-title">Next step</span></template>
        <el-button v-if="canProceed" type="primary" size="large" @click="toExperiment">
          Run Experiment →
        </el-button>
        <el-alert v-else type="warning" title="Resolve test failures before running an experiment — the gate is on the backend, not bypassed in the UI." :closable="false" />
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.section {
  margin-bottom: 16px;
}

.card-title {
  font-weight: 650;
}

.run-row {
  margin-top: 12px;
  display: flex;
  gap: 12px;
}

.summary {
  display: flex;
  align-items: center;
  gap: 16px;
}

.summary-score {
  font-size: 26px;
  font-weight: 750;
}

.summary-score.good {
  color: var(--airi-pass);
}

.summary-score.bad {
  color: var(--airi-fail);
}

.summary-warn {
  color: var(--airi-warn);
  font-size: 13px;
}

.test-card {
  margin-bottom: 10px;
}

.test-card[data-status='failed'] {
  border-color: var(--airi-fail);
}

.test-line {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}

.test-icon {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
}

.test-icon.passed {
  background: #dcfce7;
  color: var(--airi-pass);
}

.test-icon.failed {
  background: #fee2e2;
  color: var(--airi-fail);
}

.test-icon.warning,
.test-icon.skipped {
  background: #fef3c7;
  color: var(--airi-warn);
}

.test-name {
  flex: 1;
  font-weight: 600;
}

.test-evidence {
  margin-top: 8px;
  font-size: 12.5px;
  color: var(--airi-text);
}

.evidence-label {
  color: var(--airi-text-secondary);
}

.test-details {
  margin-top: 8px;
  border-top: none;
}

.details-pre {
  background: #f8fafc;
  border: 1px solid var(--airi-border);
  border-radius: 6px;
  padding: 10px;
  overflow: auto;
  max-height: 220px;
}

.classification {
  padding: 8px 0;
}

.causes {
  color: var(--airi-text-secondary);
  margin-left: 8px;
}

.actions-hint {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
  margin-top: 4px;
}
</style>
