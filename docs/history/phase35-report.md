# AIRI Phase 3.5 Implementation Report

2026-09-14。完成 Evidence-driven Reflection & Metric Refinement 的建议生成链路。
所有演示使用真实运行并持久化的 **Phase 3 合成实验**以及显式 **MockReflectionLLM**。
没有真实模型推理验收，也没有真实 Spark/Hive/MySQL 验收；没有执行任何指标改进或应用阈值。

## 1. Repository Assessment

本次检查了实际 experiments/evaluation、实验 workflow、Metric IR、Skill、审批、测试、LLM、配置、数据库、迁移、测试和示例代码。

1. `ExperimentReport` 包含 run、execution_mode、evaluation、join_summary、reproducibility、leakage_check、warnings、requires_human_review。不是只有 KS/IV 的简表。
2. `ExperimentEvaluationRow` 在 experiment_evaluations 中保存摘要列和完整 report_json；ExperimentRunRow 保存状态和关联规格。
3. ThresholdCandidate 保存 Decimal threshold、operator、sources、命中数/率、good/bad、precision/recall/lift，没有独立 candidate_id。
4. KS 为 status + nullable value，IV 为 iv + iv_status；coverage/lift 等无可用分母时为 null。Reflection 不补零。
5. warnings 同时存在 ExperimentReport 和 MetricEvaluation；抽取时合并去重。
6. ExperimentSpec 通过 dataset_snapshot_id、label_definition_id 关联快照和标签，含 observation_time、label_window、EvaluationPolicy。
7. artifact snapshot 保存 IR、Skill/Tool Plan、模板；实验 repro 记录相关哈希/版本。Reflection 验证这些关联并保留来源 hash。
8. 原 Phase 3 示例的三个实验分别登记了不同 dataset/label ID，因此不能按本阶段规则直接比较。新演示共享同一注册快照和标签，重新运行三个实验，原报告不变。
9. experiments 负责执行与持久化，evaluation 负责确定性统计；本次没有让 Reflection 调用统计重算或 SQL 执行。
10. 仓库没有既有 reflection 实现，本次新增独立模块。工作目录没有 Git 仓库，清单按实际编辑记录整理，没有声称执行 Git diff/commit。

## 2. Architecture Changes

新增 `reflection/` 领域模块，包含模型、证据抽取、诊断、兼容性检查、Prompt、建议验证和持久化。
`workflows/reflection/ReflectionWorkflow` 用普通 Python 编排；HTTP 适配器只接收实验 ID。
沿用 LLMClient 抽象和 OpenAI-compatible HTTP 适配器，没有 SDK 耦合、Agent Loop 或 LangGraph。

## 3. Added / Modified Files

新增：

- `src/airi/reflection/__init__.py`
- `src/airi/reflection/models.py`、`evidence.py`、`diagnostics.py`
- `src/airi/reflection/prompts.py`、`validation.py`、`llm.py`、`persistence.py`
- `src/airi/workflows/reflection/__init__.py`、`workflow.py`
- `src/airi/api/reflections.py`
- `migrations/versions/0005_experiment_reflection.py`
- `tests/test_reflection.py`
- `examples/demo_phase35.py`
- `examples/phase35/`：四份请求、四份 ReflectionReport，以及 experiments 子目录中的三份规格、三份实际 ExperimentReport。
- `phase35-report.md`

修改：

- `src/airi/main.py`：Reflection 路由、独立 LLM/Policy 依赖注入。
- `src/airi/infrastructure/llm.py`：为通用 Mock 增加明确 model_identifier，不改真实调用逻辑。
- `src/airi/observability/logging.py`：Reflection 关联 ID 白名单。
- `migrations/env.py`：新持久化表元数据。
- `examples/demo_phase3.py`：增加输出目录/演示库/共享快照参数，保留原命令默认行为。
- `README.md`：使用方式、边界和交付链接。

迁移 0001–0004 没有修改。没有修改 Metric IR、指标 SQL、统计计算或业务规则。

## 4. Reflection Architecture

