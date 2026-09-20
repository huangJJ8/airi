# AIRI Phase 6 Implementation Report

2026-09-16。Metric Versioning, Registry & Controlled Release：`approved_for_versioning` 之后，候选不再是"一份更好的 SQL"，而是一个受治理、不可变、可回滚的指标版本。本阶段的核心工作是让五件事彼此不可混淆，并让每一次状态迁移都留下证据。

```text
Artifact ≠ MetricVersion ≠ Released Version ≠ Production Deployed ≠ Active Registry Version
五者中只有最后一件事可能对应一个 Spark Job，而本阶段只做逻辑激活，不做生产部署。
```

本阶段演示为 Mock 执行 + 合成数据。真实 LLM、Spark/Hive/MySQL 仍未验收；`active` 只表示内置注册表指针变化，`production_deployed` 在所有记录中恒为 `false`。

## 1. Repository Assessment

基于实际 registry 模块、迁移、测试与 API 代码的逐行审阅（工作目录无 Git 仓库，结论来自文件时间戳与源码，非提交历史）：

1. `src/airi/registry/`（models / versioning / shadow / release / persistence / service）、`src/airi/api/registry.py`、迁移 `0008_metric_registry_release`、`tests/test_registry.py`、`examples/demo_phase6.py` 均已存在。
2. 到场时 `tests/test_registry.py` **从未成功运行过**：`ImportError: cannot import name 'Aggregation' from 'airi.evaluation.models'`（应为 `airi.metric_ir.models`）。
3. 修正导入后首轮执行：`22 failed, 22 passed`。失败并非测试噪音，逐条归因后有 **5 个真实实现缺陷**（见 §2）。
4. Phase 5 基线完整：`495 passed, 5 skipped`。迁移链 `0001`–`0007` 未被修改，`0008` 为新增。
5. 迁移 `migrations/env.py` 已导入 `airi.registry.persistence`，`alembic check` 无漂移；`main.py` 已注册 `registry_router`。
6. `README.md` "下一步" 仍指向 Phase 6，环境 head 仍写 `0007`。
7. `phase6-report.md` 缺失，`examples/phase6/` 无产物。
8. 复用链完好：`ApprovalService`、`ExecutionService`、`EvidenceExtractor`、`TemporalService`、`RefinementService`、`canonical_hash`、`decode`、`StrictSchema` 均被 Phase 6 复用而非复制。

## 2. 交接与续作状态

Phase 6 模块此前已完成脚手架，但只通过一份临时烟测脚本验证过主链路，**从未对正式测试套件执行**。本轮把它跑到底，得到的是"能跑通"与"能被证明"之间的差距，共 5 个真实缺陷：

| # | 位置 | 缺陷 | 影响 | 修复 |
| --- | --- | --- | --- | --- |
| 1 | `registry/models.py` `versioning.py` | 复用 Phase 4 的 `ChangedField`（`before/after: str\|int\|None`）承载版本差异，但 `dimensions` / `filters` 是列表 | `semantic_diff` 抛 `ValidationError`，minor/major 判定链路直接不可用 | 新增 registry 局部 `VersionChangedField`（`str\|int\|float\|bool\|list[str]\|None`），Phase 4 模型保持零改动 |
| 2 | `registry/service.py` `create_review` | 先读 validation report、后判 release 状态 | 未验证的 release 请求 review 返回 404（资源不存在），语义应为 409（状态冲突） | 状态判定前置，证据校验后置 |
| 3 | `registry/release.py` `assess` | `status` 由 findings 集合推导，与 `eligibility` 可产生矛盾组合 | 同一份报告可同时输出 `release_eligibility=need_more_evidence` 与 `status=passed_with_warnings` | `status` 改为由 eligibility 推导，二者恒一致 |
| 4 | `registry/service.py` `decide_rollback` | 用裸 `update(MetricReleaseRow).values(status="rolled_back")` 只改列、不改 `release_json` | 回滚后读取该 release 返回 `metric_registry_integrity_error` 409——列与文档静默漂移 | 改走 `_save_release(_transition(...))`，列与文档同写 |
| 5 | `registry/models.py` `MetricRelease` | release 本身上下没有 `logical_activation_only` / `production_deployed` | "已激活"的记录无法自证不是一次生产部署 | 在 release 记录上补两个恒定字段 |

其中 #4 与 #5 属于同一类风险：**状态既要写在列上（供查询与 CAS），又要写在文档里（供读取与哈希）**。此前修过 `MetricDefinition.active_version_id` 的同类漂移，本轮才把 release 侧补齐；现在 `_verify_definition` / `_verify_version` / `release()` 三处完整性守卫会在漂移发生时立刻 409，而不是返回一份自相矛盾的数据。

测试侧同时修正 4 处断言（详见 §35），均属测试写错而非实现写错，唯一例外是 #2、#3 两处实现语义已按上述方向固化。

## 3. 五个不等式与治理边界

| 概念 | 身份 | 谁能创建 | 本阶段是否代表生产 |
| --- | --- | --- | --- |
| Artifact | 研究 SQL 产物（Phase 1.5） | 生成器 | 否 |
| MetricVersion | 不可变受治理版本 | 只能由 `approved_for_versioning` 的 PromotionReview 触发 | 否 |
| Released Version | 某个版本的一次受控发布准备 | Release Review 通过 | 否 |
| Production Deployed | 真实集群上的 Spark 作业 | 本阶段**不存在**这条路径 | 恒 `false` |
| Active Registry Version | 内置注册表指针 | 人工 Release Review + CAS 激活 | 否，仅逻辑指针 |

三条独立人工门禁不可合并：

