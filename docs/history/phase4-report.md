# AIRI Phase 4 Implementation Report

2026-09-15。Controlled Metric Refinement Loop：由已接受的研究提案确定性生成候选，候选单独审批后重新测试、实验、比较，最终由人工决定下一步。

本阶段仅自动转换 `invoice_amount_30d` 的 60/90 天 window_review。演示为 Mock／合成数据；真实 LLM、Spark/Hive/MySQL 尚未验收。没有替换基线、应用阈值或生产发布。

## 1. Repository Assessment

基于实际 Reflection、IR、审批、Tool、测试、实验、基础设施及其测试代码检查：

1. RefinementProposal 有 type/target/action/parameters/reason/validation_question/evidence_refs/priority/requires_human_review；没有独立 ID。
2. ReflectionReport 存在 reflection_reports.report_json，并关联 reflection_runs。
3. 本阶段为旧提案派生稳定 ID，不改写已有 ReflectionReport；通过提案查询 API 返回 ProposalView。
4. evidence_refs 指向 Finding 或 experiment/candidate 字段引用；ReflectionEvidence 保存实验 ID 和报告 hash。
5. Baseline IR 来自 development_artifacts.snapshot.metric_ir，并按 canonical_hash 校验。
6. RequirementParser 只接自然语言；不应为候选再调用它。
7. 原 DevelopmentWorkflow 把 parse 和 planning 连在 run 内；本次抽出内部 run_structured，复用后续规划和校验。
8. artifact → ApprovalService → ExecutionService → TestingWorkflow → ExperimentService 已完整关联；本次复用。
9. DerivedMetricIR 有 current/previous 两个依赖及固定偏移；窗口变化涉及多个语义字段，首版 manual_only。
10. 业务白名单、SparkSQLInput 和测试参考计算原限定 30 天。可执行范围只扩展金额窗口；其他提案不自动转换。

工作目录没有 Git 仓库，文件清单来自实际编辑记录；没有声称 Git 提交或基于历史提交做迁移分析。

## 2. Architecture Changes

新增 refinement 领域模块和分阶段 RefinementService。候选创建与评估分开，复用既有 development_artifacts，不创建第二套 SQL 审批。
新增版本化候选 Tool/Skill；原 30 天 Tool 1.1.0 和业务输入校验保持不变。Sandbox 增加受限金额 60/90 天白名单，测试参考计算相应扩展。

## 3. Added / Modified Files

新增：

- `src/airi/refinement/{__init__,models,transform,comparison,persistence,service,fixtures}.py`
- `src/airi/tools/candidate_sql.py`
- `src/airi/api/refinements.py`
- `migrations/versions/0006_controlled_refinement_controlled_refinement.py`
- `tests/test_refinement.py`
- `examples/demo_phase4.py`
- `examples/phase4/`：Baseline 规格/实验报告、Reflection、Proposal 列表/决策、60/90 天请求、候选草稿、SQL、评估报告、最终决策报告。
- `phase4-report.md`

修改：

- `src/airi/main.py`：新 Tool/Skill 和路由注册。
- `src/airi/workflows/development/{workflow,planning}.py`：内部结构化入口、精确候选 Tool 版本。
- `src/airi/execution/sandbox.py`：受限候选语义验证。
- `src/airi/testing/{reconciliation,runner}.py`：独立 60/90 天参考和动态边界探针。
- `src/airi/observability/logging.py`：谱系 IDs/outcome 白名单。
- `migrations/env.py`：新表元数据。
- `examples/demo_phase3.py`：可指定 fixture 和指标集合，默认行为不变。
- `README.md`：Phase 4 使用方式与验收。

迁移 0001–0005 未修改。原基线 IR/SQL 和已有实验报告没有覆盖。

## 4. Refinement Architecture

