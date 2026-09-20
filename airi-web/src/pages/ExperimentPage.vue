<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { describeApiError } from '../api/client'
import { createExperiment, fetchExperimentRun, fetchLatestDataset, fetchLatestLabel, runExperiment } from '../api/experiments'
import { useDemoState } from '../composables/demo'
import type { ExperimentReport } from '../types/airi'
import { formatPercent, formatScore, riskDirection } from '../utils/format'

const route = useRoute()
const router = useRouter()
const { state, reset } = useDemoState()

const artifactId = computed(() => (route.query.artifact_id as string | undefined) ?? state.artifactId ?? '')
const approvalId = computed(() => (route.query.approval_id as string | undefined) ?? state.approvalId ?? '')
const testRunId = computed(() => (route.query.test_run_id as string | undefined) ?? state.testRunId ?? '')
const metricName = computed(() => (route.query.metric_name as string | undefined) ?? state.metricName ?? '')
/** Must equal the artifact anchor or the backend gate refuses with metric_anchor_mismatch. */
const anchorTime = computed(() => (route.query.anchor_time as string | undefined) || '2026-09-09T00:00:00+08:00')

/** Label window of the synthetic fixture (same as scripts/seed_demo.py). */
const LABEL_WINDOW = {
  start: '2026-09-10T00:00:00+08:00',
  end: '2026-10-09T00:00:00+08:00',
}

/** Seeded dataset snapshots and label definitions, one per demo family
 * (scripts/seed_demo.py registers both). */
const DEFAULT_DATASET = 'invoice_sample_20260909'
const DEFAULT_LABEL = 'synthetic_binary_label'
const RELATION_DATASET = 'enterprise_relation_sample_20260911'
const RELATION_LABEL = 'synthetic_relation_label'
const RELATION_METRIC = 'related_enterprise_count'

function isRelationMetric(): boolean {
  return metricName.value === RELATION_METRIC
}

const running = ref(false)
const error = ref('')
const report = ref<ExperimentReport | null>(null)

const distChartEl = ref<HTMLDivElement | null>(null)
const liftChartEl = ref<HTMLDivElement | null>(null)
let distChart: echarts.ECharts | null = null
let liftChart: echarts.ECharts | null = null

const evaluation = computed(() => report.value?.evaluation ?? null)

const thresholdRows = computed(() => evaluation.value?.threshold_candidates ?? [])

function bucketLabel(bin: { lower?: string | null; upper?: string | null; bucket?: number }): string {
  if (bin.lower !== undefined && bin.upper !== undefined && bin.lower !== null && bin.upper !== null) {
    return `${Number(bin.lower).toFixed(0)}–${Number(bin.upper).toFixed(0)}`
  }
  return `bin ${bin.bucket ?? '?'}`
}

