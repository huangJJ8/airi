# AIRI Phase 10 — Web MVP Implementation Report

> 诚实声明：本报告所有数字来自真实运行的命令输出（pytest / Vitest / ruff / alembic / 浏览器端到端链路）。截图由 agent-browser 自动截取自真实运行的 dev server。合成数据边界全程显式声明，无任何伪造生产能力。

## 1. Repository Assessment

- 后端：`src/airi/`（api / metric_ir / skills / tools / execution / testing / evaluation / experiments / reflection / refinement / temporal / registry / production / workflows / observability / core / infrastructure / approvals / agents），Phase 1–9 全链路已完成，`alembic head = 0011_operational_convergence`。
- 前端：接手时**不存在**任何前端目录（`airi-web/`、`frontend/`、`web/`、`package.json` 均不存在），按任务书新建 `airi-web/`。
- 包管理器：无既有 lock 文件，按默认使用 **npm**（`package-lock.json` 已生成并提交）。
- 非纯 git 仓库（目录无 `.git`），文件级核验代替 `git status`。

## 2. Backend Freeze Boundary

新增后端能力**仅限任务书允许的例外**：bug 修复 + 必要 read API + CORS + 响应模型可消费性。领域能力零新增：

| 改动 | 类别 | 理由 |
| --- | --- | --- |
| `core/config.py`：`llm_mode`、`cors_origins`、`demo_fixtures` 三个配置项 | Web 接入必要配置 | 显式 demo 模式，绝不静默回退 |
| `infrastructure/demo_llm.py`（新） | 演示用确定性 LLM 替身 | 无凭据也能完整演示；未知需求显式拒绝 |
| `main.py`：按 `llm_mode` 注入 LLM 客户端、CORS 中间件、`demo_fixtures` 选择种子夹具 | Web 接入必要装配 | 通配符 Origin 在 validator 层直接拒绝 |
| `api/routes.py`：`GET /api/v1/meta` | 必要 read API | 顶栏 LOCAL DEMO / Runtime 模式的真实数据源 |
| `api/experiments.py`：`GET /datasets/latest?name=`、`GET /labels/latest` | 必要 read API | 浏览器不能伪造 dataset checksum，必须复用种子快照 |
| `experiments/service.py`：`latest_dataset(name)` / `latest_label()` | 必要 read API 的服务层 | 只读查询，无写路径 |

未实现（进入 Roadmap）：Controlled Autonomy、Multi-Agent、LangGraph、真实 Spark、Kerberos、真实 Telemetry、真实 IAM、Break-glass、多环境传播、新 Governance/Monitoring/Reconciliation。

## 3. Frontend Architecture

```text
airi-web/src/
├── api/            # client.ts（错误归一化）+ development/experiments/reflection/refinement/registry
├── components/     # StatusTag / MetricIRViewer / SqlViewer
├── composables/    # demo.ts（DemoContext，sessionStorage 持久化）
├── layouts/        # MainLayout（顶栏 + 侧边菜单 + Stepper）
├── pages/          # 6 个核心页面
├── router/         # 产品语言路由
├── types/          # airi.ts（从 OpenAPI 手工对齐的接口）
└── utils/          # canonicalHash / format / status
```

原则：组件内不散落 Axios 请求；UUID 通过 route query + DemoContext 自动传递，用户全程无需复制 ID；前端零业务计算（KS/IV/Lift/Outcome/版本分类全部来自后端）、零 LLM 调用（浏览器永不直连 OpenAI）。

## 4. Tech Stack

Vue 3 + TypeScript + Vite + Vue Router + Element Plus + ECharts + Axios + Vitest。状态管理第一版使用 Composition API + Composable（`useDemoState`），未引入 Pinia。无 SSR/Nuxt/微前端。

## 5. Added / Modified Files

