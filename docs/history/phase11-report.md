# AIRI Phase 11 — Multi-Scenario Validation Implementation Report

> 状态声明：本阶段全部内容跑在合成数据（`demo` 关系表 + 合成标签）、mock 执行器与显式 `AIRI_LLM_MODE=demo_mock` 替身上。**NOT PRODUCTION VERIFIED** 原则贯穿始终：任何未在真实运行时验证的路径都不声称成功。

## 1. Repository Assessment

编码前已完成仓库评估（记录于开发过程）：

- **可复用架构**：Requirement Parser（scenario 路由 prompt）、SkillPlanner / ToolPlanner、`GenerateSparkSQLMetric`（pinned version 工具）、SQL 静态验证、SandboxGuard、TestingWorkflow（6 项 invoice 检查）、Experiment（Coverage/KS/IV/Lift）、审批与 artifact 不可变性、Web 6 页面产品语言。
- **阻塞 enterprise_relation 的限制**：MetricIR 只有单 source + 单 aggregation + AND filters，不支持 join；SQL 工具只渲染单表；静态验证语法只认窗口聚合；测试类型不含关系检查；Demo LLM 只有 invoice 词表。
- **最小扩展**：Join IR（`MetricIR.joins` / `column_filters`，Optional 向后兼容）、join SQL 工具 pinned 新版本（1.3.0）、join SQL 语法验证器、场景级关系探针、enterprise_relation Scenario Skill、Demo LLM relation 词表、Web 示例选择器。
- **明确不动的文件**：已发布的历史迁移文件（新增列未发生，无需迁移）；Phase 1–9 的时间验证 / 注册 / 发布 / 生产 / 监控模块；invoice SQL 模板。

## 2. Phase 10 Regression Baseline

Phase 10 基线：pytest **646 passed / 14 skipped**，前端 Vitest 19 tests。

本阶段结束时（含 lint/格式统一与全部 Phase 11 测试）：

| 项 | 结果 |
| --- | --- |
| pytest | **675 passed / 14 skipped**（≥ 646 ✓，+29） |
| Vitest | **6 files / 28 tests passed**（≥ 19 ✓，+9） |
| Ruff | `check src tests` 全绿；`format --check` 176 files 全绿 |
| alembic | `0011_operational_convergence (head)`，`alembic check` 无漂移，无需新迁移 |
| demo | `examples/demo_phase11.py` exit 0，产物落 `examples/phase11/` |

## 3. Why enterprise_relation

`invoice_risk`：企业 → 发票事实表 → 时间窗口 → SUM/COUNT（单表）。
`enterprise_relation`：企业 → 关联自然人 → 关联企业 → 关系图 → COUNT DISTINCT（两跳 JOIN）。

二者在业务语义、数据结构、实体关系、SQL Join、指标解释上全部不同，且不需要任何新的 Workflow——正是 Domain Generalization 需要的对照。

## 4. Scenario Architecture

```text
Scenario Knowledge (业务语义)         Capability Skills (执行机制)
enterprise_relation                   metric_join / metric_count / spark_sql_generator
        │                                        │
        └──────────── Skill Planner ─────────────┘
                          ↓
              Structured Metric IR (+ joins[])
                          ↓
        Deterministic Tools → Static Validation → Sandbox
                          ↓
        Approval → Testing → Experiment → （同一套 Web UI）
```

## 5. Scenario Skill

`src/airi/skills/enterprise_relation.py`（`enterprise_relation@1.0.0`），只含业务语义：业务域、主体（enterprise / enterprise_id）、关系路径（enterprise → person → enterprise，两跳）、数据源含义、实体/关系字段解释、业务规则（COUNT DISTINCT 去重、按企业去重、自环排除、无路径企业缺行）、known pitfalls（直连 join 语义错误、数据覆盖偏差、重名自然人、不做 N 跳/穿透）、以及 scenario 自己的测试声明（8 个 test type）。

## 6. Capability Skill Reuse

- `metric_count@1.0.0` 直接复用（COUNT/COUNT DISTINCT 语义）。
- `spark_sql_generator@1.0.0` 复用（工具名同一，pinned 新版本见 §13）。
- **没有**新增 `enterprise_relation_count_skill` 之类的业务-机制耦合 Skill。