```text
POST /api/v1/reflections
→ ReflectionSpec validation
→ ReflectionWorkflow.run
→ EvidenceExtractor.extract
  → completed 实验、报告、规格、产物、快照、标签
  → 严格 Schema、关联身份、内容 hash 校验
→ ComparisonCompatibilityGuard.validate（仅 comparison）
→ ReflectionDiagnostics.analyze / compare
→ ReflectionContext + input_evidence_hash
→ LLMClient.complete_structured
→ ReflectionLLMOutput Pydantic validation
→ RefinementProposalValidator.validate
→ ReflectionReport → SQLAlchemy commit → HTTP Response
→ Human Review（系统外）
```

没有 QueryExecutor 依赖，没有创建新指标、SQL、审批或实验的下游调用。

## 5. ReflectionSpec

```json
{
  "experiment_run_ids": ["已有 completed 实验的 ID"],
  "reflection_policy_version": "1.0.0",
  "mode": "single_metric"
}
```

single_metric 严格要求一个 ID；comparison 要求两个或三个不同 ID。仅支持 policy 1.0.0。
未知字段拒绝，客户端不能上传统计结果、报告、SQL、IR 或自由 Evidence。
所有 API 输入示例见 [examples/phase35](../../examples/phase35)。示例 ID 属于演示库，不能直接用于其他数据库。

## 6. ReflectionPolicy

服务端 `ReflectionPolicy@1.0.0`，默认参数：

| 字段 | 默认值 | 用途 |
|---|---:|---|
| coverage_warning_below | 0.85 | 高缺失研究提示 |
| ks_review_below | 0.1 | weak_separation 提示 |
| ks_strong_at_least | 0.3 | strong_separation 提示 |
| small_sample_below | 500 | 小样本研究提示 |
| few_bins_below | 3 | 有效箱数不足提示 |
| iv_sensitivity_above | 1.0 | 配合小样本、纯箱、平滑识别 IV 敏感性 |
| max_proposals | 5 | 最多五条研究提案 |

这些是可配置的 **研究提示阈值**，不是生产准入标准，也不代表业务价值排序。
Phase 3 的 min_labeled_samples=100 是另一个实验警告参数；本阶段 500 用于更保守的研究证据提示。
服务端可通过 create_app(reflection_policy=...) 注入；API 不接受任意 policy 参数。
完整 policy 写入报告与 input hash。改变 policy 含义时应发布新版本，而不是复用旧版本名。

## 7. Evidence Model

ReflectionEvidence 明确保存实验/产物/快照/标签身份、报告 hash、IR hash、产物版本、观察时间、标签窗口、完整 EvaluationPolicy、MetricEvaluation、join_summary、warnings、synthetic_data、严格 Metric IR 与 ScenarioSkill。

指标统计直接复制持久化结果，不调用 calculate，不从样本重算。提供全局唯一引用：

- `experiment:{run_id}:evaluation`
- `experiment:{run_id}:join_summary`
- `experiment:{run_id}:warnings`
- `experiment:{run_id}:synthetic_data`
- `experiment:{run_id}:candidate:{index}`
- `finding:{run_id}:{finding_type}`

候选索引是该报告内的位置，由 experiment_report_hash 绑定。references 给出所属实验和字段路径，可回查具体阈值、统计和诊断。LLM 输入没有主体明细、标签行、完整结果集或 SQL。

## 8. Deterministic Diagnostics

FindingType 是有限 Literal 集合。DiagnosticFinding 包含 finding_id、type、severity、experiment_run_ids、evidence_refs、summary。

- coverage_gap 描述有效标签样本的 NULL 数和缺失比例，并引用 join_summary；不宣称数据质量差。
- high_null_rate、weak/strong_separation、small_sample、few_effective_bins 使用 Policy。
- risk_direction_uncertain 对应已存储 undetermined；single_class 对应单类计数。
- high_iv_sensitivity 同时要求超过 advisory IV、小样本、存在纯箱、epsilon>0，仅提出敏感性风险，不断言过拟合。
- threshold_tradeoff 对已有候选两两检查：precision 与 recall 的变化方向相反才产生 Finding，不选阈值。
- insufficient_evidence 明确单次实验无法证明时间稳定性、样本外表现和因果解释。

任务同时限定 FindingType 白名单，又要求 strong_separation_with_coverage_gap。为保持有限集合，该组合采用 strong_separation Finding 下的固定 `pattern` 字段，而不新增任意 FindingType。

## 9. Cross-Experiment Comparison