后端（7 文件）：`core/config.py`、`infrastructure/demo_llm.py`、`main.py`、`api/routes.py`、`api/experiments.py`、`experiments/service.py`、`tests/test_demo_mode.py`。
脚本（2 文件）：`scripts/seed_demo.py`、`scripts/verify_web_flow.py`。
前端（29 文件）：`airi-web/` 全新 —— 11 个 `.vue`（App + Layout + 3 组件 + 6 页面）、18 个 `.ts`（api/types/utils/router/composables/main/vite-env + 4 个 spec）。
文档：本报告、`README.md` Phase 10 节、`docs/screenshots/`（6 张）。

## 6. Routing

`/dashboard`（默认重定向 `/`）、`/development`、`/testing`、`/experiments`、`/reflection`、`/registry`。左侧菜单为产品语言（Dashboard / Metric Development / Metric Testing / Experiment / Reflection & Refinement / Metric Registry），无任何 "Phase N" 字样。顶部 Stepper（1 Development → 5 Registry）随 DemoContext 自动点亮当前步。

## 7. API Client Architecture

- `client.ts`：单一 Axios 实例，`VITE_API_BASE_URL` 配置 base（`.env.example` 提供 `http://localhost:8000`）；`describeApiError()` 把 400/404/409/422/500 统一归一化为 `{ error_code, message }`，**409 作为治理错误一等公民**展示后端 `error_code`（如 `reconciliation_review_required`）而非 "Request failed"。
- 按域模块：`development.ts` / `experiments.ts` / `reflection.ts` / `refinement.ts` / `registry.ts`。
- `canonicalHash.ts`：与后端 `approvals/artifacts.py` 完全一致的规范化 JSON SHA-256，用于前端预览 IR hash（展示用途，权威值仍以 API 响应为准）。

## 8. Dashboard

4 张 KPI 卡（Metric families / Active version / Open alerts / Runtime mode）全部来自真实 API（`/metrics`、`/metric-alerts`、`/meta`）。核心是 **How AIRI works** 流程图：Requirement → Metric IR → Development → Testing → Experiment → Reflection → Registry，每个节点标注角色归属（LLM / Python / Human）且可点击进入对应页面；顶部显式三角色图例。下方 Governance 卡显示 `production_deployed = false` → **NOT PRODUCTION VERIFIED**。Recent Activity 展示能从真实 API 可靠获取的 Active version 与 open alerts，不伪造数据库不存在的数据。

## 9. Metric Development

顶部 "Build a Risk Metric" + 需求输入框 + **Load Demo**（一键填入 `统计企业近30天开票金额`）+ anchor time。Generate Metric 调用 `POST /api/v1/development/generate`。生成结果按 Requirement Understanding → Skill Planning → Tool Planning → Metric IR → SQL Preview 依次展开，底部 CTA `Submit for Human Review → Approve as Reviewer → Continue to Testing →`。

## 10. Requirement Understanding UI

卡片字段（Scenario=invoice_risk / Entity=enterprise / Aggregation=SUM / Field=invoice_amt / Window=30 days / Time field=invoice_date）全部来自真实响应的 `requirement` 结构，无前端硬编码解析。

## 11. Skill / Tool Planning UI

Scenario Skill（`invoice_risk@…`）与 Capability Skills（`metric_sum` / `metric_window` / `spark_sql_generator`）用 Tag/Badge 展示；Tool Planning 卡显示 `generate_spark_sql_metric@…` 并注明 "the tool generates the SQL"——让面试官一眼看到 **Skill ≠ Tool**。

## 12. Metric IR Viewer

可折叠 JSON Viewer：默认折叠展示友好 Summary（entity / measure / window / filters），展开后为完整结构化 JSON（含 syntax 高亮的 key 着色），不是裸 JSON dump。

## 13. SQL Viewer

`SQL Preview` 代码块（等宽字体 + 简易关键字高亮）+ 一键 Copy。SQL 来自 `artifact.sql`，页面明确标注"浏览器不生成、不修改 SQL"。

## 14. Metric Testing

`/testing?artifact_id=…&approval_id=…` 自动携带上下文。6 项确定性验证（Schema Validation / Null Entity / Duplicate Entity / Window Semantics / Boundary Test / Reconciliation）逐条展示 Test Name + PASS/FAIL + Short Evidence（Expected vs Actual），详细 JSON 折叠。顶部汇总 **6 / 6 Passed + PASS tag**（状态忠实来自后端，含 Passed with Warnings / Failed 分支）。后端不允许进入实验时只显示 `Resolve Test Failures`，前端不绕过 Gate。