1. **SQL Approval**（Phase 1.5 `ApprovalService`）— 这份 SQL 能不能被执行；
2. **Promotion Review**（Phase 5）— 这份多阶段验证过的候选值不值得成为正式版本；
3. **Release Review**（Phase 6）— 这个已存在的版本能不能被暴露出去。

三种角色的判据、记录与 API 完全分离，任何一条通过都不会隐含另一条通过。

## 4. Architecture Changes

```text
PromotionReview.decision == approved_for_versioning      ← 唯一入口
→ 服务器自行恢复谱系（IR / artifact / refinement / temporal / promotion）
→ VersionChangeClassifier（patch / minor / major）→ 服务器分配版本号
→ MetricVersion（不可变，content_hash 锁定）
→ MetricDefinition（家族身份 + active_version_id 指针）
→ MetricRelease（staging 环境，状态机）
→ Staging Validation（真实执行一次 candidate artifact）
→ Shadow Validation（candidate 与 incumbent 同输入并排跑）
→ ReleaseDiagnostics（多证据）→ ReleaseValidationReport
→ ReleaseReview（独立人工门禁）
→ Activation（CAS + 注册表指针切换，仅逻辑）
→ Rollback（人工 RollbackReview → 指针回退，历史保留）
```

复用 Phase 1–5 既有设施：`ApprovalService`（候选 SQL 与每个 shadow 输入的审批）、`ExecutionService`、`EvidenceExtractor`、`TemporalService.review/environment_hash`、`canonical_hash` / `decode` / `StrictSchema`。不新建第二套 SQL 审批、不新建第二套执行通道、不引入事件总线。

## 5. Added / Modified Files

新增：

- `src/airi/registry/__init__.py`（25 行）
- `src/airi/registry/models.py`（355 行）— 领域模型、状态机、ReleasePolicy、content hash
- `src/airi/registry/versioning.py`（167 行）— 语义叶子、语义差异、分类器、家族键、版本规划
- `src/airi/registry/shadow.py`（205 行）— 指标值抽取、分布摘要/差异、并排比较、findings
- `src/airi/registry/release.py`（76 行）— `ReleaseDiagnostics` 与 BLOCKING / SOFT finding 集
- `src/airi/registry/persistence.py`（114 行）— 7 张 SQLAlchemy 行
- `src/airi/registry/service.py`（1094 行）— `RegistryService` / `RegistryError` / `RegistryNotFound`
- `src/airi/api/registry.py`（149 行）— 19 条路由
- `migrations/versions/0008_metric_registry_release_metric_registry_release.py`（164 行）
- `tests/test_registry.py`（717 行，44 项）
- `examples/demo_phase6.py`（350 行）
- `examples/phase6/` 演示产物（32 个文件）
- `phase6-report.md`（本文）

修改（最小侵入）：

- `migrations/env.py`：导入 `airi.registry.persistence`，使 autogenerate 能看到新表（否则 `alembic check` 报 7 张表全部缺失）。
- `src/airi/main.py`：注册 `registry_router`。
- `src/airi/registry/{models,versioning,shadow,release,service}.py`：§2 表中 5 项修复 + `ruff format`。
- `tests/test_registry.py`：4 处断言修正 + `ruff`。
- `README.md`：Phase 6 章节、head `0008`、下一步 Phase 7。
- `examples/demo_phase6.py`：`ruff format`。

迁移 `0001`–`0007` 未作任何修改。

## 6. MetricDefinition（家族身份）

`MetricDefinition` 是业务身份，版本挂在它下面，而不是反过来：

```text
metric_definition_id / metric_key / display_name
scenario = "invoice_risk" / entity_type = "enterprise"
member_metric_names[]        # 该家族历史上出现过的指标名，只增不改
active_version_id | None     # 当前逻辑激活的版本
created_at / updated_at
```

`active_version_id` 同时存在于**行上的列**与**文档字段**中。`definition_by_id` 通过 `_verify_definition` 强制二者一致，不一致立即 `metric_registry_integrity_error`。`_apply_active_version` 是唯一的写入点，列与文档同写——这是 §2 中类风险的结构性防御。

## 7. MetricVersion（不可变身份）

```text
metric_definition_id / metric_key / version / status
metric_name / display_name
metric_ir / metric_ir_hash
artifact_id / artifact_hash
source_refinement_run_id / source_temporal_validation_run_id / source_promotion_review_id
version_change{kind, previous_version, changed_fields[]}
provenance{...}              # 完整研究链哈希包
content_hash                 # 内容哈希
synthetic_data
```

`status ∈ {registered, release_candidate, active, retired}`。语义字段一旦写入不再变化；改变语义必须产生新版本。`status` 是生命周期，不属于内容哈希。

## 8. 授权门禁：approved_for_versioning

`promotion_lineage(promotion_review_id)` 是唯一的版本创建入口，逐层校验后才允许继续：

1. `PromotionReview.decision == "approved_for_versioning"`，否则 `metric_version_not_authorized`（409）；
2. `canonical_hash(review.evidence) == review.evidence_hash`，否则 `promotion_evidence_changed`；
3. `canonical_hash(temporal_report) == evidence.source_hashes["temporal_report"]`；
4. `canonical_hash(refinement_report) == evidence.source_hashes["refinement_report"]`；
5. 从候选实验重新提取证据，比对 `artifact_id` / `artifact_hash` / `metric_ir_hash` / `canonical_hash(metric_ir)`，任一不符为 `metric_version_lineage_changed`；
6. 同一 `PromotionReview` 只能注册一次版本，重复为 `metric_version_already_registered`。

`rejected` 与 `need_more_evidence` 均返回 409 `metric_version_not_authorized`，且此时 `GET /metrics` 仍为空——被拒绝的评审不会留下任何注册表痕迹。

