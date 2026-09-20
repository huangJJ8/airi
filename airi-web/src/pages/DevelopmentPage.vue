<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { describeApiError } from '../api/client'
import { decideReview, generateMetric, submitReview } from '../api/development'
import { demoState, DEMO_EXAMPLES } from '../composables/demo'
import type { ApprovalRecord, DevelopmentResult } from '../types/airi'
import MetricIRViewer from '../components/MetricIRViewer.vue'
import SqlViewer from '../components/SqlViewer.vue'
import StatusTag from '../components/StatusTag.vue'
import { canonicalHash } from '../utils/canonicalHash'
import { formatDateTime } from '../utils/format'

const router = useRouter()

const requirement = ref(demoState.requirement || '')
const anchorTime = ref('2026-09-09T00:00:00+08:00')
const selectedExample = ref(DEMO_EXAMPLES[0]!.scenario)
const generating = ref(false)
const generateError = ref('')
const result = ref<DevelopmentResult | null>(null)
const showLineage = ref(false)

const submittingReview = ref(false)
const reviewError = ref('')
const approval = ref<ApprovalRecord | null>(null)
const deciding = ref(false)

const reviewed = computed(() => approval.value?.decision === 'approved')
const rejected = computed(() => approval.value?.decision === 'rejected')

/** The example picker decides which scenario family the requirement belongs to. */
const activeExample = computed(
  () => DEMO_EXAMPLES.find((example) => example.scenario === selectedExample.value)!,
)

const joinCount = computed(() => result.value?.metric_ir.joins?.length ?? 0)

function loadDemo() {
  requirement.value = activeExample.value.requirement
}

async function generate() {
  if (!requirement.value.trim()) return
  generating.value = true
  generateError.value = ''
  result.value = null
  approval.value = null
  try {
    result.value = await generateMetric(
      requirement.value.trim(),
      anchorTime.value,
      activeExample.value.scenario,
    )
  } catch (e) {
    generateError.value = describeApiError(e)
  } finally {
    generating.value = false
  }
}

/** Submit the deterministic SQL draft for simulated human review. */
async function submitForReview() {
  if (!result.value) return
  submittingReview.value = true
  reviewError.value = ''
  try {
    approval.value = await submitReview({
      artifact_id: result.value.artifact.artifact_id ?? result.value.artifact.content_hash,
      metric_ir_hash: await canonicalHash(result.value.metric_ir),
      artifact_hash: result.value.artifact.content_hash,
    })
  } catch (e) {
    reviewError.value = describeApiError(e)
  } finally {
    submittingReview.value = false
  }
}

async function decide(decision: 'approve' | 'reject') {
  if (!approval.value) return
  deciding.value = true
  reviewError.value = ''
  try {
    approval.value = await decideReview(
      approval.value.approval_id,
      decision,
      'demo-reviewer',
      decision === 'approve' ? 'Simulated demo review — SQL is deterministic and reviewed' : 'Rejected in this demo run',
    )
  } catch (e) {
    reviewError.value = describeApiError(e)
  } finally {
    deciding.value = false
  }
}

function continueToTesting() {
  if (!result.value || !approval.value) return
  router.push({
    path: '/testing',
    query: {
      artifact_id: approval.value.artifact_id,
      approval_id: approval.value.approval_id,
      metric_name: result.value.artifact.metric_name,
      anchor_time: result.value.execution_context.anchor_time,
    },
  })
}
</script>

