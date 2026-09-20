# AIRI Phase 5 Implementation Report

2026-09-15。Temporal Validation & Candidate Promotion Governance：已接受研究候选进入跨时间验证。固定参考分布计算 PSI，冻结阈值跨 Slice 迁移，OOT 独立评估，全部诊断为确定性 Python（0 次 LLM 调用），最终晋升资格仍由人工评审决定。

本阶段仅验证 `invoice_amount_30d` 的 60 天 window_review 候选。演示为 Mock／合成数据；真实 LLM、Spark/Hive/MySQL 尚未验收。没有生产发布，`approved_for_versioning` 不等于上线。

## 1. Repository Assessment

基于实际 temporal 模块、工作流、迁移、测试与 API 代码检查（工作目录无 Git 仓库，结论来自文件时间戳与源码逐行审阅，非提交历史）：

1. Codex 已交付 `src/airi/temporal/`（models/statistics/diagnostics/service/persistence）、`src/airi/api/temporal.py`、`src/airi/workflows/temporal_validation/workflow.py`、迁移 `0007` 与 `tests/test_temporal.py`。
2. 到场时全量 `pytest` 为 491 passed / 5 skipped，Phase 5 主体功能可运行，但演示从未完整跑通（`.demo/phase5-demo.log` 不存在）。
3. `workflow.py` 在报告组装处对 JSON-mode dict 直接 `model_validate`，`StrictSchema(strict=True)` 触发 Pydantic serializer warnings（6 条）。
4. `fixtures.py` 只有 `stable` / 方向翻转两种形态，没有 OOT 退化（`need_more_evidence`）分支。
5. `tests/test_temporal.py` 缺少入口负向门禁测试（`keep_baseline` / `need_more_evidence` / `reject_candidate` 应 409）。
6. `README.md` "下一步" 仍指向 Phase 5，环境验收 head 仍写 `0006`。
7. `phase5-report.md` 缺失。
8. Phase 3/4 复用链完好：`BaselineCandidateComparison`、`TestingWorkflow`、`ExperimentService`、`EvidenceExtractor`、`ApprovalService` 均未破坏；迁移 0001–0006 未修改。

## 2. Codex → WorkBuddy 交接

Codex 已完成（保留，未重写）：

- 领域模型与校验（`temporal/models.py`，232 行）
- PSI / 冻结阈值 / 聚合统计（`temporal/statistics.py`，141 行）
- 确定性诊断（`temporal/diagnostics.py`，94 行）
- `TemporalService` 全套门禁与持久化（`temporal/service.py` 454 行 + `persistence.py` 70 行）
- API 路由（`api/temporal.py`，58 行）与 `main.py` 注册
- 时间验证工作流（`workflows/temporal_validation/workflow.py`）
- 迁移 `0007_temporal_validation_promotion`（106 行）
- `tests/test_temporal.py`（382 行）与 `examples/demo_phase5.py`（93 行）

WorkBuddy 补完（最小侵入修复 + 补缺，不动 Codex 架构）：

- 修复 `workflow.py` serializer warnings：改用 `decode()` JSON round-trip（代码库既有惯例），269 → 272 行。
- `fixtures.py` 增加 `mode` 参数（`stable` / `direction_flip` / `oot_degradation`），保留 legacy `stable` 选择器，33 → 45 行。
- `tests/test_temporal.py` 新增 4 项测试：3 参数化入口负向门禁 + OOT 退化 `need_more_evidence` 全链，382 → 427 行。
- `examples/demo_phase5.py` 扩展为三分支演示（stable / unstable / oot-degradation），93 → 196 行，并落盘全部产物。
- 实际跑通三模式演示（exit 0，0 条 serializer warning），生成 `.demo/phase5-demo.log`。
- `README.md` 增补 Phase 5 章节，"下一步"改为 Phase 6，环境 head 改为 `0007`。
- 本文 `phase5-report.md`。
- 迁移链、质量门禁全量复验（见 §30）。

