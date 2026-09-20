<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { describeApiError } from '../api/client'
import {
  createReflection,
  decideProposal,
  fetchProposals,
} from '../api/reflection'
import {
  createRefinement,
  decideRefinement,
  evaluateRefinement,
  fetchRefinement,
} from '../api/refinement'
import { submitReview, decideReview } from '../api/development'
import { canonicalHash } from '../utils/canonicalHash'
import { useDemoState } from '../composables/demo'
import type { ProposalView, RefinementReport } from '../types/airi'
import { formatPercent, formatScore, formatDelta } from '../utils/format'
import StatusTag from '../components/StatusTag.vue'

const route = useRoute()
const router = useRouter()
const { state, reset } = useDemoState()

const experimentRunId = computed(
  () => (route.query.experiment_run_id as string | undefined) ?? state.experimentRunId ?? '',
)

const analyzing = ref(false)
const error = ref('')
const reflection = ref<Awaited<ReturnType<typeof createReflection>> | null>(null)
const proposals = ref<ProposalView[]>([])
const decidingProposal = ref<string | null>(null)

const evaluating = ref<string | null>(null)
const refinements = ref<RefinementReport[]>([])
const CANDIDATE_WINDOWS = [60, 90] as const

const evidence = computed(() => reflection.value?.evidence ?? [])
const diagnostics = computed(() => reflection.value?.diagnostics ?? [])
const hypotheses = computed(() => reflection.value?.hypotheses ?? [])
const llmAvailable = computed(() => reflection.value?.llm_reflection_status === 'completed')

async function analyze() {
  if (!experimentRunId.value) {
    error.value = 'No experiment run context — start from the Experiment page.'
    return
  }
  if (state.currentStep === 0) reset()
  analyzing.value = true
  error.value = ''
  try {
    reflection.value = await createReflection([experimentRunId.value])
    proposals.value = await fetchProposals(reflection.value.run.reflection_run_id)
  } catch (e) {
    error.value = describeApiError(e)
  } finally {
    analyzing.value = false
  }
}

async function decide(proposal: ProposalView, decision: 'accepted_for_investigation' | 'rejected' | 'need_more_evidence') {
  if (!reflection.value) return
  decidingProposal.value = proposal.proposal_id
  error.value = ''
  try {
    await decideProposal(
      reflection.value.run.reflection_run_id,
      proposal.proposal_id,
      decision,
      'demo-reviewer',
      'Simulated human decision in the Web demo',
    )
    proposals.value = await fetchProposals(reflection.value.run.reflection_run_id)
  } catch (e) {
    error.value = describeApiError(e)
  } finally {
    decidingProposal.value = null
  }
}

/** Candidate window semantics: the baseline window plus the accepted candidate windows. */
async function evaluateCandidate(windowDays: number) {
  if (!reflection.value) return
  const accepted = proposals.value.some(
    (item) => item.decision?.decision === 'accepted_for_investigation',
  )
  if (!accepted) {
    error.value = 'Accept a proposal for investigation first — AIRI never evaluates an unaccepted AI suggestion.'
    return
  }
  const proposal = proposals.value.find((item) => item.decision?.decision === 'accepted_for_investigation')!
  evaluating.value = `w${windowDays}`
  error.value = ''
  try {
    const refinement = await createRefinement(
      reflection.value.run.reflection_run_id,
      proposal.proposal_id,
      windowDays,
    )
    // The candidate artifact needs the same governed SQL review as any draft.
    const approval = await submitReview({
      artifact_id: refinement.candidate.artifact_id,
      metric_ir_hash: await canonicalHash(refinement.definition.metric_ir),
      artifact_hash: refinement.candidate.content_hash,
    })
    await decideReview(approval.approval_id, 'approve', 'demo-reviewer', 'Simulated review of the candidate artifact')
    await evaluateRefinement(refinement.run.refinement_run_id)
    const finished = await fetchRefinement(refinement.run.refinement_run_id)
    refinements.value = [...refinements.value.filter((r) => r.run.refinement_run_id !== finished.run.refinement_run_id), finished]
  } catch (e) {
    error.value = describeApiError(e)
  } finally {
    evaluating.value = null
  }
}

async function decideCandidate(runId: string, decision: 'keep_baseline' | 'accept_candidate_for_further_validation') {
  error.value = ''
  try {
    const updated = await decideRefinement(runId, decision, 'demo-reviewer', 'Simulated governance decision')
    refinements.value = refinements.value.map((r) =>
      r.run.refinement_run_id === runId ? updated : r,
    )
  } catch (e) {
    error.value = describeApiError(e)
  }
}

function toRegistry() {
  router.push('/registry')
}
</script>