## 7. Join Capability

新增通用能力 `metric_join@1.0.0`（`src/airi/skills/capabilities.py`）：

> Compose two or more structured metric data sources through constrained deterministic joins.

它只知道"如何 join"（别名、等值条件、跨列排除），**不知道** enterprise → person → enterprise。两个 scenario 通过 `register_shared` 共享同名同版本 capability，冲突性重复注册会被拒绝（测试覆盖）。

## 8. Metric IR Extension

`MetricIR` 新增四个 Optional 字段（`joins`、`source_alias`、`aggregation_alias`、`column_filters`，`max_length` 受限），默认为空——所有既有 invoice IR 结构、schema version（1.0.0）、hash、存储、API 均不变。模型级校验：别名仅在声明 join 时有意义、别名唯一、join 类型封闭、比较字段必须落在已声明别名上。**未创建 MetricIRV2。**

## 9. Join IR

`JoinSpec`：`alias` + `join_type(inner|left)` + `source` + `conditions[]`（`FieldComparison`，operator 第一版只支持 `=` / `<>`，跨列排除走 `column_filters`）。通用机制门 `metric_ir/joins.py`（`validate_join_metric`）：最多 2 个 join、必须锚定等值键、聚合必须来自 joined alias、禁止窗口/分区/filters/dimensions/catalog。业务白名单 `metric_ir/enterprise_relation.py` 再收紧为"恰好声明的两跳路径"。先机制后业务，两层都不修复、只拒绝。

## 10. Requirement Parsing

- 真实 Parser：`prompts.py` 新增 `RELATION_SYSTEM_PROMPT`（prompt version 1.2.0），按 scenario 选择，完整描述 IR 固定写法与必须拒绝的歧义/穿透类需求；未声明的 scenario 回落 invoice prompt，由校验器显式拒绝。
- Demo LLM：`demo_llm.py` 扩展确定性 relation 解析（词表匹配 + 禁词），主逻辑无 `if "关联企业" in requirement` 的业务硬编码；未知 relation 需求显式拒绝（`unsupported_reason`），绝不静默回退。

## 11. Skill Planning

`invoice_risk` 与 `enterprise_relation` 各自声明 capabilities；Planner 校验 scenario 具备全部所需能力后才产出计划。测试：relation 计划不含 invoice 能力、invoice 计划不含 join 能力、跨场景 planning 被拒绝（PlanningError）。

## 12. Tool Planning

同一工具族 `generate_spark_sql_metric`：
- invoice → `1.1.x`（单表窗口）
- relation → **`1.3.0`**（`GenerateJoinMetricSQL`，渲染声明式 join 路径；无 scenario 专属工具）

## 13. SQL Generation

`tools/join_sql.py`：Metric IR → 结构化 `JoinMetricSQLInput` → Jinja 模板 `spark/join_metric.sql.j2`。实际产出（`examples/phase11/relation_join_sql.sql`）：

```sql
SELECT
    ep.`enterprise_id` AS entity_id,
    COUNT(DISTINCT pe.`related_enterprise_id`) AS `related_enterprise_count`
FROM `demo`.`enterprise_person_relation` AS ep
INNER JOIN `demo`.`person_enterprise_relation` AS pe
    ON ep.`person_id` = pe.`person_id`
WHERE pe.`related_enterprise_id` <> ep.`enterprise_id`

GROUP BY ep.`enterprise_id`;
```

Workflow 中没有任何硬编码 SQL 字符串。

## 14. Static Validation

`workflows/development/join_validation.py`（`JoinSQLValidator`）：封闭 join SELECT 语法——仅 INNER/LEFT JOIN、来源与字段白名单（重解析渲染结果并与 Metric IR 逐项比对：join 数量、类型、来源、条件、排除谓词）、无窗口、无注释/DDL/DML/子查询注入。声明 joins 的 metric 必须由该验证器处理（单表验证器显式拒绝）。

## 15. Sandbox Compatibility