测试从到场 491 passed 增至 495 passed（+4），5 skipped 不变；无任何 Codex 已完成功能被移除或重写。

## 3. Architecture Changes

新增 temporal 领域模块：序列注册（dataset series）→ 时间验证计划（per-anchor SQL 草稿）→ 逐 Slice 人工 SQL 审批 → 运行（每 Slice 重建 baseline/candidate 实验）→ 确定性诊断 → 晋升资格 → 人工 Promotion Review。

复用 Phase 1–4 既有设施：development_artifacts 草稿、ApprovalService 逐 artifact 审批、TestingWorkflow、ExperimentService（含 `sample_observer` 钩子）、BaselineCandidateComparison、EvidenceExtractor、环境验证与 hash 体系。不新建第二套 SQL 审批。

## 4. Added / Modified Files

新增（Codex）：

- `src/airi/temporal/{__init__,models,statistics,diagnostics,service,persistence,fixtures}.py`
- `src/airi/api/temporal.py`
- `src/airi/workflows/temporal_validation/{__init__,workflow}.py`
- `migrations/versions/0007_temporal_validation_promotion_temporal_validation_promotion.py`
- `tests/test_temporal.py`
- `examples/demo_phase5.py`
- `examples/phase5/` 演示产物（stable / unstable / oot-degradation 三目录）

修改（WorkBuddy，均为最小兼容性修复）：

- `src/airi/workflows/temporal_validation/workflow.py`：`decode()` 修复 + 注释更新。
- `src/airi/temporal/fixtures.py`：`mode` 参数。
- `tests/test_temporal.py`：辅助函数抽取 + 4 项新测试。
- `examples/demo_phase5.py`：三分支。
- `README.md`：Phase 5 章节、head 0007、下一步 Phase 6。
- `phase5-report.md`（本文）。

`src/airi/main.py` 的 temporal 路由注册为 Codex 原有。迁移 0001–0006 未修改，0007 修复而非重建。

## 5. Temporal Validation 链路

```text
POST /refinements/{id}/decision   → accept_candidate_for_further_validation（人工）
POST /temporal-series             → 注册 3+ historical + 1 OOT 切片序列
POST /temporal-validations        → 谱系校验 + 每 Slice×2 生成 SQL 草稿（pending_sql_review）
   人工逐条批准每条 anchor-specific SQL
POST /temporal-validations/{id}/run → 每 Slice：Testing → Experiment(baseline/candidate)
                                   → PSI / 冻结阈值 / 比较 → 确定性诊断
GET  /temporal-validations/{id}    → passed / passed_with_warnings / failed / inconclusive
                                   + promotion_eligibility
POST /promotion-reviews            → 仅 eligible_for_review 可建（证据包 + hash）
POST /promotion-reviews/{id}/decision → 人工：approved_for_versioning / rejected /
                                        need_more_evidence
```

## 6. TemporalDatasetSeries 与 TimeSlice

`TimeSlice(Times)`：`time_slice_id`、`name`、`dataset_snapshot_id`、`observation_time`、`label_window`、`role ∈ {historical, oot}`。构造时经 `ExecutionContext(anchor_time=...)` 校验时区感知。

`TemporalSeriesInput` 强约束：4–24 个切片；≥3 historical 且恰好 1 OOT；`time_slice_id` / `dataset_snapshot_id` / `observation_time` 三者唯一；参考切片必须是 historical 且为最早；OOT 必须晚于全部 historical。

`register_series` 逐切片核对 `DatasetSnapshot.snapshot_time == observation_time`、`entity_key` 与 LabelDefinition 一致，并固化 `snapshot_hashes` 与 `label_hash`。

## 7. 时间语义与泄漏防护