<template>
  <div class="page">
    <h1 class="page-title">Reflection & Refinement</h1>
    <p class="page-subtitle">
      Deterministic evidence stays on the left; the LLM's reflection on the right is clearly labelled a
      <strong>hypothesis</strong>. Experiments — not the model — decide whether a candidate is better.
    </p>

    <div class="honesty-banner principle">
      AI proposes. Experiment decides. Human governs — AIRI never promotes an AI suggestion only because the model suggested it.
    </div>

    <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" class="section" />

    <el-card v-if="!reflection" shadow="never" class="section">
      <p class="hint">
        {{ experimentRunId ? 'Experiment run ' + experimentRunId.slice(0, 8) + '… ready for reflection.' : 'No experiment context — run an experiment first.' }}
      </p>
      <el-button type="primary" :loading="analyzing" :disabled="!experimentRunId" @click="analyze">
        Analyze Experiment
      </el-button>
    </el-card>

    <template v-if="reflection">
      <el-row :gutter="16" class="section">
        <el-col :span="12">
          <el-card shadow="never" class="fill">
            <template #header>
              <span class="card-title">Deterministic Evidence</span>
              <el-tag size="small" type="success" effect="plain">computed by Python</el-tag>
            </template>
            <div v-for="item in evidence" :key="item.experiment_run_id" class="evidence-block">
              <el-descriptions :column="2" size="small" border>
                <el-descriptions-item label="Metric">{{ item.evaluation.metric_name }}</el-descriptions-item>
                <el-descriptions-item label="Coverage">{{ formatPercent(item.evaluation.coverage) }}</el-descriptions-item>
                <el-descriptions-item label="KS">{{ formatScore(item.evaluation.ks.value, 3) }}</el-descriptions-item>
                <el-descriptions-item label="IV">{{ formatScore(item.evaluation.iv) }}</el-descriptions-item>
              </el-descriptions>
            </div>
            <div class="findings">
              <div class="findings-title">Findings</div>
              <div v-for="finding in diagnostics" :key="finding.finding_id" class="finding">
                <el-tag size="small" :type="finding.severity === 'warning' ? 'warning' : 'info'" effect="plain">
                  {{ finding.finding_type }}
                </el-tag>
                <span class="finding-text">{{ finding.summary }}</span>
              </div>
            </div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never" class="fill">
            <template #header>
              <span class="card-title">AI Reflection</span>
              <el-tag size="small" :type="llmAvailable ? 'primary' : 'warning'" effect="plain">
                {{ llmAvailable ? `model: ${reflection.run.model}` : 'LLM unavailable — deterministic path only' }}
              </el-tag>
            </template>
            <p v-if="reflection.summary" class="llm-summary">{{ reflection.summary }}</p>
            <div v-for="hypothesis in hypotheses" :key="hypothesis.hypothesis_id" class="hypothesis">
              <div class="hypothesis-head">
                <el-tag size="small" type="primary" effect="light">Hypothesis</el-tag>
                <el-tag size="small" type="info" effect="plain">confidence: {{ hypothesis.confidence }}</el-tag>
                <el-tag size="small" type="warning" effect="plain">{{ hypothesis.status }}</el-tag>
              </div>
              <div class="hypothesis-body">{{ hypothesis.statement }}</div>
              <div class="hypothesis-refs mono">evidence: {{ hypothesis.evidence_refs.join(', ') }}</div>
            </div>
            <el-empty v-if="hypotheses.length === 0" description="No hypotheses produced" :image-size="60" />
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="section">
        <template #header>
          <span class="card-title">Refinement Proposals</span>
          <span class="hint-inline">Human decision required — simulated reviewer in this demo</span>
        </template>
        <el-empty v-if="proposals.length === 0" description="No proposals" :image-size="60" />
        <div v-for="proposal in proposals" :key="proposal.proposal_id" class="proposal">
          <div class="proposal-head">
            <el-tag size="small" type="warning" effect="light">{{ proposal.proposal.proposal_type }}</el-tag>
            <span class="proposal-target mono">{{ proposal.proposal.target }}</span>
            <StatusTag v-if="proposal.decision" :status="proposal.decision.decision" />
          </div>
          <div class="proposal-body">
            <span class="proposal-label">Current:</span> 30 days
            <span class="proposal-label candidate">Candidates:</span>
            <el-tag v-for="w in proposal.proposal.parameters?.candidate_windows ?? proposal.allowed_windows" :key="w" size="small" class="mono">
              {{ w }} days
            </el-tag>
          </div>
          <div class="proposal-reason">{{ proposal.proposal.reason }}</div>
          <div class="proposal-question">
            <span class="proposal-label">Validation question:</span> {{ proposal.proposal.validation_question }}
          </div>
          <div v-if="!proposal.decision" class="proposal-actions">
            <el-button
              type="primary"
              size="small"
              :loading="decidingProposal === proposal.proposal_id"
              @click="decide(proposal, 'accepted_for_investigation')"
            >
              Accept for Investigation
            </el-button>
            <el-button size="small" @click="decide(proposal, 'need_more_evidence')">Need More Evidence</el-button>
            <el-button size="small" type="danger" plain @click="decide(proposal, 'rejected')">Reject</el-button>
          </div>
        </div>
      </el-card>

      <el-card shadow="never" class="section">
        <template #header>
          <span class="card-title">Candidate Experiments</span>
          <span class="hint-inline">each candidate is re-generated and re-tested by the same pipeline</span>
        </template>
        <div class="candidate-actions">
          <el-button
            v-for="days in CANDIDATE_WINDOWS"
            :key="days"
            :loading="evaluating === `w${days}`"
            :disabled="evaluating !== null"
            @click="evaluateCandidate(days)"
          >
            Evaluate {{ days }}d candidate
          </el-button>
        </div>
        <div v-for="item in refinements" :key="item.run.refinement_run_id" class="comparison">
          <div class="comparison-head">
            <span class="comparison-title mono">
              baseline 30d vs candidate {{ item.definition.metric_ir.window?.size }}d
            </span>
            <StatusTag v-if="item.run.outcome" :status="item.run.outcome" />
          </div>
          <el-table v-if="item.comparison" :data="[item.comparison]" size="small" border>
            <el-table-column label="Metric" width="120">
              <template #default>—</template>
            </el-table-column>
            <el-table-column label="Baseline (30d)">
              <template #default="{ row }">
                cov {{ formatPercent(row.baseline.coverage) }} · KS {{ formatScore(row.baseline.ks.value, 2) }} · IV {{ formatScore(row.baseline.iv) }}
              </template>
            </el-table-column>
            <el-table-column :label="`Candidate (${item.definition.metric_ir.window?.size}d)`">
              <template #default="{ row }">
                cov {{ formatPercent(row.candidate.coverage) }} · KS {{ formatScore(row.candidate.ks.value, 2) }} · IV {{ formatScore(row.candidate.iv) }}
              </template>
            </el-table-column>
            <el-table-column label="Δ KS" width="90">
              <template #default="{ row }">{{ formatDelta(row.ks_delta) }}</template>
            </el-table-column>
            <el-table-column label="Δ IV" width="90">
              <template #default="{ row }">{{ formatDelta(row.iv_delta) }}</template>
            </el-table-column>
          </el-table>
          <div v-if="item.comparison" class="comparison-reasons">{{ item.comparison.reasons.join(' · ') }}</div>
          <div class="proposal-actions">
            <el-button size="small" type="primary" plain @click="decideCandidate(item.run.refinement_run_id, 'accept_candidate_for_further_validation')">
              Accept for Further Validation
            </el-button>
            <el-button size="small" @click="decideCandidate(item.run.refinement_run_id, 'keep_baseline')">
              Keep Baseline
            </el-button>
          </div>
        </div>
      </el-card>

      <el-card shadow="never" class="section">
        <template #header><span class="card-title">Next step</span></template>
        <el-button type="primary" size="large" @click="toRegistry">View Metric Registry →</el-button>
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