## 9. 服务器所有权：客户端不能提交内容

`MetricVersionRequest` 只有 `promotion_review_id` 一个字段；`ReleaseRequest` 只有 `metric_version_id` 与 `target_environment`。两者都是 `StrictSchema(extra="forbid", strict=True, frozen=True)`，客户端的越权字段直接 422：

- 版本侧：`metric_ir`、`version`、`artifact_id`、`SQL`、`metric_key` → 422；
- 发布侧：`expected_active_version`、`release_policy` → 422。

`ReleasePolicy` 本身也拒绝覆盖：`{"version": "2.0.0"}`、`{"require_shadow_validation": false}`、`{"logical_activation_only": false}` 全部 `ValidationError`。

版本号、IR、SQL、artifact 全部由服务器从既有谱系恢复，客户端只能提交"已评审的标识符"。

## 10. VersionChangeClassifier（patch / minor / major）

分类完全确定性，零 LLM 调用，字段集合硬编码在 `versioning.py`：

- `MAJOR_FIELDS`：`source.*`、`entity_key`、`entity_type`、`partition_field`、`aggregation.*`、`window*`、`filters`、`schema_version` —— 改变数字对业务的含义；
- `MINOR_FIELDS`：`dimensions` —— 增加分析上下文但不移动主体值；
- `PATCH_FIELDS`：`name`、`display_name`、`description` —— 只是标签，重命名不是语义变化。

判定顺序为 major → minor → patch。测试覆盖 8 种情形（identical / display / dimensions / window / aggregation / entity / filters / source），其中 identical 断言 `changed == []` 且 `entity_key` 落在 `unchanged` 中。

## 11. MetricFamilyKey（家族派生）

```text
invoice_amount_30d  → invoice_amount
invoice_amount_60d  → invoice_amount
invoice_amount_90d  → invoice_amount
invoice_count       → invoice_count
30d                 → 30d          # 全匹配时不做空串替换
```

规则：只有**结尾的** `_<n>d` 窗口后缀不参与身份，正则 `_\d+d$`。历史指标名从不在任何地方被改写——`member_metric_names` 只累加。这是"版本属于家族"而非"每次改窗口都换一个指标"的关键，也是 Phase 4/5 已有产物零改写的前提。

## 12. 版本号生成与不可回退

`MetricVersionPlanner.plan(ir, previous)`：

| previous | kind | 版本 |
| --- | --- | --- |
| 无 | `initial` | `1.0.0` |
| `1.0.0` + patch | `patch` | `1.0.1` |
| `1.0.0` + minor | `minor` | `1.1.0` |
| `1.0.0` + major | `major` | `2.0.0` |

`previous` 取该家族现有版本中 `version_key` 最大者；`version_key` 按 `.` 切分后逐段转 int，因此 `2.0.0 > 1.10.0` 之类的字符串比较陷阱被避开。客户端无法提交版本号，服务器也不允许"降级复用"已存在的号。

## 13. MetricVersionDiff 与精确版本读取

`GET /metrics/{key}/versions/{from}/compare/{to}` 返回 `MetricVersionDiff`（`changed_fields` / `unchanged` / `classification`）。`changed_fields` 按 `FIELD_ORDER` 稳定排序，字段名使用点号路径（`window.size`、`source.table`），便于机器消费。

精确版本读取 `GET /metrics/{key}/versions/{version}` 走 `(metric_definition_id, version)` 复合查询；不存在的版本返回 404，不做"最接近版本"的模糊回退。

## 14. content_hash 与不可变性

```python
metric_version_content_hash(version)  # sha256(sorted json)
include = {metric_definition_id, metric_key, version, metric_name, display_name,
           metric_ir, metric_ir_hash, artifact_id, artifact_hash,
           source_refinement_run_id, source_temporal_validation_run_id,
           source_promotion_review_id, version_change, provenance, synthetic_data}
```

刻意排除：`metric_version_id`（身份）、`status`（生命周期）、时间戳。因此版本在 `registered → active → retired` 的整个生命周期中内容哈希恒定，"退役"不会改变内容身份，也不会破坏任何引用它的 release。

篡改 `version_json` 里的任何语义字段（测试中改 `metric_ir.window.size` 为 120）会导致读取时 `metric_registry_integrity_error`（409）。

## 15. Shadow Validation 设计

Shadow 验证回答的问题是：**新版本与在位版本在同一份输入上并排跑，主体是否还在、NULL 是否突增、执行是否完成**。它刻意不回答"新数值是否等于旧数值"——语义改变时数值本就应当改变。

"值变化是预期证据，不是错误"这条规则体现在三处：

1. `ShadowComparator` 仅把 `duplicates`、`subject_loss_exceeds_policy`、`new_null_exceeds_policy` 判为 failed；
2. 分布位移只产生 warning `distribution_shifted_as_expected_for_semantic_change`；
3. `shadow_findings` 里没有"值变化率超限"这一项。

## 16. ShadowReference（baseline 选择）

`_shadow_reference` 的选取顺序体现治理意图：

| 条件 | role | label |
| --- | --- | --- |
| 家族已有活跃版本，且不是本次候选 | `active_metric_version` | 活跃版本号（如 `1.0.0`） |
| 家族尚无活跃版本 | `lineage_baseline_artifact` | 谱系基线指标名（如 `invoice_amount_30d`） |

即：**在位版本优先，没有在位版本时退回谱系基线**。候选侧恒为 `candidate_metric_version`。基线与候选的 artifact anchor 必须一致，否则 `shadow_anchor_mismatch`——不允许拿两个不同时间点的数据做"影子对比"。

## 17. ShadowComparator 输出