ComparisonCompatibilityGuard 检查相同 dataset_snapshot_id、label_definition_id、observation_time、label_window，以及完整 evaluation policy（含版本、bins、epsilon 等）和合成来源标记。
比只比较版本号更严格，避免同名 policy 不同参数混用。

不兼容返回 409 / experiments_not_comparable，在调用 LLM 和创建 ReflectionRun 前拒绝。
MetricComparison 只列出各实验的覆盖率、KS、IV，没有 winner 字段。每对实验产生 cross_metric_difference，列明其可核验统计值。

## 10. LLM Reflection Boundary

业务代码只依赖 LLMClient。Reflection 使用独立实例，可注入 reflection_llm；默认使用现有 AIRI_LLM_* 配置的真实适配器。不会自动选择 Mock。

LLM 仅输出未验证解释、假设、研究提案和证据缺口。确定性 summary 和 diagnostics 与 llm_summary 分开；llm_summary 仍属于待人工审阅的模型解释，不能当作新实验事实。
LLMOutput 根本没有 KS/IV/coverage 等字段；任何未知数值字段都被严格 Schema 拒绝。

提供者不可用时，状态为 diagnostics_only / llm_reflection_status=unavailable，保留诊断、缺口和来源，hypotheses/proposals 为空。
非法 JSON、Schema 或 Proposal 返回 rejected，并丢弃整份模型内容，不保存部分通过的建议，不保留原始响应。

## 11. Reflection Prompt

[prompts.py](../../src/airi/reflection/prompts.py) 独立保存 `reflection_prompt@1.0.0`，不复用 Requirement Parser Prompt。
约束聚合证据为数据而非指令、合成来源不可外推、假设未验证、不得生成 SQL/IR/部署操作、只引用已有阈值、建议数量上限及 action 白名单。

为避免模型在 prose 中伪造统计量，v1 采用保守契约：叙述字段不能包含数字，数值窗口只能放在结构化参数中，阈值只能使用引用。此限制可能拒绝有用文字；不会降级绕过。

## 12. RefinementHypothesis

严格字段：hypothesis_id、statement、evidence_refs、confidence、status=unverified。
验证引用存在、hypothesis_id 唯一，并要求 statement 使用 may/might/could/hypothesis 或“可能/或许/待验证”等不确定表达。
不允许 proven、confirmed、已证实等确定化表述。confidence 是模型主观标签，不是统计置信度。

## 13. RefinementProposal

固定十类：window_review、aggregation_review、coverage_investigation、null_handling_review、deduplication_review、source_field_review、business_rule_review、threshold_review、additional_dataset_validation、additional_time_snapshot_validation。

每条含 target、固定 action、严格 parameters、reason、validation_question、evidence_refs、priority、requires_human_review=true。
窗口提案可提出 60/90 天等研究参数，但不会创建正式 Metric IR。
AVG/COUNT DISTINCT 属于允许研究的聚合候选，不代表当前 SQL Tool 已支持它们。

## 14. Proposal Validation

RefinementProposalValidator 检查：

- 类型与 action 对应、目标指标真实存在；未知操作/字段拒绝。
- evidence_refs 全部可解析，至少包含属于目标指标的 Finding。
- 阈值只接受目标实验内真实 candidate refs，没有数值 threshold 或新 operator 参数。
- 窗口为严格整数 1–365 天，非窗口提案不能携带窗口参数。
- 聚合仅接受 sum/count/avg/count_distinct 研究枚举；其余提案参数必须符合本类型。
- SQL/code、生产部署措辞、确定性稳定结论、prose 数字拒绝。
- 提案数不超过 policy.max_proposals。

priority 由 Python 根据引用 Finding 的 severity 重写：有 warning 为 high，否则 medium；不是业务优先级。模型不能通过自己填写 high 获得生产授权。
自然语言规则是保守启发式，不是完整语义证明；人工审核仍是必要边界。

## 15. Missing Evidence

确定性加入：没有独立 OOT/跨期稳定性证据，红冲和重复发票规则仍需人工确认。
合成实验另加：不能证明真实金融业务效果，真实 Spark 结果与金融标签未由本组证据验证。
LLM 可补充缺口，不能删除这些固定提醒。
报告 synthetic_data 依据 mock 模式或已记录 fixture source_mapping 设置，真实连接本身不等于真实业务数据。