.principle {
  border-style: solid;
  background: #eff6ff;
  border-color: var(--airi-primary);
  color: #1e40af;
}

.fill {
  height: 100%;
}

.hint {
  color: var(--airi-text-secondary);
  margin: 0 0 12px;
}

.hint-inline {
  color: var(--airi-text-secondary);
  font-size: 12px;
  font-weight: 400;
}

.evidence-block {
  margin-bottom: 12px;
}

.findings {
  margin-top: 8px;
}

.findings-title {
  font-weight: 650;
  font-size: 13px;
  margin-bottom: 6px;
}

.finding {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 4px 0;
}

.finding-text {
  font-size: 13px;
}

.llm-summary {
  color: var(--airi-text);
  font-size: 13px;
  background: #f8fafc;
  border: 1px solid var(--airi-border);
  border-radius: 6px;
  padding: 10px;
}

.hypothesis {
  border: 1px dashed var(--airi-border);
  border-radius: 8px;
  padding: 10px;
  margin-top: 10px;
}

.hypothesis-head {
  display: flex;
  gap: 8px;
}

.hypothesis-body {
  margin-top: 8px;
  font-size: 13px;
}

.hypothesis-refs {
  color: var(--airi-text-secondary);
  margin-top: 6px;
  font-size: 11.5px;
}

.proposal {
  border: 1px solid var(--airi-border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}

.proposal-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.proposal-target {
  color: var(--airi-text-secondary);
}

.proposal-body {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.proposal-label {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
}

.proposal-label.candidate {
  margin-left: 12px;
}

.proposal-reason {
  margin-top: 8px;
  font-size: 13px;
}

.proposal-question {
  margin-top: 6px;
  font-size: 12.5px;
  color: var(--airi-text-secondary);
}

.proposal-actions {
  margin-top: 10px;
  display: flex;
  gap: 10px;
}

.candidate-actions {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
}

.comparison {
  border-top: 1px solid var(--airi-border);
  padding-top: 12px;
  margin-top: 12px;
}

.comparison-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.comparison-title {
  font-weight: 650;
}

.comparison-reasons {
  color: var(--airi-text-secondary);
  font-size: 12.5px;
  margin-top: 6px;
}
</style>