```text
sample_count / matched_entities / baseline_only / candidate_only
value_equal / value_changed / new_null / resolved_null
difference_rate          = (changed + new_null + resolved_null) / matched
entity_loss_fraction     = |baseline_keys - candidate_keys| / |baseline_keys|
new_null_fraction        = new_null / matched
distribution_delta{entities, non_null, null_rate, min, max, mean, p50, p90, p95, p90_relative_shift}
warnings[]               # duplicate_entity_in_execution / no_matched_entities
                         # subject_loss_exceeds_policy / new_null_exceeds_policy
                         # distribution_shifted_as_expected_for_semantic_change
status ∈ {passed, passed_with_warnings, failed, inconclusive}
```

`metric_values()` 把执行行转成 `{entity: Decimal | None}`，重复主体计入 `duplicates` 并保留首次出现值；无法解析为 Decimal 的值按 NULL 处理（不静默当 0）。零匹配主体 → `inconclusive`，绝不返回 `passed`。

## 18. ReleasePolicy@1.0.0

```text
version = "1.0.0"
require_temporal_validation = true      require_promotion_review = true
require_staging_validation  = true      require_shadow_validation = true
max_entity_loss_fraction    = 0.05
max_new_null_fraction       = 0.05
max_distribution_shift      = 1.0
logical_activation_only     = true      synthetic_review_only = true
production_requires_verified_environment = true
```

三个阈值是**多证据中的一个证据**，不是准入门槛的全部：超过 0.05 的主体丢失/新增 NULL 会让 release 变成 `not_eligible`，而分布位移超限只记录 warning。Policy 随 release 一起持久化在 `release_policy` 字段中，`release_policy_version` 另有独立列供查询，二者不一致即完整性错误。

## 19. Release 状态机

```text
draft → pending_staging_validation → staging_validated → pending_release_review → approved → active
                ↓                          ↓                     ↓
             failed                     failed                failed
                                                                          active → rolled_back
```

`RELEASE_TRANSITIONS` 是白名单表，`_transition` 拒绝任何不在表中的边。`failed` 与 `rolled_back` 为终态。`draft → active` 这条捷径在结构上不存在。

实际创建的 release 直接进入 `pending_staging_validation`（`draft` 是模型默认值，不是 API 可达状态）。`active → rolled_back` 只能由 `decide_rollback` 触发。

## 20. Staging Validation

`validate(release_id)` 的前置状态必须是 `pending_staging_validation`，否则 `release_not_validatable`。执行前先重跑整条证据链（§28），再用**已审批**的候选 artifact 执行一次：

```text
schema_ok   = {"entity_id", metric_name} ⊆ columns 且 columns ≤ 16
coverage    = non_null / entities
null_rate   = (entities - non_null) / entities
status      = failed       if 执行失败 or not schema_ok or truncated
              inconclusive if 结果为空
              passed       否则
truncated   = 执行结果被行数上限截断 → 直接 failed（不允许用截断数据声称覆盖率）
```

执行失败时**不产生 shadow 报告**——staging 已经 failed，再跑影子比较没有意义。

## 21. ReleaseDiagnostics 多证据

```text
BLOCKING = staging_execution_failed | staging_schema_invalid | staging_result_truncated
           shadow_execution_failed | shadow_duplicate_entity
           shadow_entity_loss | shadow_null_increase
SOFT     = staging_inconclusive | staging_empty_result
           shadow_missing | shadow_no_matched_entities

eligibility = not_eligible          if BLOCKING 命中
              need_more_evidence    if SOFT 命中
              eligible_for_release_review  否则
status      = failed | inconclusive | passed_with_warnings | passed   （由 eligibility 推导）
```

`status` 与 `eligibility` 之间的推导关系是单向强制的（§2 #3 的修复）：`not_eligible → failed`，`need_more_evidence → inconclusive`，`eligible → passed` 或 `passed_with_warnings`。这样一份报告不可能既说"通过但有告警"又说"需要更多证据"。

不存在任何单一 KS / PSI / 数值阈值决定准入的规则。

合成数据的影响被显式降级而非隐藏：追加 warning `synthetic evidence only; research release demonstration, not a real release`，并把 `real Spark not verified`、`real production traffic not verified` 写入 `missing_evidence`。

## 22. ReleaseValidationReport

一份报告同时携带：`release_id` / `metric_version_id` / `version` / `status` / `release_eligibility`、完整 `staging` 结果、完整 `shadow` 结果（或 `null`）、`evidence_checks`、`findings`、`warnings`、`missing_evidence`、`synthetic_data`、`execution_mode`、`release_policy_version`，以及三个恒定断言字段：

```text
logical_activation_only = true
production_deployed     = false
requires_human_review   = true
```

报告写入 `release_validation_results` 时存 `report_hash = canonical_hash(report)`，同时把该哈希写进 release 的 `validation_report_hash`。Release Review 在决策时要求 `release.validation_report_hash == review.validation_report_hash == canonical_hash(当前报告)`，三者任一不符即 `release_evidence_changed`。

## 23. ReleaseReview（与 PromotionReview 分离）

```text
decision ∈ {pending, approved, rejected, need_more_evidence}
previous_decisions[]      # 重开时追加历史决定
production_deployed = false
```

`POST /releases/{id}/review` 的判定顺序（§2 #2 的修复）：

1. release 状态为 `pending_release_review`：走**重开**路径，仅当上一次决定是 `need_more_evidence` 时允许；复用同一个 `release_review_id`，把上一次决定追加进 `previous_decisions`，`decision` 重置为 `pending`；
2. release 状态不是 `staging_validated`：409 `release_not_ready_for_review`（**在读取证据之前**判定，因此未验证的 release 得到的是状态冲突而非 404）；
3. 报告 `release_eligibility != eligible_for_release_review`：409 `release_not_eligible`；
4. 无既有评审则创建，靠数据库唯一约束兜底并发（`IntegrityError → release_review_already_exists`）。