## 15. Experiment Dashboard

KPI 卡：**Coverage 92.0% / KS 0.577 / IV 6.03 / Direction: Higher is Riskier** —— 全部来自种子链路真实实验报告。下方双图 + Threshold Candidates 表（Source / Operator / Threshold / Hit Rate / Precision / Recall / Lift），表格明确标注 **Research Candidates — not a recommended production threshold**。

## 16. Charts

ECharts 两张：`Score distribution by bin`（good/bad 堆叠柱状图）、`Bad rate & lift by bin`（bad rate 柱 + lift 折线双轴）。KS 累计曲线第三图按任务书（§29）从简未做。数据直接来自 `evaluation.bins`，前端不重算任何统计量。（实现备注：`watch(evaluation, …, { flush: 'post' })` 保证图表容器挂载后渲染。）

## 17. Reflection UI

左右布局：左侧 **Deterministic Evidence**（`computed by Python` 标签；coverage_gap：15/188 labeled entities 缺失非空 metric，缺失率 0.0798；strong_separation：KS≈0.577）；右侧 **AI Reflection**（model: mock-window-reflection@1.0.0），每条 Hypothesis 显式打上 **Hypothesis + confidence + unverified** 标签，evidence refs 指向真实 finding id。两侧视觉强分离。

## 18. Refinement Proposal UI

Proposal Card（Window Review：Current 30 days → Candidate 60/90 days + Reason）提供 **Accept for Investigation / Reject / Need More Evidence** 三个决策按钮，调用真实 Phase 4 API，前端不伪造 decision。

## 19. Candidate Comparison

候选实验完成后 Baseline vs Candidate 表格（Coverage / KS / IV 按窗口列对比），Outcome 用明显 Tag（WORSE / MIXED）——60d 候选是 WORSE，**故意不做成全绿**，用真实结果表达 "AI proposes, Experiment decides"。页面固定提示条：*AIRI never promotes an AI suggestion only because the model suggested it — every candidate faces the same experiments, and humans decide what ships.*

## 20. Metric Registry

Metric Family（invoice_amount）+ Version Timeline：**v1.0.0 `invoice_amount_60d` ACTIVE / v2.0.0 `invoice_amount_90d` RETIRED**（状态来自真实 Registry API）。版本详情（Version / Status / Metric Name / Created At / Source Artifact / IR Hash）默认展示核心字段，高级信息折叠。

## 21. Version Comparison

选择 v1.0.0 vs v2.0.0 调用真实 compare API：`window.size 60 → 90`（↓ 前后对比展示），分类 **MAJOR** 大标签；display_name/description 等弱化展示。

## 22. Governance / Runtime Summary

Dashboard 的 Runtime / Governance 卡（Runtime Mode = mock、Real Production Verified = NO、Active Version = v1.0.0、Open Alerts = 真实数量）+ Release Events 时间线（version_registered → release_created → staging_validated → release_approved → activated → rollback_requested → rollback_approved → rolled_back；种子数据 13 条真实事件）。Alert 卡示例突出 **Automatic Action: None / Recommended: Investigate**，明确 **Alert ≠ Automatic Rollback**。

## 23. Demo Navigation

无任何手动 UUID：Development 生成后 `Continue to Testing →` 携带 artifact_id/approval_id；Testing 通过后 `Run Experiment →` 携带 test_run_id；Experiment 完成后 `Analyze Experiment →` 携带 experiment_run_id；Reflection 决策后 `View Metric Registry →`。DemoContext（sessionStorage）保存 requirement / artifact_id / approval_id / test_run_id / experiment_run_id / reflection_run_id / refinement_run_id，只用于导航，不是第二个 Workflow DB。Stepper 自动点亮当前步。

## 24. Synthetic Data Boundary