- 每个 Slice 校验 `observation_time < label_window.start`，否则 `temporal_validation_invalid: label leakage`。
- `role` 只有 `historical` / `oot`，类型层面禁止 `latest` / `current` / `today`。
- 谱系门禁要求 OOT 快照不在 reflection 证据与候选实验已用快照之列，且 `oot.observation_time > spec.observation_time`（晚于候选选择观察点），否则拒绝。
- demo 锚点间隔 100 天（2026-10-01 / 2027-01-09 / 2027-04-19 / 2027-07-28），观察窗 10/45 天回看不重叠。

## 8. 入口门禁

只有 `accept_candidate_for_further_validation` 的最终人工决策能进入时间验证：refinement 报告与 DB 行 `status == completed`、`final_decision_recorded`、决策值匹配，否则 409 `candidate_not_authorized_for_temporal_validation`。`keep_baseline` / `need_more_evidence` / `reject_candidate` 一律拒绝（有参数化测试覆盖）。

同时校验 refinement 输入 hash、候选/baseline artifact 与实验证据 hash 一致，防调包。

## 9. StabilityPolicy@1.0.0

版本化常量策略（`Literal["1.0.0"]`，不可由请求覆盖）：`psi_review_above=0.2`、`ks_material_drop=0.05`、`coverage_material_drop=0.05`、`iv_material_change=1.0`、阈值 precision/recall/lift 退化容忍 0.1/0.1/0.5、`direction_flip_allowed=False`、`oot_required=True`、`min_historical_slices=3`、`psi_epsilon=1e-6`、`advisory_only=True`。

## 10. PromotionPolicy@1.0.0

`min_not_materially_worse_fraction=0.75`（baseline/candidate 比较中"未实质变差"占比下限）、`synthetic_review_only=True`、`research_governance_only=True`。同样版本化字面量，不可注入。

## 11. Spec 冻结与谱系

`create` 时固化 `TemporalValidationSpec`：baseline/candidate artifact ID、historical/OOT/reference slice ID、评估策略（继承原实验）、双策略、逐 Slice artifact 清单、`input_hash` 与 provenance（refinement_report_hash、series_hash、双 artifact hash、proposal_id、semantic_diff、snapshot_hashes、label_hash、environment_validation_run_id、environment_hash、execution_mode、双 experiment_report_hash）。

`input_hash = canonical_hash(provenance)`；一个 refinement run 只允许冻结一次计划（查询 + unique 约束双保险，重复 → `temporal_plan_already_frozen`）。运行时逐项复验，任一漂移 → `temporal_lineage_changed` / `temporal_artifact_changed`。

## 12. 逐 Anchor SQL 审批

`create` 对每个 Slice × {baseline, candidate} 调 `run_structured` 生成 anchor 专属 SQL 草稿（`ExecutionContext(anchor_time=slice.observation_time)`），`ApprovalService.save_draft` 落盘，不自动批准。报告初始 `status="pending_sql_review"`，warnings 明示"每个 anchor-specific SQL 草稿需要单独人工 SQL 批准"。

`run` 逐草稿核对：metric_ir_hash、artifact_hash、anchor_time 三者一致，且存在 `decision=="approved"` 的 ApprovalRecord，否则 `temporal_sql_not_approved`；原实验 SQL 审批在谱系门禁中复核。这是 8 条独立人工批准（4 Slice × 2），没有任何"批量放行"。

## 13. PSI 实现

- 参考分布：reference slice（最早 historical）上按 `EvaluationPolicy.bins` 分位切桶，`fixed_boundaries` 返回去重排序边界，全程固定，绝不随评估切片重算。
- NULL 独立桶：`bucket_counts` 把 `None` 计入最后一桶，`PSIBin.is_null` 标注，NULL 永远参与。
- epsilon 平滑：比例加 `eps` 再归一化 `(p+eps)/(1+eps*n)`，`Decimal` 50 位精度计算 `(a-e)*ln(a/e)`，`psi()` 校验边界有序唯一、`0<eps<=0.01`。
- 空分布返回 `status="not_available"` + warning，不臆造数值。

