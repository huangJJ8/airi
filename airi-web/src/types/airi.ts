/**
 * TypeScript interfaces for the AIRI API responses this UI consumes.
 *
 * Hand-written from the FastAPI OpenAPI schema (src/airi routers + pydantic
 * models). Deliberately limited to what the six demo pages render; the backend
 * remains the single source of truth for all domain semantics.
 */

export interface VersionedReference {
  name: string
  version: string
}

export interface ExecutionContext {
  anchor_time: string
  timezone?: string
}

/** A join edge declared by a relationship metric: alias ON left = right. */
export interface JoinSpec {
  alias: string
  join_type: 'inner' | 'left'
  source: { catalog?: string | null; database: string; table: string }
  conditions: {
    left: QualifiedField
    operator: '='
    right: QualifiedField
  }[]
}

export interface QualifiedField {
  alias: string
  field: string
}

/** A row-level exclusion, e.g. related_enterprise_id <> enterprise_id. */
export interface FieldComparison {
  left: QualifiedField
  operator: '=' | '<>'
  right: QualifiedField
}

export interface MetricIR {
  schema_version?: string
  name: string
  display_name?: string | null
  description: string
  entity_type?: string | null
  entity_key: string
  partition_field?: string | null
  source: { catalog?: string | null; database: string; table: string }
  aggregation: { function: string; field: string | null }
  dimensions?: string[]
  filters?: unknown[]
  window?: {
    size: number
    unit: string
    time_field: string
    timezone: string
  } | null
  source_alias?: string | null
  aggregation_alias?: string | null
  joins?: JoinSpec[]
  column_filters?: FieldComparison[]
  // DerivedMetricIR payloads carry their own shape; rendered as raw JSON.
  metric_type?: string
  [key: string]: unknown
}

export interface SQLValidationResult {
  valid: boolean
  errors: string[]
  warnings?: string[]
}

export interface DevelopmentArtifact {
  type?: string
  language?: string
  status?: string
  code: string
  warnings?: string[]
  template: VersionedReference
  artifact_id?: string
  artifact_version?: string
  metric_name: string
  content_hash: string
}

export interface DevelopmentResult {
  workflow_run_id: string
  metric_ir: MetricIR
  skill_plan: { scenario_skill: VersionedReference; capabilities: VersionedReference[] }
  tool_plan: { tools: VersionedReference[] }
  artifact: DevelopmentArtifact
  validation: SQLValidationResult
  execution_context: ExecutionContext
  prompt_version: string
  requires_human_review: true
}

export interface ApprovalRecord {
  approval_id: string
  workflow_run_id: string
  artifact_id: string
  artifact_version: string
  metric_name: string
  metric_ir_hash: string
  artifact_hash: string
  reviewer: string | null
  decision: 'pending' | 'approved' | 'rejected'
  comment: string | null
  created_at: string
  reviewed_at: string | null
}

export interface ExecutionRequest {
  artifact_id: string
  approval_id: string
  max_rows?: number
}

export interface LabelDefinition {
  label_definition_id: string
  name: string
  [key: string]: unknown
}

export interface MetricTestResult {
  test_case_id?: string
  type:
    | 'schema'
    | 'null'
    | 'duplicate'
    | 'window'
    | 'boundary'
    | 'join'
    | 'distinct'
    | 'self_exclusion'
    | 'missing_relation'
    | 'reconciliation'
  severity: 'error' | 'warning'
  status: 'passed' | 'warning' | 'failed' | 'skipped'
  failure_category?: string | null
  expected_summary: string
  actual_summary: string
  details?: Record<string, string | number | boolean>
}

export interface MetricTestReport {
  test_run_id?: string
  execution_run_id: string
  artifact_id: string
  status: 'passed' | 'passed_with_warnings' | 'failed'
  total: number
  passed: number
  warnings: number
  failed: number
  skipped: number
  created_at?: string
  finished_at?: string
  results: MetricTestResult[]
  classifications?: {
    category: string
    possible_causes: string[]
    recommended_actions: string[]
  }[]
}

export interface TestingResponse {
  execution: { execution_run_id: string; status: string; row_count?: number }
  report: MetricTestReport
}

export interface EvaluationBin {
  bucket?: number
  is_null?: boolean
  lower?: string | null
  upper?: string | null
  count?: number
  good?: number
  bad?: number
  bad_rate?: number | null
  lift?: number | null
  good_pct?: number | null
  bad_pct?: number | null
  woe?: number | null
  iv_component?: number | null
  [key: string]: unknown
}

export interface ThresholdCandidate {
  threshold?: string
  operator?: string
  sources?: string[]
  hit_count?: number
  hit_rate?: number | null
  bad_count?: number
  good_count?: number
  bad_rate?: number | null
  precision?: number | null
  recall?: number | null
  lift?: number | null
  [key: string]: unknown
}