`decide_review` 用 `WHERE decision = 'pending'` 的条件更新实现 CAS，`rowcount != 1` 即 `release_review_already_decided`。`approved → release.status = approved`；`rejected → failed`；`need_more_evidence` 不改状态，只允许重开。

## 24. 逻辑激活（CAS 与注册表指针）

`activate(release_id, ActivationRequest(expected_active_version))`：

1. 若 release 已 `active` 且定义指针已指向它 → 直接返回，**幂等且不追加事件**；
2. release 必须是 `approved`，否则 `release_not_approved`；
3. CAS：`UPDATE metric_definitions ... WHERE metric_definition_id = ? AND active_version_id <匹配 expected>`，`expected = None` 时条件是 `active_version_id IS NULL`。`rowcount != 1` → 409 `activation_conflict`（先 `rollback()` 再抛，不留半写状态）；
4. `_apply_active_version` 原子更新列与文档；
5. 旧版本 `active → retired`，新版本 `registered → active`；
6. release `approved → active`，写 `activated_at`；
7. 追加 `activated` 事件，metadata 记录 `previous_active_version_id` 与 `logical_activation_only: true`。

并发语义是乐观并发：客户端必须声明"我认为当前活跃的是哪个版本"，服务端不猜、不覆盖。陈旧期望得到 409，而不是静默移动指针。

## 25. logical_activation_only / production_deployed

三个层次都带这组恒定断言，任何一处被改写都会被发现：

| 记录 | 字段 |
| --- | --- |
| `ReleaseValidationReport` | `logical_activation_only=True`、`production_deployed=False` |
| `ReleaseReview` | `production_deployed=False` |
| `MetricRelease` | `logical_activation_only=True`、`production_deployed=False` |

它们是 `Literal[True]` / `Literal[False]`，不是可配置布尔值：本仓库没有任何代码路径能写出 `true`。生产环境的唯一入口 `target_environment=production` 在 `environment != "production"` 时直接 409 `production_environment_not_verified`——验收环境里这条路径**不可达**，而不是"暂未实现"。

## 26. Rollback（保留历史）

`request_rollback(metric_key, {target_version, reason})`：

- 家族无活跃版本 → `rollback_no_active_version`；
- 目标即当前活跃版本 → `rollback_target_already_active`；
- 否则创建 `RollbackReview`（`from_version_id` / `from_version` / `to_version_id` / `to_version` / `reason`，`decision=pending`），追加 `rollback_requested`。

`decide_rollback`：

- `rejected` → 追加 `rollback_rejected`，指针不动；
- `approved` → CAS `UPDATE ... WHERE active_version_id = from_version_id`，失败即 `rollback_conflict`；`_apply_active_version` 指向 `to_version_id`；`from_version_id` 版本置 `retired`、`to_version_id` 版本置 `active`；**该版本对应的 release 由 `active` 迁移到 `rolled_back`**（列与文档同写，§2 #4）；追加 `rollback_approved` 与 `rolled_back`。

历史保全的性质：

- 被替换的版本是 `retired`，不是删除；`GET /metrics/{key}/versions` 仍返回两个版本；
- 被回滚的 release 仍可读取，状态为 `rolled_back`，其 validation 报告与 review 记录全部保留；
- 回滚不是"撤销激活"而是"又一次激活"，因此同样留下完整审计链。

## 27. Registry Integrity Re-verification

读取路径上的一致性守卫（任一处不符即 409 `metric_registry_integrity_error`）：

| 读取点 | 校验 |
| --- | --- |
| `_verify_definition` | 行 `active_version_id` / `metric_key` == 文档字段 |
| `_verify_version` | `metric_version_content_hash(version) == row.content_hash`；`canonical_hash(metric_ir) == row.metric_ir_hash == version.metric_ir_hash`；`row.artifact_hash == version.artifact_hash`；`row.status == version.status` |
| `definition()` | `canonical_hash(定义文档) == row.definition_hash` |
| `version()` | `canonical_hash(version_json) == row.content_hash` 的完整重算 |
| `release()` | 行 `status` / `version` / `target_environment` / `release_policy_version` / `metric_version_id` == 文档；`release_provenance.metric_version_content_hash` == 该版本当前 `content_hash` |
| `validation_report()` | `canonical_hash(报告) == row.report_hash == release.validation_report_hash` |
| `release_review()` / `rollback_review()` | `canonical_hash(评审文档) == row.review_hash` |

`_evidence_checks(version)` 在**每次** validate 与 review 决策时重跑整条研究链：PromotionReview evidence hash → temporal report hash → refinement report hash → artifact 行与文档 → test report hash → 重新提取实验证据。整条链上任何一环被改动，release 就会被 `release_evidence_changed` 拦下，即使审批早已通过。

## 28. Audit Events

`metric_release_events` 记录 10 类事件：

```text
version_registered / release_created / staging_validated / release_approved / release_rejected
activated / rollback_requested / rollback_approved / rollback_rejected / rolled_back
```

每条事件含 `event_id`、`metric_definition_id`、`metric_version_id`、`release_id`、`event_type`、`actor`、`metadata`、`created_at`。服务端动作的 `actor` 为 `registry-service`，人工动作为请求中的 reviewer。

这是一张审计表，不是事件总线：没有投递、没有重试、没有订阅者，也不承担跨系统一致性。它只保证"注册表变过什么，事后能逐条对齐"。