## 14. Frozen Threshold Transferability

reference slice 上 `ks_best_split / p90 / p95` 来源的阈值候选（阈值数值 + 操作符固定）在每个后续 slice 复算命中率/precision/recall/lift，产出 `ThresholdTemporalResult(performance, deltas)`。NULL 永不命中。阈值绝不重优化——OOT 只回答"参考阈值还成立吗"，不回答"OOT 上最优阈值是什么"。

## 15. 稳定性聚合

`summary()`：coverage/ks/iv 的 min/max/range/mean/std + 可用切片计数。`direction_consistency()`：以 reference 方向为期望，统计 consistent/inconsistent/undetermined 切片数，状态 `consistent` / `unstable` / `inconclusive`。

## 16. TemporalDiagnostics（确定性，0 次 LLM）

对每个 slice 逐项产出 Finding：`coverage_drift`、`ks_degradation`、`iv_instability`、`risk_direction_flip`、`population_shift`（PSI>0.2）、`threshold_degradation`、`oot_degradation`（OOT 比较 worse）、`insufficient_temporal_evidence`（样本不足/方向未定/PSI 不可用/无冻结阈值/阈值 delta 缺失）。全部为纯 Python 阈值比较，无任何模型调用。

## 17. 状态与 Eligibility

- `risk_direction_flip` → `not_eligible`（唯一硬否决）。
- 其余 Finding（`iv_instability` 除外）或 not-worse 占比 < 0.75 → `need_more_evidence`。
- 否则 `eligible_for_review`。资格只有这三态，永不 `production_ready`。
- 报告状态：含 `insufficient_temporal_evidence` → `inconclusive`；资格非 eligible → `failed`；有其他 Finding → `passed_with_warnings`；干净 → `passed`。证据不足不允许 passed。

## 18. TemporalValidationReport

字段：spec、status、historical 切片结果列表、oot 结果、stability 聚合、threshold_transferability、diagnostics、improvement_pattern（improved/worse/mixed/inconclusive 计数）、promotion_eligibility、synthetic_data、warnings、missing_evidence、failure_category、`requires_human_review=True`（Literal 强制）。

## 19. Promotion Evidence Package

`create_review` 仅当 `promotion_eligibility == eligible_for_review` 且 status ∈ {passed, passed_with_warnings} 才允许（否则 409 `promotion_not_eligible`）。证据包 = refinement 报告 + 关联证据（原实验/候选实验报告、候选审批、候选测试报告、proposal）+ 完整时间验证报告 + source_hashes + missing_evidence，`research_governance_only=True`、`production_deployed=False`（Literal）。创建前重验 lineage hash 与全部实验证据 hash。

## 20. Promotion Review 与人工决策

一个时间验证只允许一个 review（unique 约束）。`decide` 接受 `approved_for_versioning` / `rejected` / `need_more_evidence`，必须给出 reviewer 与 comment；决策前再次校验当前报告 hash、全部实验证据 hash 与 refinement hash。CAS 更新（`decision == "pending"` 守卫）保证一次性，重复决策 → `promotion_already_decided`。

## 21. 治理边界

- `PromotionReview` 是独立于 `ApprovalService` 的第二道人工关口：SQL 审批管"能不能跑"，Promotion Review 管"能不能进入版本化"。
- `approved_for_versioning` 只解锁 Phase 6 的版本注册，不触发任何生产部署；`production_deployed=False` 由类型系统强制。
- `advisory_only` / `research_governance_only` / `synthetic_review_only` 均为 `Literal=True`，请求无法关闭。
- API 拒绝 SQL/IR/threshold/override 注入：`TemporalValidationRequest` 只有 `source_refinement_run_id` + `series_id` 两个字段，`extra="forbid"`。

## 22. OOT 防调参