```text
GET reflection proposals → 稳定 proposal_id / capability
POST proposal decision → 人工接受研究
POST refinements
  → 校验提案与原实验 provenance
  → CandidateIRTransformer → semantic_diff
  → DevelopmentWorkflow.run_structured
  → SkillPlanner / DependencyValidator / ToolPlanner
  → GenerateCandidateSQL / Jinja / SQLValidator
  → 独立 Candidate draft + lineage
POST approvals → submit → approve/reject（独立人工 SQL 门禁）
POST refinements/{id}/evaluate
  → require candidate approved → TestingWorkflow
  → ExperimentService（复制原实验条件）
  → Compatibility Guard → BaselineCandidateComparison
  → RefinementReport
POST refinements/{id}/decision → 人工研究决策
```

普通同步 Python 服务编排，没有 Autonomous Loop、LangGraph 或额外 LLM 调用。

## 5. Proposal Decision Model

ProposalDecision 保存 decision_id、reflection_run_id、proposal_id、proposal_hash、decision、reviewer、comment、created_at 和 previous_decisions。
无记录表示尚待决定。API 支持 pending / accepted_for_investigation / rejected / need_more_evidence。
pending、need_more_evidence 可继续流转，历史保存在 JSON；accepted/rejected 为不可覆盖的终态。
唯一约束及状态条件更新防止重复决定。reviewer 仍是调用方声明身份，不是已认证生产审批人。

## 6. Proposal Capability Matrix

| 提案 | Baseline | 自动化 |
|---|---|---|
| window_review，包含 60/90 参数 | invoice_amount_30d | supported，仅允许交集内窗口 |
| window_review | count / derived / 其他 | manual_only |
| aggregation / coverage / null / dedup / source / business_rule | 任意 | manual_only |
| threshold / additional_dataset / additional_time_snapshot | 任意 | manual_only |

ProposalCapabilityMatrix 从提案和目标 IR 得出能力，不把“接受研究”视为已具备执行能力。不实现 SUM→AVG、过滤负金额、去重或 NULL 处理变化。

## 7. RefinementRequest

```json
{
  "reflection_run_id": "已完成 Reflection ID",
  "proposal_id": "提案查询 API 返回的 ID",
  "parameter_selection": {"window_days": 60}
}
```

严格禁止客户端传 SQL、IR、Skill/Tool Plan、Baseline override、Dataset、Label 或 policy。窗口必须在提案参数和系统支持的 {60,90} 交集中。
未接受返回 refinement_not_authorized，manual_only 返回 proposal_manual_only，非法选值返回 proposal_parameter_invalid 或请求 Schema 422。

## 8. Candidate Metric Definition

CandidateMetricDefinition 保存独立 candidate_metric_id、Baseline artifact/experiment、Reflection/proposal 来源、严格 MetricIR、IR hash 和 semantic_diff。
Candidate 名称确定性为 invoice_amount_60d 或 invoice_amount_90d。每次创建独立 UUID，不覆盖 Baseline。

## 9. CandidateIRTransformer

`transform_window` 先验证原 30 天金额语义，再复制并只修改 name、display_name、description、window.size。
source、enterprise、seller_tax_no、invoice_amt、SUM、invoice_date、dt、filters、dimensions 和时区保持一致。
同一输入得到相同 IR 语义 hash；创建 UUID 不影响语义一致性。

## 10. Semantic Diff

MetricSemanticDiff 包含全部实际变化（包括三个展示/名称字段及 window.size），并列出未变语义字段。
将四个允许字段复原后，完整 canonical hash 必须与 Baseline 一致；source/entity/aggregation/filter 等额外变化立即拒绝。
SQL 半开窗口固定为 `[anchor-window, anchor)`：60 天从 2026-07-11 开始，90 天从 2026-06-11 开始，均不包含 2026-09-09。

## 11. Candidate Artifact

CandidateArtifact 是 lineage 视图，role=candidate_draft；底层依旧保存 standard draft，避免扩展全局 Artifact 状态枚举。
具有 artifact_id/version/content_hash/metric_ir_hash，以及 baseline_artifact_id/refinement_run_id/proposal_id。