## 29. 门禁错误码总表

| 错误码 | HTTP | 触发条件 |
| --- | --- | --- |
| `metric_version_not_authorized` | 409 | PromotionReview 决策不是 `approved_for_versioning` |
| `promotion_evidence_changed` | 409 | 评审证据或上游报告哈希不符 |
| `metric_version_lineage_changed` | 409 | 重新提取的 artifact / IR 与评审链不一致 |
| `metric_version_not_supported` | 409 | 候选 IR 不是 `MetricIR` |
| `metric_version_already_registered` | 409 | 同一 PromotionReview 重复注册 |
| `metric_definition_not_found` / `metric_version_not_found` | 404 | 家族 / 版本不存在 |
| `metric_registry_integrity_error` | 409 | 行与文档漂移或内容哈希不符 |
| `production_environment_not_verified` | 409 | `target_environment=production` 而环境未验收 |
| `release_not_validatable` | 409 | release 不是 `pending_staging_validation` |
| `release_not_ready_for_review` | 409 | release 不是 `staging_validated` / 不可重开 |
| `release_not_eligible` | 409 | 报告 eligibility 不是 `eligible_for_release_review` |
| `release_review_already_exists` | 409 | 既有评审已决定且非 `need_more_evidence` |
| `release_review_already_decided` | 409 | 重复决策 |
| `release_not_approved` | 409 | release 不是 `approved` |
| `activation_conflict` | 409 | CAS 期望的活跃版本与实际不符 |
| `release_state_invalid` | 409 | 状态机拒绝的迁移 |
| `release_evidence_changed` | 409 | 报告/上游证据在验证后被改动 |
| `shadow_anchor_mismatch` | 409 | 基线与候选 anchor 不一致 |
| `rollback_no_active_version` | 409 | 无活跃版本可回滚 |
| `rollback_target_already_active` | 409 | 回滚目标即当前活跃版本 |
| `rollback_conflict` | 409 | 回滚 CAS 失败 |
| `rollback_already_decided` | 409 | 重复决策 RollbackReview |

`RegistryError` 继承既有 `RefinementError`，域错误统一为 409；仅"资源不存在"使用 404（`RegistryNotFound`）。

## 30. Database Schema（0008_metric_registry_release）

`revision = "0008_metric_registry_release"`，`down_revision = "0007_temporal_validation"`，164 行，7 张表：

| 表 | 关键列 | 约束 |
| --- | --- | --- |
| `metric_definitions` | `metric_definition_id` PK、`metric_key` UNIQUE、`display_name`、`scenario`、`entity_type`、`member_metric_names`(JSON)、`active_version_id`、`definition_json`(JSON)、`created_at`、`updated_at` | `metric_key` 唯一 |
| `metric_versions` | `metric_version_id` PK、`metric_definition_id` FK、`version`、`status`、`metric_name`、`metric_ir_hash`、`artifact_id` FK、`artifact_hash`、`source_promotion_review_id` FK、`content_hash`、`version_json`(JSON)、`created_at` | UNIQUE(`metric_definition_id`,`version`)、UNIQUE(`source_promotion_review_id`) |
| `metric_releases` | `release_id` PK、`metric_definition_id` FK、`metric_version_id` FK、`version`、`target_environment`、`status`、`expected_active_version_id`、`release_policy_version`、`release_json`(JSON)、`created_at`、`validated_at`、`activated_at` | `release_id` PK |
| `release_validation_results` | `release_id` PK+FK、`status`、`release_eligibility`、`report_json`(JSON)、`report_hash`、`created_at` | `release_id` 唯一 |
| `release_reviews` | `release_review_id` PK、`release_id` FK、`metric_version_id` FK、`decision`、`reviewer`、`comment`、`review_json`(JSON)、`review_hash`、`created_at`、`reviewed_at` | UNIQUE(`release_id`) |
| `rollback_reviews` | `rollback_review_id` PK、`metric_definition_id` FK、`from_version_id`、`to_version_id`、`decision`、`reviewer`、`comment`、`review_json`(JSON)、`review_hash`、`created_at`、`reviewed_at` | `rollback_review_id` PK |
| `metric_release_events` | `event_id` PK、`metric_definition_id`、`metric_version_id`、`release_id`、`event_type`、`actor`、`metadata_json`(JSON)、`created_at` | 审计表 |

模式与既有各阶段一致：业务列复制自 JSON 文档（供查询、唯一约束与 CAS），文档字段才是读取源，读取时逐条比对。`member_metric_names` 用可变 JSON 存储，避免 MySQL 长度陷阱。

## 31. APIs

`registry_router`，前缀 `/api/v1`，共 19 条路由：

```text
GET  /metrics                                          家族列表
GET  /metrics/{metric_key}                             家族定义
GET  /metrics/{metric_key}/versions                    版本列表（按版本号排序）
GET  /metrics/{metric_key}/active                      当前活跃版本（未激活为 null）
GET  /metrics/{metric_key}/events                      审计事件（时间序）
GET  /metrics/{metric_key}/versions/{version}          精确版本
GET  /metrics/{metric_key}/versions/{from}/compare/{to} 版本差异

POST /metric-versions                                  注册版本（仅需 promotion_review_id）
POST /releases                                         创建发布（staging）
GET  /releases/{release_id}                            发布详情
POST /releases/{release_id}/validate                   staging + shadow 验证
GET  /releases/{release_id}/validation                 已存验证报告
POST /releases/{release_id}/review                     创建/重开 Release Review
GET  /release-reviews/{release_review_id}              评审详情
POST /release-reviews/{release_review_id}/decision     人工决定
POST /releases/{release_id}/activate                   CAS 逻辑激活
POST /metrics/{metric_key}/rollback                    申请回滚
GET  /rollback-reviews/{rollback_review_id}            回滚评审详情
POST /rollback-reviews/{rollback_review_id}/decision   人工回滚决定
```