- 阈值冻结自 reference，全链不重优化。
- PSI 边界冻结自 reference。
- OOT 切片不参与任何参考分布/阈值/策略计算，只被评估。
- 谱系门禁确保 OOT 快照未被候选选择过程使用过。

## 23. 环境一致性门禁

`environment_hash` 覆盖 effective_execution_mode + 全部 Spark 配置字段 + 环境验证报告 JSON。`run` 开始时与 spec provenance 比对，`create` 与运行内每个实验的环境键（environment_validation_run_id / environment_report_hash / source_mapping）逐 slice 交叉比对，不一致 → `temporal_environment_mismatch`。spark_test 模式下先过 `ExperimentService.gates` 环境报告检查再执行。

## 24. 防篡改 Hash 链

- 序列：`content_hash = canonical_hash(series)`，读取时复验；label_hash 与逐快照 hash 复验。
- 计划：spec_hash + spec_json 双落盘，读取时 hash 与 JSON 双比对。
- 结果：report_hash；读取时 hash、spec hash、spec JSON、status 四重比对，任一不符 → `temporal_evidence_changed`。
- Review：review_hash + evidence_hash，读取与决策时复验。
- 重复运行同一 validation → `temporal_already_run`（CAS on `pending_sql_review`）。

## 25. Database Schema（0007_temporal_validation）

四张表：`temporal_dataset_series`（series_id、name、reference_slice_id、oot_slice_id、series_json、content_hash）、`temporal_validation_runs`（temporal_validation_run_id、source_refinement_run_id（unique）、baseline/candidate_artifact_id、series_id、status、策略版本、spec_hash/spec_json、started_at/finished_at）、`temporal_validation_results`（run_id、status、promotion_eligibility、report_json、report_hash）、`promotion_reviews`（promotion_review_id、temporal_validation_run_id（unique）、candidate_artifact_id、decision、reviewer、comment、reviewed_at、review_json、review_hash）。迁移 0001–0006 未动，down_revision = `0006_controlled_refinement`。

## 26. APIs

```text
POST /api/v1/temporal-series                          201 TemporalDatasetSeries
POST /api/v1/temporal-validations                     201 报告（pending_sql_review）
POST /api/v1/temporal-validations/{id}/run            200 运行后报告
GET  /api/v1/temporal-validations/{id}                200 报告（hash 复验）
POST /api/v1/promotion-reviews                        201 PromotionReview
GET  /api/v1/promotion-reviews/{id}                   200 PromotionReview
POST /api/v1/promotion-reviews/{id}/decision          200 已决策 PromotionReview
```

业务错误为 `TemporalError(RefinementError)` → 409，错误码见上文各门禁。

## 27. Demo：stable 分支

4 切片（3 historical + 1 OOT）全部 improved：coverage 1.0→1.0，KS 0.08→1.0（30 天基线无分离、60 天候选完美分离），PSI 0.0，方向 `higher_is_riskier` 一致，IV 6.03。结果 `passed_with_warnings`（合成数据警告）/ `eligible_for_review`；创建 PromotionReview → pending → 人工 `approved_for_versioning`。产物 `examples/phase5/stable/`（含 plan / refinement / series / temporal_report / promotion_pending / promotion_decision）。

## 28. Demo：direction_flip（unstable）分支

仅 OOT 切片改变：好坏样本的金额模式互换，KS 1.0→0.11 且方向翻转为 `lower_is_riskier`，PSI 5.203。诊断 `population_shift` + `risk_direction_flip` + `threshold_degradation`；`failed` / `not_eligible`（方向翻转是唯一硬否决）。PromotionReview → 409 `promotion_not_eligible`。

## 29. Demo：oot_degradation 分支

仅 OOT 切片改变：分离信号移入 30 天基线、无关旧发票稀释 60 天候选——KS 1.0→0.9（worse），PSI 0.342，方向保持 `higher_is_riskier`（无翻转）。improvement_pattern {improved: 3, worse: 1}；诊断 `iv_instability` + `ks_degradation` + `oot_degradation` + `population_shift`；`failed` / `need_more_evidence`（无翻转但证据不足以晋升）。PromotionReview → 409。