## 16. invoice_amount_30d Reflection

实际实验 coverage=0.9202127659574468，KS=0.576750700280112，IV=6.031774836511457。
生成 6 个 Finding，包含 coverage_gap、strong_separation、small_sample、high_iv_sensitivity、insufficient_evidence，并依据实际候选产生 threshold_tradeoff。
显式 Mock 给出一条追加时间快照验证提案和未验证缺失假设；不是模型真实业务推理。

完整报告：[金额 Reflection](../../examples/phase35/invoice_amount_30d_reflection.json)。

## 17. invoice_count_30d Reflection

coverage=0.9202127659574468，KS=0.07563025210084033，IV=0.04532277654214983。
生成 5 个 Finding，weak_separation 来自 policy 而非硬编码指标名称。覆盖缺口、小样本、阈值取舍和证据不足均按实际数据识别。
Mock 给出追加时间快照验证建议；不会声称该指标没有业务价值。

完整报告：[次数 Reflection](../../examples/phase35/invoice_count_30d_reflection.json)。

## 18. invoice_amount_growth_30d Reflection

coverage=0.8138297872340425，KS=0.44606038291605304，IV=5.0975912642223005。
生成 7 个 Finding：coverage_gap、high_null_rate、strong_separation、small_sample、high_iv_sensitivity、threshold_tradeoff、insufficient_evidence。
strong_separation 包含 pattern=strong_separation_with_coverage_gap，避免只看 KS 而忽视覆盖率。

完整报告：[增长率 Reflection](../../examples/phase35/invoice_amount_growth_30d_reflection.json)。

## 19. Comparison Reflection

在共享 dataset/label/observation/window/policy 的新合成实验上运行，comparable=true；21 个 Finding，其中 3 个为两两 cross_metric_difference，3 条 Mock 研究提案。
金额 KS 大于次数；增长率覆盖率低于金额和次数。这是同一合成条件下的统计差异，不是显著性检验、业务排行榜或真实效果证明。

完整报告：[比较 Reflection](../../examples/phase35/comparison_reflection.json)。原 Phase 3 文件中的不同快照/标签 ID 没有改写。

## 20. ReflectionReport Example

返回包含 run、experiment_run_ids、evidence、diagnostics、comparison、references、确定性 summary、llm_summary、LLM 状态/来源、hypotheses、refinement_proposals、missing_evidence、provenance、warnings 和 requires_human_review=true。

```powershell
uv run --frozen python examples/demo_phase35.py
```

命令使用 `.demo/phase35_demo.db`，迁移到 head，通过 API 运行三个完整 Phase 3 链路，然后运行三个单指标 Reflection 和一个 comparison。
在 `examples/phase35/experiments/` 保存原始实验规格/报告，在父目录保存 Reflection 请求/报告。没有生产数据库连接或真实 API Key 依赖。
演示审批只表示合成 fixture 审批，不是业务审批。

## 21. Provenance / Hash

保留实验 IDs 和 report hashes、artifact IDs/versions、IR hashes、dataset/label IDs、evaluation policy versions、完整 ReflectionPolicy、Prompt 版本、model identifier、Scenario knowledge hashes、input_evidence_hash。

输入实验按 ID 排序，组装严格 ReflectionContext 后计算 canonical SHA-256。同一组证据重复调用或交换请求 ID 顺序，input hash 相同；新的运行 ID、时间或模型输出无需相同。
EvidenceExtractor 校验规格 hash、产物内容/IR hash、数据/标签元数据、Skill/Tool Plan、模板以及持久化摘要列和报告内容一致性。
这些校验不能替代数据库访问控制和不可篡改签名；数据库本身仍是可信边界。

## 22. Database Schema

新增两表，不修改历史迁移：

| 表 | 主要字段 |
|---|---|
| reflection_runs | reflection_run_id、mode、status、reflection_policy_version、prompt_version、model、input_evidence_hash、created_at、finished_at |
| reflection_reports | reflection_run_id FK/PK、report_json、summary、proposal_count、requires_human_review、created_at |