function renderCharts() {
  const evaluationValue = evaluation.value
  if (!evaluationValue) return
  const bins = evaluationValue.bins.filter((bin) => !bin.is_null)

  if (distChartEl.value) {
    distChart ??= echarts.init(distChartEl.value)
    distChart.setOption({
      grid: { left: 48, right: 16, top: 32, bottom: 28 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: bins.map((bin) => bucketLabel(bin)) },
      yAxis: { type: 'value', name: 'samples' },
      series: [
        {
          name: 'good',
          type: 'bar',
          stack: 'total',
          itemStyle: { color: '#93c5fd' },
          data: bins.map((bin) => bin.good ?? 0),
        },
        {
          name: 'bad',
          type: 'bar',
          stack: 'total',
          itemStyle: { color: '#ef4444' },
          data: bins.map((bin) => bin.bad ?? 0),
        },
      ],
    })
  }

  if (liftChartEl.value) {
    liftChart ??= echarts.init(liftChartEl.value)
    liftChart.setOption({
      grid: { left: 52, right: 52, top: 32, bottom: 28 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: bins.map((bin) => bucketLabel(bin)) },
      yAxis: [
        { type: 'value', name: 'bad rate', axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` } },
        { type: 'value', name: 'lift' },
      ],
      series: [
        {
          name: 'bad_rate',
          type: 'bar',
          itemStyle: { color: '#2563eb' },
          data: bins.map((bin) => bin.bad_rate ?? 0),
        },
        {
          name: 'lift',
          type: 'line',
          yAxisIndex: 1,
          itemStyle: { color: '#d97706' },
          data: bins.map((bin) => bin.lift ?? null),
        },
      ],
    })
  }
}

// flush:'post' — render after the DOM patch, otherwise the chart container
// refs are still null when the report arrives and the charts never mount.
watch(evaluation, () => renderCharts(), { flush: 'post' })

onMounted(async () => {
  if (state.currentStep === 0) reset()
  const existingRun = (route.query.experiment_run_id as string | undefined) ?? state.experimentRunId
  if (existingRun) {
    running.value = true
    try {
      report.value = await fetchExperimentRun(existingRun)
    } catch (e) {
      error.value = describeApiError(e)
    } finally {
      running.value = false
    }
    return
  }
  if (artifactId.value && approvalId.value && testRunId.value) {
    await startExperiment()
  }
})

async function startExperiment() {
  running.value = true
  error.value = ''
  try {
    const [dataset, label] = await Promise.all([
      fetchLatestDataset(isRelationMetric() ? RELATION_DATASET : DEFAULT_DATASET),
      fetchLatestLabel(isRelationMetric() ? RELATION_LABEL : DEFAULT_LABEL),
    ])
    if (!dataset || !label) {
      throw new Error(
        `No seeded dataset snapshot found (${isRelationMetric() ? RELATION_DATASET : DEFAULT_DATASET}) — run scripts/seed_demo.py first.`,
      )
    }
    const spec = await createExperiment({
      experiment_name: `web_demo_${metricName.value || 'invoice_metric'}_evaluation`,
      metric: { artifact_id: artifactId.value },
      approval_id: approvalId.value,
      test_run_id: testRunId.value,
      dataset_snapshot_id: dataset.dataset_snapshot_id,
      label_definition_id: label.label_definition_id,
      anchor_time: anchorTime.value,
      observation_time: anchorTime.value,
      label_window: LABEL_WINDOW,
    })
    report.value = await runExperiment(spec.experiment_spec_id)
  } catch (e) {
    error.value = describeApiError(e)
  } finally {
    running.value = false
  }
}

function toReflection() {
  if (!report.value) return
  router.push({
    path: '/reflection',
    query: {
      experiment_run_id: report.value.run.experiment_run_id,
      artifact_id: artifactId.value,
      approval_id: approvalId.value,
      test_run_id: testRunId.value,
      metric_name: metricName.value,
      anchor_time: anchorTime.value,
    },
  })
}

onBeforeUnmount(() => {
  distChart?.dispose()
  liftChart?.dispose()
})
</script>

<template>
  <div class="page" v-loading="running">
    <h1 class="page-title">Experiment</h1>
    <p class="page-subtitle">
      Python computes the numbers: coverage, separation (KS), information value (IV) and lift by bin.
      Nothing on this page is estimated in the browser.
    </p>

    <div class="honesty-banner">
      Synthetic Data — for demonstration only. Results do not represent real financial performance.
    </div>

    <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" class="section" />

    <el-card v-if="!report && !running && !error" shadow="never" class="section">
      <p class="hint">Open this page from a passed test run, or paste an experiment run id in Advanced.</p>
      <el-button type="primary" :disabled="!artifactId || !approvalId || !testRunId" @click="startExperiment">
        Run Experiment
      </el-button>
      <span v-if="!artifactId || !approvalId || !testRunId" class="hint-inline">
        Missing context — go back to Testing.
      </span>
    </el-card>

    <template v-if="report">
      <el-row :gutter="16" class="section">
        <el-col :span="6">
          <el-card shadow="never" class="kpi">
            <div class="kpi-value">{{ formatPercent(evaluation?.coverage) }}</div>
            <div class="kpi-label">Coverage</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" class="kpi">
            <div class="kpi-value">{{ formatScore(evaluation?.ks.value, 3) }}</div>
            <div class="kpi-label">KS</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" class="kpi">
            <div class="kpi-value">{{ formatScore(evaluation?.iv, 2) }}</div>
            <div class="kpi-label">IV</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" class="kpi">
            <div class="kpi-value kpi-direction">{{ riskDirection(evaluation?.ks.direction) }}</div>
            <div class="kpi-label">Risk direction</div>
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="16" class="section">
        <el-col :span="12">
          <el-card shadow="never" class="fill">
            <template #header><span class="card-title">Score distribution by bin</span></template>
            <div ref="distChartEl" class="chart" />
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never" class="fill">
            <template #header><span class="card-title">Bad rate & lift by bin</span></template>
            <div ref="liftChartEl" class="chart" />
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="section">
        <template #header>
          <span class="card-title">Threshold Candidates</span>
          <el-tag size="small" type="info" effect="plain">Research Candidates — not a recommended production threshold</el-tag>
        </template>
        <el-table :data="thresholdRows" size="small" border>
          <el-table-column prop="threshold" label="Threshold" width="140" />
          <el-table-column prop="operator" label="Operator" width="90" />
          <el-table-column label="Sources" width="150">
            <template #default="{ row }">
              <span class="mono">{{ (row.sources ?? []).join(', ') }}</span>
            </template>
          </el-table-column>
          <el-table-column label="Hit rate">
            <template #default="{ row }">{{ formatPercent(row.hit_rate) }}</template>
          </el-table-column>
          <el-table-column label="Precision">
            <template #default="{ row }">{{ formatPercent(row.precision) }}</template>
          </el-table-column>
          <el-table-column label="Recall">
            <template #default="{ row }">{{ formatPercent(row.recall) }}</template>
          </el-table-column>
          <el-table-column label="Lift">
            <template #default="{ row }">{{ formatScore(row.lift) }}</template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card v-if="(report.warnings ?? []).length > 0" shadow="never" class="section">
        <template #header><span class="card-title">Warnings</span></template>
        <el-alert
          v-for="(warning, index) in report.warnings"
          :key="index"
          type="warning"
          :title="warning"
          :closable="false"
          class="warn-item"
        />
      </el-card>

      <el-card shadow="never" class="section">
        <template #header><span class="card-title">Next step</span></template>
        <el-button type="primary" size="large" @click="toReflection">Analyze Experiment →</el-button>
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
  margin-right: 12px;
}

.kpi {
  text-align: left;
}

.kpi-value {
  font-size: 26px;
  font-weight: 700;
}

.kpi-direction {
  font-size: 15px;
  padding-top: 6px;
}

.kpi-label {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
  margin-top: 4px;
}

.fill {
  height: 100%;
}

.chart {
  height: 300px;
  width: 100%;
}

.hint {
  color: var(--airi-text-secondary);
  margin: 0 0 12px;
}

.hint-inline {
  color: var(--airi-text-secondary);
  margin-left: 12px;
  font-size: 12.5px;
}

.warn-item {
  margin-bottom: 8px;
}
</style>