`SandboxGuard` 最小扩展：把每个 join 的 source 纳入允许来源集合，仅接受结构化生成器产出的 SQL；UNION / CTE / UDF / 任意 SQL 仍然禁止。测试：join draft 通过、外部 database 拒绝、naive 直连 join（跳过自然人）拒绝。

## 16. Synthetic Relationship Dataset

`infrastructure/relation_fixture.py`：120 家企业 E1..E120 + 孤立企业 ISO（无任何关系），全部使用合成标识（`91310000SYNTHxxxx` / `SYNTHPERSONxxxx`），schema 为 `demo.enterprise_person_relation` / `demo.person_enterprise_relation`。确定性构造规则覆盖：重复关系行（i%3）、自环（i%7）、hub person（i%4）、多自然人指向同一企业、无关系企业。未出现任何真实客户、身份证、统一社会信用代码或内部表名。

## 17. Hand-calculated Expected Results

期望值由文档化构造规则手算（`expected_counts()`），不调用生产代码：`present(i) iff i%5>=1 or i%4==0`，count = max(i%5,1)，共 **102** 家企业出现；ISO 与无路径企业**缺行不报 0**（口径在测试与 fixture 文档中明确定义，不含糊）。spot check 覆盖任务书 §37/§38 全部案例。

## 18. Automated Testing

`testing/relation.py` 四个独立探针（join / distinct / self_exclusion / missing_relation），全部执行**已批准 SQL**并与独立 oracle（`person_mediated_count`）比对，不看 SQL 文本、不做子串匹配。join 探针额外执行一个"故意错误的直连 join"对照语句，要求结果必须与之不同——证明结果确实经由 person 中转。加上 schema/null/duplicate/reconciliation 共 **8 项检查**，全部来自 scenario 的 `test_rules` 声明（后端返回测试列表，前端 generic 渲染）。

## 19. Distinct Semantics

COUNT DISTINCT 按关联**企业**去重：重复关系行不膨胀计数；多自然人共同指向同一企业仍计 1（探针 + API 链路测试双覆盖）。

## 20. Self Relation Exclusion

`pe.related_enterprise_id <> ep.enterprise_id` 由 IR 强制（缺 column_filters 直接拒绝）；探针验证只回连自身的企业不出现在结果中。

## 21. Experiment

复用既有 Experiment 链路：`enterprise_relation_sample` 数据快照 + `synthetic_relation_label` 标签定义（键 `enterprise_id`），relation fixture 标签刻意含噪（高关联数偶尔 safe、低关联数偶尔 bad），不做 KS=1 的完美分离。

## 22. Evaluation Results

`related_enterprise_count`（纯合成数据）：

| Coverage | KS | IV | Risk Direction |
| --- | --- | --- | --- |
| 84.3% | 0.646 | 2.04 | higher_is_riskier |

Threshold Candidates（p50/p75/p90…）复用既有评估结构，未新增第二套 evaluation。

## 23. Web Demo Integration

`/api/v1/meta` 返回注册的 scenario 列表（后端为唯一事实源）；Demo Context 提供 Invoice Risk / Enterprise Relation 两个示例，一键填充；未新增独立页面——两个场景走同一 `/development` `/testing` `/experiments`。

## 24. Development UI

示例选择器（`Demo example scenario`）；结果面板显示 Scenario Skill tag（`enterprise_relation@1.0.0`）、Capability tags（`metric_count` / `metric_join` / `spark_sql_generator`）、Metric IR viewer 的 Join 行（`demo.person_enterprise_relation AS pe ON ep.person_id = pe.person_id` + Self-exclusion 行）、Generated SQL viewer（浏览器不生成/不修改 SQL）。两个 Demo 并列时 capability 组合差异一目了然。

## 25. Testing UI

测试列表由后端 TestReport 返回、前端 generic 渲染（name/status/evidence）；relation 指标显示 8 项检查（Join Correctness、Distinct、Self-exclusion 等），页面文案说明"六个属于 invoice 族、八个属于关系族"，**没有**为统一而硬显示不适用的 Window 项。

## 26. Experiment UI