候选使用 invoice_risk@1.1.0 和 metric_window / metric_sum / spark_sql_generator@1.1.0；能力名称不变。
Tool 为 generate_spark_sql_metric@1.2.0，严格输入窗口为 60/90。复用未改动的 aggregation_metric 模板@1.1.0。
DevelopmentResult 的既有 prompt_version 兼容字段写 1.0.0；本链路实际执行的是确定性 transformer，不调用该 Prompt 或 Parser。

## 12. Candidate Approval

提案 accepted 只允许创建研究候选。候选仍需通过现有 `/approvals` 提交 IR/hash 并独立 approve。
evaluate 查询候选绑定的 approved 记录，调用 require_approved 重验具体内容；未批准返回 candidate_not_approved，测试不会开始。
评估前再次校验候选身份、内容/IR hash、Baseline 谱系与 anchor。没有隐式跨越 SQL 审批。

## 13. Candidate Testing

复用 SandboxGuard、ExecutionService、六类 MetricTestRunner、MetricTestReport 和其持久化。
独立 Python reference_calculate 支持金额 60/90 天；边界探针增加窗口前一天、窗口边界及后一天，覆盖半开区间。
passed / passed_with_warnings 可继续；failed 立即终止，RefinementRun.failed，candidate_test_failed，不创建候选实验。
测试警告关联测试报告，成功实验的警告也保留在最终 RefinementReport。

## 14. Candidate Experiment

服务端从原 Reflection 指向的 Baseline ExperimentSpec 恢复上下文，仅替换产物/审批/测试 ID 及实验名称。
数据/标签/观察时间/标签窗口/anchor/完整 EvaluationPolicy/环境验收 ID 不允许客户端改变。
候选实验必须 completed；失败生成 failed / inconclusive RefinementReport，不回退为 Baseline 或伪造 comparison。

## 15. Comparison Compatibility

再次调用 ComparisonCompatibilityGuard：相同 DatasetSnapshot ID、LabelDefinition ID、observation、label_window、完整 policy 和 synthetic 来源。
还检查当前 execution_mode、原/候选环境验收 ID/hash、source_mapping；不匹配为 comparison_invalid。
真实环境尚未验收，已有环境模型没有集群端点指纹/有效期绑定，因此这些检查不能冒充生产级集群身份保证。

## 16. Baseline / Candidate Comparison

MetricComparisonResult 完整保留两端 MetricEvaluation：coverage、KS、IV、risk_direction、actual_bins、分箱和全部 threshold candidates。
coverage_delta、ks_delta、iv_delta 由 Decimal 字符串转换后做差，最后展示 float；unavailable 保持 null。
阈值按共同 source 和相同 operator 匹配，并保留各自实际阈值和 same_numeric_threshold；匹配不表示数值边界或命中主体完全相同。
记录 hit_rate/precision/recall/lift delta，不选最终阈值。

## 17. Comparison Policy

RefinementComparisonPolicy@1.0.0：ks_material_delta=0.02，coverage_material_delta=0.02，min_labeled_samples=100，max_experiment_warnings=10。
参数完整进入 plan 和 refinement_input_hash。它只用于研究结果分类，不是生产“好指标”标准。
超过容忍度才算实质变化，恰好等于容忍度视为无实质变化。IV 作为背景证据，不作为单调越大越好的优化目标。

## 18. RefinementOutcome

- improved：至少一项 coverage/KS 实质改善，另一项未实质变差。
- worse：至少一项实质变差，另一项未实质改善。
- mixed：coverage 与 KS 出现实质反向变化。
- inconclusive：无实质变化、统计不可用、单类、标签样本不足或超出警告预算。