<template>
  <div class="page">
    <h1 class="page-title">Build a Risk Metric</h1>
    <p class="page-subtitle">
      Describe a risk indicator in natural language. AIRI's LLM understands the requirement —
      then skills and tools produce <strong>deterministic SQL</strong> that a human must review.
    </p>

    <el-card shadow="never" class="input-card">
      <el-input
        v-model="requirement"
        type="textarea"
        :rows="2"
        placeholder="Describe your risk indicator..."
        aria-label="Risk indicator requirement"
      />
      <div class="input-actions">
        <el-select v-model="selectedExample" class="example-select" aria-label="Demo example scenario">
          <el-option
            v-for="example in DEMO_EXAMPLES"
            :key="example.scenario"
            :value="example.scenario"
            :label="example.label"
          />
        </el-select>
        <el-button @click="loadDemo">Load Demo</el-button>
        <span class="anchor">
          <span class="anchor-label">Anchor time</span>
          <el-date-picker v-model="anchorTime" type="datetime" value-format="YYYY-MM-DDTHH:mm:ssZ" />
        </span>
        <el-button
          type="primary"
          :loading="generating"
          :disabled="!requirement.trim()"
          @click="generate"
        >
          Generate Metric
        </el-button>
      </div>
    </el-card>

    <el-alert v-if="generateError" type="error" :title="generateError" show-icon :closable="false" class="gap" />

    <template v-if="result">
      <el-card shadow="never" class="section">
        <template #header><span class="card-title">Requirement Understanding</span></template>
        <el-descriptions :column="3" size="small" border>
          <el-descriptions-item label="Scenario">
            {{ result.skill_plan.scenario_skill.name }}
          </el-descriptions-item>
          <el-descriptions-item label="Entity">{{ result.metric_ir.entity_type ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="Aggregation">{{ result.metric_ir.aggregation.function.toUpperCase() }}</el-descriptions-item>
          <el-descriptions-item label="Field">{{ result.metric_ir.aggregation.field ?? 'COUNT' }}</el-descriptions-item>
          <el-descriptions-item label="Window">
            {{ result.metric_ir.window ? `${result.metric_ir.window.size} ${result.metric_ir.window.unit}s` : '— (state of record)' }}
          </el-descriptions-item>
          <el-descriptions-item label="Joins">
            {{ joinCount > 0 ? `${joinCount} (via ${result.metric_ir.joins![0]!.conditions[0]!.left.field})` : '— (single source)' }}
          </el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-row :gutter="16" class="section">
        <el-col :span="12">
          <el-card shadow="never" class="fill">
            <template #header><span class="card-title">Skill Planning</span></template>
            <div class="plan-row">
              <span class="plan-label">Scenario Skill</span>
              <el-tag size="small" effect="plain" class="mono">
                {{ result.skill_plan.scenario_skill.name }}@{{ result.skill_plan.scenario_skill.version }}
              </el-tag>
            </div>
            <div class="plan-row">
              <span class="plan-label">Capability Skills</span>
              <el-tag
                v-for="capability in result.skill_plan.capabilities"
                :key="capability.name"
                size="small"
                type="info"
                effect="plain"
                class="mono capability"
              >
                {{ capability.name }}@{{ capability.version }}
              </el-tag>
            </div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never" class="fill">
            <template #header>
              <span class="card-title">Tool Planning</span>
              <span class="tool-note">Skills choose tools — the tool generates the SQL</span>
            </template>
            <div class="plan-row">
              <span class="plan-label">Selected Tool</span>
              <el-tag
                v-for="tool in result.tool_plan.tools"
                :key="tool.name"
                size="small"
                type="warning"
                effect="plain"
                class="mono capability"
              >
                {{ tool.name }}@{{ tool.version }}
              </el-tag>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="section">
        <template #header><span class="card-title">Metric IR</span></template>
        <MetricIRViewer :ir="result.metric_ir" />
      </el-card>

      <el-card shadow="never" class="section">
        <template #header><span class="card-title">Generated SQL</span></template>
        <SqlViewer :code="result.artifact.code" />
        <div class="validation-line">
          <StatusTag :status="result.validation.valid ? 'passed' : 'failed'" />
          <span class="validation-text">
            Static validation: {{ result.validation.valid ? 'valid Spark SQL' : result.validation.errors.join('; ') }}
          </span>
          <el-tag v-if="(result.validation.warnings ?? []).length > 0" size="small" type="warning">
            {{ result.validation.warnings!.length }} warning(s)
          </el-tag>
        </div>
      </el-card>

      <el-card shadow="never" class="section">
        <template #header><span class="card-title">Human Review</span></template>
        <p class="review-note">
          The LLM never writes the final SQL directly. This draft was generated deterministically from the Metric IR —
          and still needs a human decision before anything runs.
        </p>
        <el-alert v-if="reviewError" type="error" :title="reviewError" show-icon :closable="false" class="gap" />
        <div v-if="!approval" class="review-actions">
          <el-button type="primary" :loading="submittingReview" @click="submitForReview">Submit for Human Review</el-button>
        </div>
        <div v-else class="review-actions">
          <StatusTag :status="approval.decision" />
          <span class="mono review-id">review {{ approval.approval_id.slice(0, 8) }}…</span>
          <template v-if="approval.decision === 'pending'">
            <el-tag size="small" type="warning" effect="plain">simulated human decision</el-tag>
            <el-button type="success" :loading="deciding" @click="decide('approve')">Approve as Reviewer</el-button>
            <el-button type="danger" plain :loading="deciding" @click="decide('reject')">Reject</el-button>
          </template>
          <span v-if="reviewed" class="reviewed-at">reviewed {{ formatDateTime(approval.reviewed_at) }}</span>
        </div>
      </el-card>

      <el-card shadow="never" class="section">
        <template #header><span class="card-title">Next step</span></template>
        <el-button v-if="reviewed" type="primary" size="large" @click="continueToTesting">
          Continue to Testing →
        </el-button>
        <el-alert
          v-else-if="rejected"
          type="error"
          title="Review rejected — adjust the requirement and regenerate."
          :closable="false"
        />
        <p v-else class="next-hint">Testing unlocks after the SQL review is approved.</p>
      </el-card>

      <el-card shadow="never" class="section">
        <el-collapse v-model="showLineage">
          <el-collapse-item title="Advanced — Development lineage (auditability)" name="lineage">
            <el-descriptions :column="2" size="small" border>
              <el-descriptions-item label="Artifact ID">
                <span class="mono">{{ result.artifact.artifact_id ?? '—' }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="Artifact version">
                <span class="mono">{{ result.artifact.artifact_version ?? '—' }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="Artifact content hash">
                <span class="mono">{{ result.artifact.content_hash }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="Prompt version">
                <span class="mono">{{ result.prompt_version }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="Workflow run">
                <span class="mono">{{ result.workflow_run_id }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="Anchor time">
                <span class="mono">{{ result.execution_context.anchor_time }}</span>
              </el-descriptions-item>
            </el-descriptions>
          </el-collapse-item>
        </el-collapse>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.input-card {
  margin-bottom: 16px;
}

.input-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
}

.example-select {
  width: 300px;
}

.anchor {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.anchor-label {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
  white-space: nowrap;
}

.section {
  margin-bottom: 16px;
}

.card-title {
  font-weight: 650;
}

.gap {
  margin-bottom: 16px;
}

.fill {
  height: 100%;
}

.plan-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 6px 0;
}

.plan-label {
  width: 140px;
  color: var(--airi-text-secondary);
  font-size: 12.5px;
}

.capability {
  margin-right: 4px;
}

.tool-note {
  color: var(--airi-text-secondary);
  font-size: 12px;
  font-weight: 400;
}

.validation-line {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}

.validation-text {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
}

.review-note {
  color: var(--airi-text-secondary);
  font-size: 13px;
  margin: 0 0 12px;
}

.review-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.review-id {
  color: var(--airi-text-secondary);
}

.reviewed-at {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
}

.next-hint {
  color: var(--airi-text-secondary);
  margin: 0;
}
</style>