所有 POST 请求体均为 `extra="forbid"` 的严格模型；`ActivationRequest` 只接受 `expected_active_version`（`Version | None`）。响应模型直接复用领域模型，不另设 DTO 层——避免"API 视图"与"治理记录"两套真相。

## 32. Demo：v1 注册与发布

`uv run --frozen python examples/demo_phase6.py`（专用 `.demo/phase6_demo.db`，脚本启动时清空该演示库以保证版本号从 `1.0.0` 开始）。

前置：Phase 1.5 生成 + 审批 + 测试 + Phase 3 实验 + Phase 4 精炼（60 天候选，`outcome` 由真实比较决定）+ Phase 5 三段历史 + 1 段 OOT 序列。

v1（60 天候选）：

```text
版本号 1.0.0   kind=initial   previous=None   changed=[]
metric_key=invoice_amount   metric_name=invoice_amount_60d
content_hash=2d794e8f8eee6721…
```

验证（`POST /releases/{id}/validate`）：

```text
report status=passed_with_warnings   eligibility=eligible_for_release_review
staging  passed  row_count=385  coverage=1.0
shadow   passed  baseline=invoice_amount_30d (lineage_baseline_artifact)
         matched=385  equal=222  changed=163  difference_rate=0.4234
         entity_loss_fraction=0.0  new_null_fraction=0.0  p90_relative_shift=0.0066
findings=[]
missing=[real Spark not verified, real production traffic not verified]
```

163/385 的数值变化正是预期证据：60 天窗口与 30 天基线本就不同语义，主体一个没丢、NULL 一个没增、分布几乎不动（p90 位移 0.66%）——这才是"通过"的含义。

随后 `review → approved → activate(expected_active_version=null)`。激活前的陈旧期望检查用 `expected_active_version="1.0.0"`（家族内存在但未激活的版本）触发 409 `activation_conflict`，证明指针为空时不会被客户端信念覆盖。

```text
activated 1.0.0   active pointer now 1.0.0
production_deployed=false  logical_activation_only=true
```

## 33. Demo：v2 注册、发布与激活

v2（90 天候选）：

```text
版本号 2.0.0   kind=major   previous=1.0.0
changed=[name: invoice_amount_60d → invoice_amount_90d,
         display_name: 近60天企业开票金额 → 近90天企业开票金额,
         description: 统计企业近60天开票金额之和 → 统计企业近90天开票金额之和,
         window.size: 60 → 90]
共享家族 invoice_amount   member_metric_names=[invoice_amount_60d, invoice_amount_90d]
classification 1.0.0 → 2.0.0: major
```

两个指标名都成为同一家族的成员（`member_metric_names` 累加，历史名不改写），`window.size` 从 60 变 90 被判定为 major——不是 minor 也不是 patch。

验证：

```text
report status=passed_with_warnings   eligibility=eligible_for_release_review
staging  passed  row_count=401  coverage=1.0
shadow   passed_with_warnings  baseline=1.0.0 (active_metric_version)   ← 在位版本优先
         matched=385  equal=201  changed=184  difference_rate=0.4779
         entity_loss_fraction=0.0  new_null_fraction=0.0
         p90_relative_shift=4.276
warnings=[distribution_shifted_as_expected_for_semantic_change]
findings=[]
```

关键在于这次 baseline 的 role 变成 `active_metric_version`（label `1.0.0`）——同一个候选，一旦家族有了在位版本，影子对比就自动从"对比谱系基线"升级为"对比线上版本"。90 天窗口相对 60 天的分布位移达 4.28 倍，被记录为 warning 而非阻断项；主体数与 NULL 率仍然健康，因此依旧是 `eligible_for_release_review`。

随后 `review → approved → activate(expected_active_version="1.0.0")`：

```text
activated 2.0.0   active pointer now 2.0.0
versions 状态 [1.0.0 → retired, 2.0.0 → active]
```

`1.0.0` 被置为 `retired`——仍在列表中，不是删除。

## 34. Demo：回滚

```text
POST /metrics/invoice_amount/rollback
  target_version=1.0.0   reason="Synthetic regression observed in the 90d version"
  → from_version=2.0.0  to_version=1.0.0
POST /rollback-reviews/{id}/decision  decision=approved
```

结果：

```text
active pointer → 1.0.0
versions       [1.0.0 → active, 2.0.0 → retired]
releases       90d release → rolled_back（列表 90 rev 仍在）
               60d release → active（原记录未被改写）
```

审计链（13 条，`GET /metrics/invoice_amount/events`）：

```text
version_registered ×2
release_created, staging_validated, release_approved, activated      ← v1
release_created, staging_validated, release_approved, activated      ← v2
rollback_requested, rollback_approved, rolled_back
actor = registry-service（服务端动作） / synthetic-demo（人工动作）
```

回滚是一次新的、有记录的激活，而不是对历史的抹除：两个版本都在，两份 release 都在，13 条事件按时间序完整。

## 35. Test Results 与 Quality Gates

`tests/test_registry.py` 共 **44 项**：