不可比较实验直接拒绝比较，运行保存 failed/inconclusive；不生成可比较的数值 delta。
分类由版本化 Policy 和 Python 决定，无 LLM 参与。所有 Outcome 均不触发 Baseline 替换。

## 19. 30d → 60d Result

实际合成演示：**worse**。

- Baseline coverage=0.9202127659574468，KS=0.576750700280112，IV=6.031774836511457。
- coverage_delta=0.0。
- ks_delta=-0.2907563025210084。
- iv_delta=-5.441059545122221。

覆盖率未改善而 KS 实质降低；IV 的下降单独记录，不解释为必然好或坏。
报告：[60 天结果](../../examples/phase4/60d_report.json)，SQL：[60 天 SQL](../../examples/phase4/invoice_amount_60d.sql)。

## 20. 30d → 90d Result

实际合成演示：**mixed**。

- coverage_delta=0.0797872340425532（覆盖率达到 1.0）。
- ks_delta=-0.4103790188641828。
- iv_delta=-5.650902068902891。

更早月份提供了更多覆盖，但分离证据降低；系统忠实保留取舍，没有选赢家。
报告：[90 天结果](../../examples/phase4/90d_report.json)，SQL：[90 天 SQL](../../examples/phase4/invoice_amount_90d.sql)。

## 21. RefinementReport Example

报告包含 run、plan、CandidateMetricDefinition、CandidateArtifact、CandidateExperiment、comparison、experiment_context、synthetic_data、warnings、missing_evidence、final_decision 和 requires_human_review=true。

```powershell
uv run --frozen python examples/demo_phase4.py
```

使用专用 `.demo/phase4_demo.db`，基于 200 主体固定合成样本，在原 fixture 上增加独立噪声的更早月份。没有调参强迫候选提升。
脚本显式模拟提案研究决定、独立 SQL 审批与最终 need_more_evidence 决策，仅适用于该合成 fixture。真实系统不会自动审批。
报告、请求、候选、决策和 SQL 全部写入 examples/phase4；重新运行创建新 UUID，数值内容可复现。

## 22. Lineage / Provenance

稳定 proposal_id = canonical_hash(reflection ID + 原提案序号 + 原提案内容)，ProposalView 派生该值而不改写旧 Reflection。
refinement_input_hash 涵盖完整 ReflectionReport hash、提案内容、研究决定（含历史）、Baseline IR hash、选择参数、comparison policy。

Baseline experiment → Reflection → Proposal → decision_id → refinement_run_id → candidate artifact/hash → approval → test → experiment → comparison 可完整回查。
同样输入的候选 IR/hash/SQL 相同，UUID 可不同。评估前重算输入 hash，输入变化立即拒绝。
当前数据库仍是信任边界；这些校验不是防恶意 DBA 的签名体系。

## 23. Database Schema

新增三张表：proposal_decisions、refinement_runs、refinement_reports。候选产物复用 development_artifacts，测试和实验复用原表。
proposal_decisions 对 reflection/proposal 唯一，记录决定和历史 JSON。
refinement_runs 保存 Baseline/Candidate/decision/test/experiment IDs、status/outcome、policy version、时间及 final_decision_recorded。
refinement_reports 保存完整报告 JSON 和可查询 delta/outcome；Human Review 标志在报告 Schema 中固定为 true。

evaluate 使用条件更新领取 pending_candidate_review，避免同一运行被重复执行；最终决定也用条件更新防止覆盖。
revision=`0006_controlled_refinement`，parent=`0005_experiment_reflection`；没有修改历史迁移。

## 24. APIs

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | /api/v1/reflections/{id}/proposals | 稳定 ID、能力、允许参数、已有决定 |
| POST | /api/v1/reflections/{id}/proposals/{pid}/decision | 人工研究决定 |
| POST | /api/v1/refinements | 201，生成一个独立候选和谱系 |
| POST | /api/v1/approvals | 复用候选 SQL 提交审批 |
| POST | /api/v1/approvals/{id}/approve | 复用具体 SQL 批准 |
| POST | /api/v1/refinements/{id}/evaluate | 测试、实验、比较，返回完成或失败报告 |
| GET | /api/v1/refinements/{id} | 读取报告 |
| POST | /api/v1/refinements/{id}/decision | 记录人工最终研究决定 |