export interface MetricEvaluation {
  metric_name: string
  total_sample: number
  labeled_sample: number
  good_count?: number
  bad_count?: number
  non_null_metric?: number
  null_metric?: number
  coverage: number | null
  bad_rate: number | null
  bins: EvaluationBin[]
  ks: { value: number | null; direction?: string; [key: string]: unknown }
  iv: number | null
  iv_status: 'available' | 'not_available'
  threshold_candidates: ThresholdCandidate[]
  warnings?: string[]
  [key: string]: unknown
}

export interface ExperimentSpec {
  experiment_name: string
  metric: { artifact_id: string; artifact_version?: string }
  approval_id: string
  test_run_id: string
  dataset_snapshot_id: string
  label_definition_id: string
  anchor_time: string
  observation_time: string
  label_window: { start: string; end: string }
}

export interface DatasetSnapshotInfo {
  dataset_snapshot_id: string
  name: string
  row_count: number
  snapshot_time: string
  [key: string]: unknown
}

export interface ExperimentReport {
  run: {
    experiment_run_id: string
    experiment_spec_id: string
    artifact_id: string
    status: 'pending' | 'running' | 'completed' | 'failed'
    metric_row_count?: number
    labeled_row_count?: number
  }
  execution_mode: 'mock' | 'spark_test'
  evaluation?: MetricEvaluation | null
  warnings?: string[]
  [key: string]: unknown
}

export interface DiagnosticFinding {
  finding_id: string
  finding_type: string
  severity: 'info' | 'warning'
  summary: string
  evidence_refs: string[]
  [key: string]: unknown
}

export interface RefinementHypothesis {
  hypothesis_id: string
  statement: string
  evidence_refs: string[]
  confidence: 'low' | 'medium' | 'high'
  status: 'unverified'
}

export interface RefinementProposal {
  proposal_type: string
  target: string
  action: string
  parameters?: { candidate_windows?: number[]; [key: string]: unknown }
  reason: string
  validation_question: string
  evidence_refs: string[]
  priority?: 'low' | 'medium' | 'high'
}

export interface ReflectionReport {
  run: {
    reflection_run_id: string
    mode: string
    status: string
    model: string
    [key: string]: unknown
  }
  experiment_run_ids: string[]
  evidence: {
    experiment_run_id: string
    evaluation: MetricEvaluation
    synthetic_data: boolean
    [key: string]: unknown
  }[]
  diagnostics: DiagnosticFinding[]
  summary: string
  llm_summary?: string | null
  llm_reflection_status: 'completed' | 'unavailable' | 'rejected'
  llm_output_kind: 'mock' | 'provider'
  hypotheses: RefinementHypothesis[]
  refinement_proposals?: RefinementProposal[]
  missing_evidence: string[]
  warnings?: string[]
  [key: string]: unknown
}

export interface ProposalView {
  proposal_id: string
  proposal: RefinementProposal
  automation_capability: 'supported' | 'manual_only'
  allowed_windows: number[]
  decision?: {
    decision: string
    reviewer?: string | null
    comment?: string | null
    [key: string]: unknown
  } | null
}

export interface RefinementReport {
  run: {
    refinement_run_id: string
    status: string
    outcome?: 'improved' | 'worse' | 'mixed' | 'inconclusive' | null
    candidate_artifact_id: string
    [key: string]: unknown
  }
  definition: { metric_ir: MetricIR; semantic_diff?: unknown; [key: string]: unknown }
  candidate: { artifact_id: string; content_hash: string; metric_ir_hash: string }
  candidate_experiment?: { experiment_run_id: string } | null
  comparison?: {
    baseline: MetricEvaluation
    candidate: MetricEvaluation
    coverage_delta: number | null
    ks_delta: number | null
    iv_delta: number | null
    outcome: 'improved' | 'worse' | 'mixed' | 'inconclusive'
    reasons: string[]
  } | null
  synthetic_data: boolean
  warnings?: string[]
  [key: string]: unknown
}

export interface MetricDefinition {
  metric_definition_id?: string
  metric_key: string
  display_name: string
  member_metric_names?: string[]
  active_version_id?: string | null
  created_at?: string
}

export interface MetricVersion {
  metric_version_id: string
  metric_key: string
  version: string
  status: 'registered' | 'release_candidate' | 'active' | 'retired'
  metric_name: string
  display_name?: string | null
  metric_ir: MetricIR
  metric_ir_hash: string
  artifact_id: string
  artifact_hash: string
  source_promotion_review_id?: string
  version_change?: { kind: string; previous_version?: string | null; changed_fields?: unknown[] }
  synthetic_data?: boolean
  created_at: string
}

export interface MetricReleaseEvent {
  event_id: string
  event_type: string
  actor: string
  metadata?: Record<string, unknown>
  created_at: string
}

export interface MetricVersionDiff {
  metric_key: string
  from_version: string
  to_version: string
  changed_fields: { field: string; before?: unknown; after?: unknown }[]
  unchanged: string[]
  classification: 'patch' | 'minor' | 'major'
}

export interface MetaInfo {
  app_name: string
  version: string
  environment: string
  execution_mode: string
  llm_mode: string
  production_deployed: false
  scenarios: string[]
}