| 组 | 数量 | 内容 |
| --- | --- | --- |
| 授权门禁 | 3 | `rejected` / `need_more_evidence` 参数化 409 + 未知 review 404；被拒后 `GET /metrics` 仍为空 |
| 版本语义 | 9 | 8 种分类器情形 + 家族键派生与显示名 |
| Shadow 单元 | 7 | 值抽取/重复主体 + 6 种比较情形（identical / distribution / new_null / resolved_null / entity_loss / no_match） |
| Release 资格 | 10 | 9 种 finding 组合 + 合成数据降级上限 |
| 全链路 E2E | 1 | v1 注册 → 发布 → 激活 → v2 注册（major）→ 发布 → 激活 → 回滚；断言版本号、家族成员、diff、shadow role 切换、状态变更、13 条事件精确顺序、回滚后 release 状态 |
| 门禁与状态机 | 1 | 未验证不可 review/activate、重复 validate 409、review 重开与 `previous_decisions`、CAS `activation_conflict`、激活幂等不加事件、单版本回滚 409、production 409 |
| 篡改检测 | 2 | 篡改 `version_json` → `metric_registry_integrity_error`；篡改 temporal 报告 → `release_evidence_changed` |
| 请求形状 | 8 | 6 组越权字段 422 + 严格模型 `ValidationError` |

本轮运行中发现并修正的 4 处测试缺陷：

1. `refinement_for` 缺少 Phase 4 的最终决定（`accept_candidate_for_further_validation`），导致时序验证入口 409；
2. `shadow_result` helper 返回 dict 而非 `ShadowComparisonResult`，`ReleaseDiagnostics` 无法消费；
3. Shadow 断言把 5 个主体写成 4；
4. CAS 冲突用例使用了家族内不存在的 `2.0.0`（得到 404），改为使用存在但未激活的 `1.0.0`，才真正测到 CAS。

`missing == []` 断言改为仅在 shadow 存在时生效——shadow 缺失时 `missing_evidence` 本就应当非空。

全量质量门禁（本阶段最终状态）：

```text
uv run --frozen pytest                                   →  539 passed, 5 skipped  （Phase 5 基线 495/5，+44）
uv run --frozen coverage run -m pytest                    →  539 passed, 5 skipped
uv run --frozen coverage report                           →  src/airi 5254 语句 233 未覆盖 = 96%
                                                             registry 包 889 语句 57 未覆盖 = 94%
                                                             （models / persistence / __init__ 100%，
                                                               release 97%，shadow 93%，versioning 93%，
                                                               service 89%，api/registry 97%）
uv run --frozen ruff check src tests                      →  All checks passed!
uv run --frozen ruff format --check src tests             →  145 files already formatted
uv run --frozen alembic upgrade head                      →  0001 → 0008 全链成功
uv run --frozen alembic current                           →  0008_metric_registry_release (head)
uv run --frozen alembic check                             →  No new upgrade operations detected.
uv run --frozen python examples/demo_phase6.py            →  完整跑通（v1 → v2 → rollback，13 条事件）
```

保留项：2 条第三方弃用警告（`starlette.testclient` 的 `httpx` 提示与 `anyio` 别名）。测试套件在解释器退出时被沙箱的批量删除守卫拦截 `pytest` 临时目录清理（`.demo/full_pytest*.log` 末尾可见 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`），影响的是临时目录回收，不影响任何测试结论与退出码。

## 36. Known Limitations 与 Recommended Next Step

**已知限制（均为实质性，不掩饰）：**

1. **真实执行未验收。** 全部 staging / shadow 结果来自 Mock 执行器加载随包合成 fixture，`execution_mode=mock`。报告与 release 记录中的 `missing_evidence` 恒含 `real Spark not verified`。真实 Spark/Hive/MySQL 仍是 NOT VERIFIED。
2. **无生产部署路径。** `target_environment=production` 在非 production 环境一律 409；`production_deployed` 是 `Literal[False]`。本阶段交付的是注册表治理，不是上线机制。
3. **无身份认证。** `reviewer` 由请求自报，PromotionReview / ReleaseReview / RollbackReview 三处人工决定均可被冒名。只能用于受控环境，不能宣称"可防冒名审批"。
4. **激活是单表指针切换且仅覆盖内置注册表。** CAS 只保证这一张表的一致性；多实例并发下若绕过本服务直接改库，守卫会检测到漂移并 409，但不会自动修复。
5. **Shadow 样本有限。** 比较主体数受执行行数上限（1000）约束，演示为 385/401；大基线上的小比例主体丢失可能落在 `max_entity_loss_fraction=0.05` 之下而不被标记。
6. **分布位移阈值没有被校准。** `max_distribution_shift=1.0` 与 p90 相对位移是单一统计量，仅作为 warning 信号，没有真实数据上的分布支撑。
7. **版本号不可回退设计。** 服务器从不复用已存在的版本号，长生命周期的家族版本号会持续增长（major 语义变化频繁时尤其明显）；这是刻意的取舍，但意味着"版本号小"不代表"更早"以外的任何信息。
8. **`version_change.changed_fields` 对 `initial` 为空。** 首版没有"与什么相比"的语义，差异只在后续版本上可见。
9. **事件表不是投递机制。** 未接入任何外部审计/监控系统，`metric_release_events` 只能在库内查询。
10. **回滚无冷却与频次约束。** 任何时刻都可以对任意非活跃版本发起回滚申请，只靠人工评审把关。

**Recommended Next Step（唯一推荐，本次不实现）：**

```text
# AIRI Phase 7 — Production Integration, Monitoring & Feedback Loop
```

Phase 6 把"版本身份、发布治理、逻辑激活、可回滚"这条链闭合了，但它止步于内置注册表。Phase 7 应回答本阶段刻意回避的问题：已激活版本如何被真实生产作业消费、执行结果如何回流、线上指标漂移如何触发预警与回滚，以及这些环节如何在不引入第二套真相的前提下与既有三个门禁衔接。真实 Spark/Hive/MySQL 与真实 LLM 的环境验收仍应先于 Phase 7 的任何生产集成动作完成。