先提交 running，LLM 结束后提交 completed 或 diagnostics_only 及完整报告。门禁拒绝发生在创建运行前。
不存原始样本、完整 Prompt 或被拒绝的模型响应。报告中仅有聚合事实与受控语义上下文。
revision=`0005_experiment_reflection`，parent=`0004_experiment_evaluation`。

## 23. APIs

| 方法 | 路径 | 行为 |
|---|---|---|
| POST | /api/v1/reflections | 201，生成 ReflectionReport 或明确 diagnostics_only 报告 |
| GET | /api/v1/reflections/{reflection_run_id} | 200，读取保存的报告 |

无效请求 422、不存在资源 404、未完成实验/来源错误/不可比较 409。
沿用统一异常处理及 X-Request-ID。结构化日志仅记录 request/reflection/experiment/artifact/dataset IDs 和状态，不记录 Prompt、样本、标签或 SQL。
服务启动仍为 `uv run --frozen uvicorn airi.main:create_app --factory --reload`，启动前须 `alembic upgrade head`。

## 24. Test Results

新增 41 项测试覆盖确定性诊断、完整 policy 比较、引用/阈值/窗口白名单、SQL/部署/虚构统计拒绝、缺失与失败实验、哈希不一致、提供者未配置、非法 JSON、持久化、输入字段拒绝、聚合上下文扫描以及三个指标和 comparison E2E。
所有测试使用 Mock 或明确不可用的配置，不需要真实 API Key。

2026-09-14 实际执行结果：

| 命令 | 结果 |
|---|---|
| `uv run --frozen pytest` | 406 passed、5 skipped、2 warnings，31.19 秒 |
| `uv run --frozen pytest --cov=airi --cov-report=term-missing` | 406 passed、5 skipped、2 warnings，52.54 秒；96%，3134 statements / 126 missed |
| `uv run --frozen ruff check src tests` | All checks passed |
| `uv run --frozen ruff format --check src tests` | 115 files already formatted |
| `uv run --frozen alembic upgrade head` | 专用 SQLite 库从 0004 升到 0005，成功 |
| `uv run --frozen alembic current` | 0005_experiment_reflection (head) |
| `uv run --frozen alembic check` | No new upgrade operations detected |

新增 ReflectionDiagnostics、领域模型、API 和 ReflectionWorkflow 的行覆盖率均为 100%；Proposal Validator 为 87%，整体行覆盖率并不代表所有语义或分支已被证明。
5 项跳过为 4 项 Spark 与 1 项 MySQL 实际集成测试，默认未选择集成标记且未配置真实环境。
两个 warning 为 Starlette/httpx 和 AnyIO 第三方弃用提示。没有将集成 SKIPPED 写成 passed。

## 25. Known Limitations

- 真实 LLM、Spark/Hive/MySQL 未验证；四份演示均为 Mock Reflection + 合成实验。
- 仅支持最多三个 completed 实验及现有 invoice_risk 语义。没有大规模研究平台、后台队列或自动重试。
- Policy advisory 不是经业务验证的标准；高 IV 敏感性是警告，不是泄露/过拟合的因果证据。
- 自然语言禁止词和不确定表达检查无法证明所有句子的正确性，可能误拒绝，也可能无法识别复杂改写；LLM 文本始终为未验证解释，不能覆盖数值证据或触发执行。
- v1 不允许 narrative 数字，因此含指标代码或日期的自然语言也可能被拒绝；请用 target/parameters/evidence_refs 表达。
- evidence refs 确认存在与目标关联，不能自动证明一个业务假设与证据之间有充分因果支持。
- 当前数据库内容为信任边界，hash 检查不等于防恶意 DBA 篡改；审批身份验证、服务鉴权仍沿用既有内部开发环境限制。
- 进程退出可能留下 running。实验本身的样本上限、非原子快照、小样本和分箱 KS 限制继续适用。
- 没有新报告审批流程；人工 Accept for investigation / Reject / Need more evidence 在系统外进行。
- 没有修改 IR/SQL、应用阈值、自动执行提案、Reflection Loop、LangGraph、多 Agent、Memory、模型训练或生产发布。

## 26. Recommended Next Step

仅推荐 **AIRI Phase 4 — Controlled Metric Refinement Loop**，以人工确认的提案作为受控改进输入，并明确重新生成、审批和验证边界。本次没有开始开发 Phase 4。