Experiment 页固定警示框：**Synthetic Data — for demonstration only. Results do not represent real financial performance.** 顶栏常驻 `LOCAL DEMO · Synthetic Data`。Experiment 数字旁不隐藏合成属性；Production 一律 **NOT PRODUCTION VERIFIED**（后端 `production_deployed` 恒为 `false`，`/meta` 返回 `Literal[False]` 类型级锁死）。

## 25. Error Handling

统一归一化 400/404/409/422/500；409 展示后端 `error_code` + `message`（治理语义可读）。六个页面全部实现 Loading / Empty / Error / Success 四态——例如 Experiment 页无引导上下文时显示 "Missing context — go back to Testing" 而不是崩溃或伪造数据。

## 26. Frontend Tests

Vitest：**4 个 spec 文件 / 19 个测试全过**——`client.spec.ts`（错误归一化）、`status.spec.ts`（状态徽章映射）、`format.spec.ts`（实验指标格式化）、`canonicalHash.spec.ts`（与后端一致的内容摘要）。符合 "核心逻辑 + build" 的测试定位。

## 27. Backend Regression Results

```text
uv run --frozen pytest -q
646 passed, 14 skipped in 1700.15s
```

Phase 9 基线 639/14，新增 7 个 demo 模式测试（demo LLM 解析/变体/拒绝、通配符 CORS 拒绝、demo 夹具加载、meta 端点、demo_mock 端到端生成），零删减。

## 28. Frontend Build Results

```text
npm run build   → vue-tsc 类型检查 + vite build 通过（exit 0）
npm run test    → Test Files 4 passed (4), Tests 19 passed (19)
npm run lint    → 未配置 eslint（最小配置原则，TypeScript 严格检查由 vue-tsc 承担）
```

## 29. Screenshots

`docs/screenshots/`（agent-browser 自动截取自真实运行的 dev server + 种子数据库）：

| 文件 | 内容 |
| --- | --- |
| dashboard.png | KPI + 三角色图例 + 可点击流程图 |
| development.png | 需求理解 / Skill / Tool 规划真实响应 |
| testing.png | 6/6 Passed 六项验证 |
| experiment.png | 92.0% / 0.577 / 6.03 + 双图表 |
| reflection.png | Deterministic vs AI 分离 + Hypothesis 标签 |
| registry.png | v1.0.0 ACTIVE / v2.0.0 RETIRED + 事件时间线 |

浏览器端到端链路（Load Demo → Generate → Submit → Approve → Continue to Testing → Run Automated Tests → Run Experiment → Analyze Experiment）已通过 UI 自动化实际走通，各页截图即链路产物。另有 HTTP 级验证脚本 `scripts/verify_web_flow.py`（CORS 预检验证：`localhost:5173` 获得正确 Allow-Origin，未知 Origin 被拒）。

## 30. Known Limitations

- 前端未配置 eslint（vue-tsc 严格类型检查替代）；未做移动端适配（仅保证 1440/1920 桌面）。
- Candidate Comparison 的候选实验依赖种子链路预置，UI 引导内未提供 "重新跑候选实验" 按钮（数据真实，入口受限于第一版范围）。
- `MockNotificationSink`/生产治理页面未做（按任务书 §43 有意降级为 Dashboard 卡片）。
- DemoContext 为 sessionStorage：刷新标签页即重置（有意为之，避免状态漂移）。
- 截图基于 demo_mock LLM 替身；接真实 LLM 仅需 `AIRI_LLM_MODE=openai_compatible` + 凭据，UI 无需改动。
- Dashboard KPI 由前端聚合多个已有 API，无统一统计端点（按任务书 §12 有意不做后端统计系统）。

## 31. Recommended Next Step

下一步仅推荐：**AIRI Phase 11 — Multi-Scenario Validation**。新增 `enterprise_relation`（或另一个与 invoice_risk 明显不同的 Scenario），证明 Scenario Skill × Capability Skill 组合不是为 `invoice_amount` 写死的；Web 侧仅需在 Demo 场景下拉中增加一项。不开始实现 Phase 12（开源发布套件）。