完全复用：换 metric 名、评估数据与图表（84.3% / 0.646 / 2.04），无新增页面；SYNTHETIC MOCK 声明保留。

## 27. Browser E2E

真实 dev server + 真实 Chromium，两条引导链路各完整跑一遍（Load Demo → Generate → Submit → Approve → Testing → Experiment），全程无手填 UUID（状态经 route query + guided run 传递）：

- **Enterprise Relation**：development → testing（8/8）→ experiment（自动完成，Analyze Experiment 达成）。
- **Invoice 回归**：同一路径完整通过，证明同一套 Web 支持两个 scenario 且互不破坏。

（E2E 使用 JS 注入点击驱动 Vue 按钮；这是测试执行方式，不是产品行为变更。）

## 28. Screenshots

真实 dev server 截取（无合成图）：

- `docs/screenshots/enterprise-relation-development.png`（含 Join IR、JOIN SQL、capability 组合）
- `docs/screenshots/enterprise-relation-testing.png`（8/8 Passed）
- `docs/screenshots/enterprise-relation-experiment.png`（84.3% / 0.646 / 2.04）

## 29. Invoice Scenario Regression

- pytest：原有 invoice/单表 MetricIR/窗口 SQL 全部测试通过（675/14 ≥ 646/14）。
- API 链路测试：invoice 测试仍然恰好 6 项检查。
- 浏览器 E2E：invoice 引导链路完整通过。
- `MetricIR.joins` 为 Optional 空 defaults，旧 IR hash/schema 不变。

## 30. Test Results

| 项 | 结果 |
| --- | --- |
| pytest | 675 passed / 14 skipped（skips 仍为需真实集群的集成测试） |
| Vitest | 28 tests / 6 files passed |
| Ruff check + format | 全绿 |
| alembic | head `0011_operational_convergence`、无漂移、零新迁移（joins 存 JSON 列） |
| demo_phase11 | exit 0，产物 8 个文件落 `examples/phase11/` |

## 31. Architecture Generalization Findings

真正要证明的结论（§77）：

1. **New Scenario ≠ New Workflow**。第二个场景的落地只需要：1 份 Scenario Skill（纯语义）、1 个通用 capability（metric_join）、1 个工具 pinned 版本 + 模板、1 个语法验证器、4 个测试探针、Demo LLM 词表。Requirement Parser、Skill/Tool Planner、审批、Testing、Experiment、Web 全部零改动或最小接入。
2. **Relationship Semantics ≠ JOIN Implementation**：join 机制层（`metric_ir/joins.py`、`join_sql.py`、`join_validation.py`）不含任何业务路径；业务路径只存在于 scenario 白名单。换一个第三场景，机制层原样复用。
3. 两层校验（通用机制门 → 场景业务门）模式可扩展：机制门保证结构，业务门保证口径，都不修复输入。
4. 测试类型声明由 scenario 携带，后端即事实源，前端零业务判断——这条在两个场景下同时成立。

## 32. Known Limitations

- 关系路径固定两跳、INNER JOIN、operator 仅 `=`/`<>`、最多 2 个 join；RIGHT/FULL/CROSS/LATERAL/递归均未实现（有意为之）。
- relation 指标不做时间窗口、不做关系类型筛选、不做 N 跳穿透/最终受益人。
- Web E2E 的点击驱动是 JS 注入（headless CLI 的 Vue 兼容问题），非产品缺陷，但意味着 E2E 未验证真实鼠标事件路径。
- Reflection 未实现（可选）；relation 不进入 Refinement/Temporal/Promotion/Release（阶段边界明确）。
- 所有数字均为合成数据，不构成任何真实风险区分度证据。
- Metric IR schema version 保持 1.0.0：joins 为向后兼容 Optional，未触发 minor/major 升级判定（以现有版本制度）。

## 33. Recommended Next Step

**AIRI Phase 12 — Open Source & Portfolio Release**：README 全面重构（架构图、Demo GIF、Quick Start、One-command Demo）、Docker Compose、GitHub Actions、LICENSE、`.env.example` 补全、开源安全清理、Issue Templates、Contributing、API Docs、Release Notes、v1.0.0。不要在 Phase 11 内启动。