evaluate 只接受省略 body 或 `{}`。无效 Schema 为 422，门禁为 409；缺资源沿用统一 404。
最终决定仅 keep_baseline / accept_candidate_for_further_validation / need_more_evidence / reject_candidate，不包含 production promotion。

## 25. Security / Human Governance Boundary

两个人工门禁互不替代。Candidate Generator 不调用 LLM；LLM 没有修改 SQL、IR 或比较结果的权限。
原 Baseline 永不更新。最终人工接受也仅允许后续验证，不更新 Baseline、不产生部署副作用。
reviewer 是声明身份；完整认证、生产审批与策略发布尚未实现。API 仅适用于受控研发环境。
日志白名单关联 request、Baseline experiment、Reflection、Proposal、decision、refinement、Candidate artifact/test/experiment 与 outcome，不记录 SQL、样本、标签或 Prompt。

## 26. Test Results

测试覆盖确定性 30→60/90、非法窗口、额外 source/entity/aggregation/filter 变更拒绝、参数必须来自提案、manual_only、决策状态及历史、独立候选审批、候选测试/实验失败、测试 warning、完整两条 E2E、谱系 hash 与 Baseline 不变。
Comparison 手算输入覆盖 improved/worse/mixed/inconclusive，以及不可比较 dataset/label/policy。

2026-09-15 实际执行：

| 命令 | 结果 |
|---|---|
| `uv run --frozen pytest` | 440 passed、5 skipped、2 warnings，79.70 秒 |
| `uv run --frozen pytest --cov=airi --cov-report=term-missing` | 440 passed、5 skipped、2 warnings，143.23 秒；96%，3635 statements / 144 missed |
| `uv run --frozen ruff check src tests` | All checks passed |
| `uv run --frozen ruff format --check src tests` | 125 files already formatted |
| `uv run --frozen alembic upgrade head` | 专用 SQLite `.demo/phase4_migration.db` 从 0005 升到 0006，成功 |
| `uv run --frozen alembic current` | 0006_controlled_refinement (head) |
| `uv run --frozen alembic check` | No new upgrade operations detected |

保留原 406 项回归，新增 34 项测试。4 项 Spark、1 项 MySQL 真实集成未选择/未配置而跳过，不能视为通过。
两个 warning 为 Starlette/httpx 与 AnyIO 的第三方弃用提示。

## 27. Known Limitations

- 真实 LLM、Spark/Hive/MySQL 仍未验收；本阶段 Mock Window Reflection 与合成实验不可外推真实金融业务效果。
- 仅金额 Baseline 30 天到 60/90 天；其他聚合、主体、规则、count 和 Derived Candidate 均不自动转换。
- 同步单候选运行；没有任务调度、取消、自动重试。进程退出可能留下中间状态。
- draft 保存与 refinement 元数据分阶段提交；基础设施失败可能留下未关联草稿，需要运维识别，不能将其当作已获研究批准候选。
- 公平性沿用现有环境 ID/hash 和 source_mapping，无集群指纹绑定；实际源表版本依赖上游不可变快照治理。
- 一次样本内比较不证明稳定性、因果改善或 OOT 泛化。没有 PSI、跨月/跨期实验或自动晋升。
- 研究 policy 的容忍度和警告预算尚需业务人员审阅；不是生产标准。
- 数据库是可信边界，reviewer 未认证。没有自动生产执行、GitLab、Agent Loop、模型训练或 Baseline 替换。

## 28. Recommended Next Step

仅推荐 **AIRI Phase 5 — Temporal Validation & Candidate Promotion Governance**。本次没有开始实现。
