# AIRI v1.0.0 — 作品集版

**一个 AI 辅助的风控指标研发平台。**

AIRI 把一条自然语言风控需求转化为经过严格校验的 **Metric IR**、确定性 SQL、自动化指标测试、统计实验、证据驱动的反思，以及受治理的指标版本。

> **LLMs reason. Python verifies. Humans govern.**（模型负责推理，Python 负责验证，人负责治理。）

这是首次公开发布。AIRI v1.0.0 **在设计上功能已完备** —— 平台已冻结，后续的能力工作放在 [路线图](../README.md#roadmap) 而不是本发布版中。

---

## 亮点

- **自然语言需求解析** —— 一条风控需求会被编译为严格、带版本的 Pydantic 契约；歧义或跨域需求会被拒绝，绝不猜测。
- **结构化 Metric IR** —— 模型与数据库之间可评审的边界。模型可以提议，但只有 schema 能接纳。
- **Scenario Skill × Capability Skill** —— 领域语义与执行机制分离。能力只需声明一次，并钉选工具版本。
- **确定性 SQL 生成** —— SQL 由带版本的 Python 工具和受控 Jinja 模板产出。**模型从不编写实际交付的 SQL。**
- **静态 SQL 校验** —— 闭式文法（完整匹配，而非子串黑名单），并直接拒绝 DDL/DML。
- **自动化指标测试** —— schema、空值处理、重复、窗口边界、join 正确性、distinct 语义、自环排除、对账，以及对 join 型指标的直连 join 对照。
- **KS / IV / Lift 实验** —— 覆盖率、bad rate、十分位分箱、带方向的 KS、IV、lift、阈值候选，以及相对冻结参考的 PSI。
- **证据驱动的 LLM 反思** —— 假设与计算事实分开存储，类型不同、表也不同。
- **受控精炼（refinement）** —— 有界、由人决策的提案。不会有任何东西被悄悄替换。
- **时序验证** —— 多切片历史切片与跨期（out-of-time）切片，配以基于 PSI 的稳定性诊断。
- **Metric Registry** —— 不可变、内容哈希的版本，带审计事件流与两阶段回滚。
- **人在环治理** —— 四个各自独立记录的闸门：SQL 审批、晋级评审、发布评审、部署评审。
- **Vue Web UI** —— 仪表盘、开发、测试、实验、反思、注册表。浏览器中零业务计算；浏览器从不调用 LLM。
- **两个 demo 场景走完全相同的一条工作流** —— Invoice Risk 与 Enterprise Relation。

---

## 架构

```text
Natural Language
  → Metric IR               strict Pydantic schema, versioned
  → Skill Planner           scenario skill × capability skills
  → Tool Planner            deterministic Python tool, pinned version
  → SQL artifact            Jinja template, content-addressed
  → Static validation       closed allow-list grammar
  → Automated testing       scenario-declared probe checks
  → Experiment              coverage / KS / IV / lift / PSI
  → Reflection              hypotheses (never auto-applied)
  → Registry + Governance   immutable versions, human approval gates
```

三层各自拥有不同的东西，而边界就是设计本身：

| 层 | 负责 |
| --- | --- |
| **LLM** | 需求理解、证据解释 |
| **Python** | IR 校验、SQL 生成、测试、统计、哈希 |
| **Human** | 审批、晋级、发布、部署 |

完整细节：
[架构总览](https://github.com/huangJJ8/airi/blob/main/docs/architecture/overview.md)
·
[设计原则](https://github.com/huangJJ8/airi/blob/main/docs/architecture/design-principles.md)

---

## Demo 场景

**Invoice Risk** —— "统计企业近30天开票金额"

```text
invoice_risk × metric_sum × metric_window
→ single-source windowed SUM
```

**Enterprise Relation** —— "统计企业关联自然人控制的其他企业数量"

```text
enterprise_relation × metric_join × metric_count
→ two-hop JOIN (enterprise → person → enterprise)
→ COUNT DISTINCT with self-loop exclusion
```

两个场景共用同一套解析器、IR、规划器、SQL 生成器、校验器、审批闸门、测试层、实验层和 web 工作流。第二个场景**没有新增任何编排** —— join 机制被提升为通用能力（`metric_join@1.0.0`），而不是写成场景特例。

> `New Scenario ≠ New Workflow.`

---

## Web UI

Vue 3 + TypeScript + Vite + Element Plus + ECharts，共六个页面：
Dashboard · Metric Development · Metric Testing · Experiment ·
Reflection & Refinement · Metric Registry。

每一个 KS / IV / lift 值和每一个版本状态都来自后端 API。前端执行**零**业务计算，并使用后端的 error code 来呈现治理冲突（HTTP 409），而不是自行编造消息。

---

## 测试

| 套件 | 结果 |
| --- | --- |
| 后端（`pytest`） | **675 passed / 14 skipped** |
| 后端覆盖率 | **91%** |
| 前端（`vitest`） | **28 passed** |
| Lint（`ruff check` + `format --check`） | clean |
| 迁移（`alembic check`） | head `0011_operational_convergence`，无漂移 |
| 开源安全扫描 | clean |

以上全部也在打标签的那个提交上、在真实的 GitHub Actions runner 上跑过：workflow `CI`，run #5 —— **两个 job 全绿**（前端 install / type-check / build / unit tests；后端 lint / format / safety scan / tests / coverage / migrations / drift check）。

那 14 个跳过的测试是需要真实基础设施（Spark/Hive、MySQL、生产身份、遥测）的集成套件。它们是被**跳过，而不是被 mock** —— 一套全绿的测试永远不意味着集成已验证。

---

## 本地运行

要求：Python ≥ 3.12、[uv](https://docs.astral.sh/uv/)、Node.js ≥ 20.19。

**Windows（已验证）：**

```powershell
git clone https://github.com/huangJJ8/airi.git
cd airi
.\scripts\start-demo.ps1     # migrate → seed synthetic data → start both servers
```

然后打开 <http://localhost:5173>。API 文档在 <http://localhost:8000/docs>。用 `.\scripts\stop-demo.ps1` 停止。

**macOS / Linux（尽力而为）** —— `./scripts/start-demo.sh` / `./scripts/stop-demo.sh`。镜像脚本存在，但本项目的已验证环境是 Windows。

**无需安装，仅用终端：**

```bash
uv run --frozen python examples/quick_demo.py
```

**Docker** —— `docker compose up --build`。已提供并经过静态审查；运行时**未验证（NOT VERIFIED）**（开发环境中没有 Docker）。

无需 API key、无需集群、无需内网。demo 运行在 SQLite 上，使用确定性的 LLM 替身（`AIRI_LLM_MODE=demo_mock`）—— 这是有意为之、显式配置的替身，不是兜底方案。

---

## 已知限制

直说，因为它们很重要：

- **所有随附数据均为合成数据。** 每个数据集都在仓库内生成。报告的统计量只用来演示这条流水线；它们对真实预测能力**没有**任何说明。
- **Spark / Hive / MySQL 生产集成未经验证。** 适配器和执行器在代码中存在，并在边界层被行使，但在本仓库中从未针对真实环境运行过。它们默认不生效（inert）并故障关闭（fail closed）。
- **这是作品集 / 研究型实现。** 它**不得**被理解为经过验证的金融风控基础设施。
- **按构造，`production_deployed` 在所有地方都是 `false`。**
- **demo 模式下评审人身份由调用方声称**（demo 级，无认证）。
- **Docker 运行时未经验证**，且 `.sh` 脚本为尽力而为。
- **有一个合成标识符是有意不透明的** —— 一个看起来像内部表名的名字，保留它是为了避免让历史产物失效。它只是一个占位符，背后没有真实数据。

参见 [限制与已知不足](https://github.com/huangJJ8/airi/blob/main/README.md#limitations)
和 [真实环境检查清单](https://github.com/huangJJ8/airi/blob/main/docs/guides/real-environment-checklist.md)。

---

**Full Changelog**: [CHANGELOG.md](https://github.com/huangJJ8/airi/blob/main/CHANGELOG.md)
**License**: Apache-2.0