三分支完整运行 exit 0、0 条 Pydantic serializer warning；运行端点单次 ~3.5s（8 个实验 + 8 次测试）。

## 30. Test Results

测试覆盖：PSI 手算/恒等/零桶/空与非法输入、方向一致性矩阵、冻结阈值空命中、严格请求拒绝（SQL/IR/threshold/override/未知字段/坏时区/泄漏/重复切片/引用错位）、入口负向门禁（3 决策 × 409）、stable 与 reject 两条完整 E2E、OOT 退化 need_more_evidence 全链、诊断策略矩阵（case→finding→eligibility）、API override 全拒、非法序列 5 形态、篡改与失败路径（evidence_changed / already_run / already_decided / not_eligible）。

2026-09-15 实际执行：

| 命令 | 结果 |
|---|---|
| `uv run --frozen pytest` | 495 passed、5 skipped、2 warnings |
| `uv run --frozen coverage run -m pytest`（`COVERAGE_FILE=.demo/.coverage_phase5_solo`）+ `coverage report` | 495 passed、5 skipped；src/airi 96%，4297 statements / 174 missed |
| `uv run --frozen ruff check src tests` | All checks passed |
| `uv run --frozen ruff format --check src tests` | 136 files already formatted |
| `uv run --frozen alembic upgrade head`（`AIRI_DATABASE_URL=sqlite:///.demo/phase5_freshchain.db`） | 0001→0007 全链成功 |
| `uv run --frozen alembic current` | 0007_temporal_validation (head) |
| `uv run --frozen alembic check` | No new upgrade operations detected |
| `uv run --frozen python examples/demo_phase5.py` | exit 0，三分支，0 serializer warning |

到场基线 491 passed 保留并 +4；4 项 Spark、1 项 MySQL 真实集成未选择/未配置而跳过，不能视为通过。2 个 warning 为 Starlette/httpx 与 AnyIO 第三方弃用提示，与 Phase 4 基线相同。

覆盖率说明：本机 sandbox 安全删除策略拦截 pytest-cov 结束时的并行数据合并（删除子进程 `.coverage.*.pid.*.c` 文件触发 fail-closed，INTERNALERROR 发生在 495 项测试全部通过之后），故改用 `coverage run -m pytest` 主进程测量，数值为保守下界（不含子进程覆盖）。

## 31. Known Limitations

- 无实验复用：每个 anchor 独立新建 baseline/candidate 实验，同一 artifact 不跨 run 缓存；重复验证成本线性增长（原需求"experiment reuse"未实现，作为已知限制）。
- 真实 LLM、Spark/Hive/MySQL 仍未验收；Mock 执行与合成样本不可外推真实金融效果。
- 同步单次运行；无任务调度、取消、重试。运行中断可能留下 `running` 状态行，需运维识别。
- OOT 防调参依赖"阈值/边界冻结 + 谱系排除"，但若研究者以 OOT 结果反复新建 refinement 计划，流程层面无法完全阻止间接窥探——治理上要求 OOT 只用一次。
- reviewer 未认证（字符串字段）；数据库是可信边界。
- 4–24 切片上限内 <12 切片会补 `no seasonality coverage` 缺失证据项，但无跨年季节性建模。
- 演示中 stable 分支人工决策为演示脚本模拟，真实流程要求认证 reviewer。

## 32. Recommended Next Step

AIRI Phase 6 — Metric Versioning, Registry & Controlled Release：`approved_for_versioning` 之后，建立候选版本对象（metric_version）、只读 Registry（版本查询/比对/回滚指针）、受控发布流程（shadow 评估、发布审批、生产 promotion 的第二道人工关口）。Phase 5 的 `production_deployed=False` 边界正好是 Phase 6 的入口。
