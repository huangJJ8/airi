# AIRI

**AI 辅助的风控指标研发平台**

AIRI 把自然语言描述的风控指标需求，转换为结构化的**指标 IR（Metric IR）**、确定性 SQL、
自动化测试、指标实验、证据驱动的反思，以及受治理的指标版本。

> **LLMs reason. Python verifies. Humans govern.**（模型负责推理，Python 负责验证，人负责治理。）

[![CI](https://github.com/huangJJ8/airi/actions/workflows/ci.yml/badge.svg)](https://github.com/huangJJ8/airi/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Vue](https://img.shields.io/badge/vue-3.5-42b883)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Status](https://img.shields.io/badge/status-portfolio%20%2F%20research-orange)

![AIRI dashboard](docs/screenshots/hero.png)

> AIRI 是一个 AI 辅助的风控指标研发平台，将自然语言指标需求转换为可验证、可实验、可审计的指标工程流程。

---

## AIRI 是什么？

AIRI 是一条面向**指标工程**的 agent 流水线。你用自然语言描述一个风控指标，AIRI 把它解析成
结构化表示，用确定性的 Python 工具生成 SQL，对 SQL 做测试，跑一次统计实验，对证据做反思，
并在每一个真正重要的闸口把结果交给人来批准。

它建立在一个架构赌注之上：**语言模型可以被信任去理解意图，但永远不能被信任去产出将要部署的产物。**

演示完全在本地运行，使用合成数据——不需要 Spark，不需要 MySQL，不需要 LLM API key，不需要内网。

![AIRI demo walkthrough](docs/demo/airi-demo.gif)

*Invoice Risk 流程，录制自真实的本地演示服务（48 秒）。也提供 [MP4](docs/demo/airi-demo.mp4)。*

---

## 为什么需要 AIRI？

**问题。** LLM 能生成 SQL，但原始生成的 SQL 难以信任、难以测试、难以治理。它没有可对照的
规格可供评审，模型或提示词一变它就悄悄改变，而且它没有一个干净的节点让人可以说
*"这是我批准的那条查询。"*

**做法。** 在模型与数据库之间放入一个严格的、可评审的表示：

```text
Natural Language
   → Metric IR                 (structured, schema-validated)
   → Deterministic Python tool (template + allow-list)
   → SQL artifact              (content-addressed, approved by hash)
   → Automated testing
   → Experiment                (coverage / KS / IV / lift)
   → Reflection                (hypotheses only)
   → Governance                (human approval at each gate)
```

模型的输出必须通过严格的 Pydantic schema。如果通不过，运行就显式失败——AIRI 从不静默修补
一个糟糕的模型响应，也从不悄悄回退到预置答案。

---

## 核心特性

- **自然语言指标需求** —— 解析为结构化意图
- **结构化指标 IR** —— 严格、带版本的 Pydantic 契约
- **场景技能 × 能力技能** —— 领域知识与执行机制分离
- **确定性 SQL 生成** —— 受控的 Jinja 模板，不由模型撰写 SQL
- **SQL 静态校验** —— 白名单语法（只允许 `INNER`/`LEFT` join；禁止 UNION、CTE 注入、UDF、DDL/DML）
- **指标自动化测试** —— schema、空值、重复、窗口边界、join 正确性、distinct 语义、自环排除、对账
- **实验** —— 覆盖率、坏样本率、十分位分箱、带方向的 KS、IV、提升度、阈值候选
- **证据驱动的 LLM 反思** —— 假设与计算得到的事实分开存储
- **受控精炼** —— 有界、由人决定的候选方案
- **时间 / OOT 验证** —— 针对冻结参考分布的 PSI，由确定性 Python 计算（0 次 LLM 调用）
- **指标注册表** —— 不可变、带版本、可审计、可回滚
- **人在环治理** —— SQL 审批、晋级评审、发布评审、部署评审
- **Vue 3 Web 界面** —— 仪表盘、开发、测试、实验、反思、注册表
- **两个演示场景**走同一条完全相同的流水线

---

## 工作原理

![How AIRI works](docs/screenshots/dashboard.png)

每个阶段都会写下一份产物，而每份产物都是下一阶段的证据。界面会标明每一步归哪一层负责：

| 层 | 负责的内容 |
| --- | --- |
| **LLM** | 需求理解、反思（假设） |
| **Python** | IR 校验、SQL 生成、测试、统计、哈希 |
| **Human** | 审批、晋级、发布、部署 |

完整流水线与角色划分见 [架构总览](docs/architecture/overview.md)。

---

## 架构

```text
Scenario Skill  (business semantics)     Capability Skill  (mechanism)
   invoice_risk        ──┬──►  metric_sum, metric_window ──► spark_sql_generator
   enterprise_relation ──┴──►  metric_join, metric_count  ──► spark_sql_generator
                                        │
                                        ▼
                    Metric IR  →  Tool  →  SQL  →  Validation
                                        │
                                        ▼
                    Testing → Experiment → Reflection → Governance
```

> AIRI 通过「场景技能 × 能力技能」把**领域知识**与**执行机制**分开。

场景声明*业务含义是什么*。能力声明*某个机制如何实现*，并锁定实现它的工具。`metric_join`
知道如何通过受限的等值 join 组合两个结构化数据源——而对企业、自然人、风控一无所知。
`enterprise → person → enterprise` 这条路径存在于场景技能里。没有任何地方会去解析"最新版"；
每个引用都是锁定的 `name@version`。

- [架构总览](docs/architecture/overview.md) —— 流水线、角色划分、指标 IR
- [设计原则](docs/architecture/design-principles.md) —— 五条边界，以及它们为什么存在
- [新增一个场景](docs/guides/adding-scenario.md) —— 第二个场景是如何加进来的

---

## 设计原则

1. **LLM 从不拥有最终 SQL。** `LLM → Metric IR → Tool → SQL`。
2. **事实与假设分离。** 统计是确定性、可重算的；反思是概率性的、仅供建议。
3. **场景知识 ≠ 执行机制。** 业务含义不进入 SQL 层。
4. **研究 ≠ 生产。** 实验通过不等于发布生产。
5. **由人治理。** 审批、晋级、发布、部署是四个各自独立的决定。

每一条的完整论证见 [设计原则](docs/architecture/design-principles.md)。

---

## 演示场景

两个在结构上完全不同的场景，走**同一条**产品工作流。这正是重点：新增一个领域付出的是场景
知识，而不是一条新流水线。

| 场景 | 需求 | 数据形态 | 能力 |
| --- | --- | --- | --- |
| **Invoice Risk** | 统计企业近30天开票金额 | single fact table + time window | `metric_sum`, `metric_window` |
| **Enterprise Relation** | 统计企业关联自然人控制的其他企业数量 | `enterprise → person → enterprise` (two hops) | `metric_join`, `metric_count` |

两者共用需求解析器、指标 IR、技能规划器、工具规划器、SQL 生成器、静态校验器、审批流程、
测试、实验引擎和 Web 界面。

### 生成的 SQL —— Invoice Risk

```sql
SELECT
    `seller_tax_no` AS entity_id,
    SUM(`invoice_amt`) AS `invoice_amount_30d`
FROM `c_db`.`source_fp_jdc_view`
WHERE
    `invoice_date` >= DATE '2026-08-10'
    AND `invoice_date` < DATE '2026-09-09'
GROUP BY `seller_tax_no`;
```

### 生成的 SQL —— Enterprise Relation

```sql
SELECT
    ep.enterprise_id AS entity_id,
    COUNT(DISTINCT pe.related_enterprise_id) AS related_enterprise_count
FROM demo.enterprise_person_relation ep
INNER JOIN demo.person_enterprise_relation pe
    ON ep.person_id = pe.person_id
WHERE
    pe.related_enterprise_id <> ep.enterprise_id
GROUP BY
    ep.enterprise_id;
```

两条 SQL 都是由同一个确定性工具从指标 IR 生成的。工作流本身从不拼接 SQL 字符串。

完整走查见 [演示指南](docs/guides/demo.md)。

---

## 截图

| | |
| --- | --- |
| ![Development](docs/screenshots/development.png) | ![Testing](docs/screenshots/testing.png) |
| **Development** —— 需求 → IR → 技能 → SQL | **Testing** —— 场景声明的检查项 |
| ![Experiment](docs/screenshots/experiment.png) | ![Reflection](docs/screenshots/reflection.png) |
| **Experiment** —— 覆盖率 / KS / IV / 提升度 | **Reflection** —— 诊断 + 候选方案 |
| ![Enterprise relation](docs/screenshots/enterprise-relation-development.png) | ![Registry](docs/screenshots/registry.png) |
| **Enterprise Relation** —— Join IR + 两跳 SQL | **Registry** —— 受治理的指标版本 |

所有截图都截取自正在运行的演示，并刻意保留 `LOCAL DEMO` / `Synthetic Data` /
`NOT PRODUCTION VERIFIED` 标记可见。更多见 [`docs/screenshots/`](docs/screenshots/)。

---

## 快速开始

**环境要求：** Python ≥ 3.12、[uv](https://docs.astral.sh/uv/)、Node.js ≥ 20.19、npm。

### Windows（已验证）

```powershell
git clone https://github.com/huangJJ8/airi.git
cd airi

.\scripts\start-demo.ps1     # migrate → seed synthetic data → start both servers
```

然后打开 <http://localhost:5173>。API 文档在 <http://localhost:8000/docs>。

停止：

```powershell
.\scripts\stop-demo.ps1
```

### macOS / Linux（尽力支持）

```bash
./scripts/start-demo.sh
./scripts/stop-demo.sh
```

Shell 脚本与 PowerShell 流程一致，但本项目**经验证的环境是 Windows**。请把 `.sh` 版本
视为尽力支持。

### Docker

```bash
docker compose up --build
```

> 开发环境中**没有可用的 Docker**。配置已提供并做了静态评审，但其运行时行为
> **未验证（NOT VERIFIED）**。

完整选项、手动安装与故障排查：[快速开始指南](docs/guides/quickstart.md)。

### 终端演示

```bash
uv run --frozen python examples/quick_demo.py
```

它会把一条需求跑完整个链路——需求、场景、指标 IR、技能、工具、SQL、审批、测试、实验、反思
——只需几秒，然后与第二个场景做对照。

---

## 配置

所有配置项都是带 `AIRI_` 前缀的环境变量。如果你想覆盖任何一项，把
[`.env.example`](.env.example) 复制为 `.env`；演示用默认值即可运行，而 `start-demo` 会
显式设置它所需的值，所以即使你的 `.env` 指向别处，演示依然可复现。

| 变量 | 演示默认值 | 含义 |
| --- | --- | --- |
| `AIRI_DATABASE_URL` | `sqlite+pysqlite:///.demo/airi_web_demo.db` | 本地演示数据库 |
| `AIRI_LLM_MODE` | `demo_mock` | 确定性的 LLM 替身，不联网 |
| `AIRI_EXECUTION_MODE` | `mock` | 在合成夹具上做 mock 执行 |
| `AIRI_DEMO_FIXTURES` | `true` | 加载随仓库提供的合成数据集 |
| `AIRI_CORS_ORIGINS` | `["http://localhost:5173"]` | 允许的开发服务器来源 |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Web 界面使用的后端地址 |

`AIRI_LLM_MODE=demo_mock` 是一个**刻意配置的显式替身**——不是回退。把它指向真实的
OpenAI 兼容端点后，一个非法或违反 schema 的响应会直接报错；AIRI 不会悄悄降级到 mock。

---

## 项目结构

```text
airi/
├── src/airi/
│   ├── agents/           requirement parser (+ prompts)
│   ├── api/              FastAPI routers (14 modules)
│   ├── approvals/        content-addressed approval + canonical hashing
│   ├── core/             settings, schemas, exceptions
│   ├── environments/     Spark test environment acceptance
│   ├── evaluation/       coverage / KS / IV / lift calculator
│   ├── execution/        read-only execution + sandbox guard
│   ├── experiments/      experiment specs, runs, fixtures
│   ├── infrastructure/   database, LLM clients, query executors, fixtures
│   ├── metric_ir/        Metric IR models, joins, semantics, validation
│   ├── observability/    structured logging
│   ├── production/       adapters, identity, verification, reconciliation
│   ├── refinement/       bounded candidate refinement
│   ├── reflection/       reflection policy, models, workflow
│   ├── registry/         metric versions and audit events
│   ├── skills/           scenario + capability skills  ← domain knowledge
│   ├── temporal/         PSI, OOT slice validation
│   ├── testing/          test rules, probes, reports
│   ├── tools/            deterministic SQL generators  ← mechanism
│   └── workflows/        development / testing / experiment / reflection / ...
├── airi-web/             Vue 3 + TypeScript + Vite + Element Plus + ECharts
├── examples/             demo_phase*.py + quick_demo.py
├── scripts/              start/stop demo, seed, safety scan
├── docs/
│   ├── architecture/     overview, design principles
│   ├── guides/           quickstart, demo, adding-scenario, api, release checklist
│   ├── screenshots/      captured from the running demo
│   ├── demo/             demo GIF / MP4
│   ├── history/          phase-by-phase implementation reports
│   ├── interview/        pitch, Q&A, architecture walkthrough, demo script
│   └── portfolio.md      design rationale, interview form
├── tests/                backend test suite
├── migrations/           alembic (head: 0011_operational_convergence)
└── pyproject.toml
```

---

## API

FastAPI 在 <http://localhost:8000/docs> 提供交互式参考文档（另有 `/redoc`、
`/openapi.json`）。共 116 个路由。

承载业务含义的领域端点：

| 领域 | 端点 |
| --- | --- |
| **Development** | `POST /api/v1/development/generate`, `GET /api/v1/development/artifacts/{id}` |
| **Approvals** | `POST /api/v1/approvals`, `POST …/approve`, `POST …/reject` |
| **Testing** | `POST /api/v1/tests/run`, `GET /api/v1/tests/{id}` |
| **Experiments** | `POST /api/v1/datasets`, `/labels`, `/experiments`, `POST …/run` |
| **Reflection** | `POST /api/v1/reflections`, `GET …/proposals`, `POST …/decision` |
| **Registry** | `POST /api/v1/metric-versions`, `GET …/active`, `/versions`, `/events`, `POST …/rollback` |
| **Runtime facts** | `GET /health`, `GET /api/v1/meta` |

错误会带上机器可读的 `error_code`（例如 `requirement_parse_failed`、
`artifact_hash_mismatch`）；治理冲突返回 `409`，界面渲染后端给出的错误码，而不是自己编一个。

详细指南：[API 指南](docs/guides/api.md)。

---

## 测试

```bash
# backend
uv sync --frozen --extra dev
uv run --frozen pytest
uv run --frozen ruff check src tests scripts examples
uv run --frozen ruff format --check src tests scripts examples

# migrations (offline, SQLite)
AIRI_DATABASE_URL="sqlite+pysqlite:///.demo/check.db" uv run --frozen alembic upgrade head
AIRI_DATABASE_URL="sqlite+pysqlite:///.demo/check.db" uv run --frozen alembic check

# frontend
cd airi-web && npm ci && npm run build && npm run test
```

当前基线：**675 个后端测试通过 / 14 个跳过**、**28 个前端测试通过**、`ruff` 干净、
alembic head 为 `0011_operational_convergence`，模型与迁移之间无漂移。

被跳过的测试是需要真实基础设施的集成测试套件（Spark/Hive、MySQL、生产身份、遥测）。
CI 离线运行其余全部内容。见 [发布检查清单](docs/guides/release-checklist.md)。

---

## <a id="roadmap"></a>路线图

AIRI v1.0 在设计上就是功能完备的。后续工作：

- [ ] **有界自主研究** —— 让 AI 在沙箱内自主生成并检验假设；影响生产的决定仍然受治理
- [ ] **实验预算** —— 对每个指标的研究投入设定上限
- [ ] **更多场景包** —— 更多领域，同一条流水线
- [ ] **可选的 Spark/Hive 运行时** —— 由运维验证的集成
- [ ] **企业 IAM 集成**

---

## <a id="limitations"></a>限制与已知不足

如实说明，因为它们确实重要：

- **只有合成数据。** 所有随仓库提供的数据集都在仓库内生成。它演示的是流水线；
  它**不是**风控证据，其中的统计数字对真实预测能力没有任何说明力。
- **未经生产验证。** Spark/Hive 与生产适配器在代码中存在，并在边界层被测试到，但在本仓库中
  它们**未针对真实环境验证**。把它变成已验证状态是一个由人、由运维驱动的手工流程——见
  [真实环境检查清单](docs/guides/real-environment-checklist.md)。
- **默认模式是离线的。** SQLite、mock 执行、确定性的 LLM 替身。不需要 API key，不需要集群，
  不需要内网。
- **反思是可选的**，且没有接入自动精炼。
- **有一个标识符是刻意不透明的。** invoice 夹具引用了一个看起来像内部 schema 的表名
  （`c_db.source_fp_jdc_view`）。它是一个合成占位，背后没有任何真实数据，保留它是为了不让
  175 份历史产物失效；关联场景使用的是中性的 `demo.*` 命名。
- **取值命名偏研究导向。** 阈值候选是研究候选，不是推荐的生产阈值。

---

## 安全与数据

- 所有随仓库提供的演示数据都是合成的——不含真实企业、自然人、发票或标识符。
- 仓库中**不含**任何凭据、token、内部主机或私钥。有一个护栏脚本在强制检查，并运行在 CI 中：

  ```bash
  uv run --frozen python scripts/check_open_source_safety.py
  ```

- 生成的 SQL 在可以被批准之前，必须先通过静态校验器（白名单语法），且执行沙箱是只读的。
- 身份提供方与生产适配器**默认处于惰性状态**，并且失败时关闭（fail closed）。

报告漏洞前请先阅读 [SECURITY.md](SECURITY.md)。

---

## 仓库状态

这是一个**作品集 / 研究性质的实现**。克隆时有两点需要注意：

- 上方的 CI 徽章读取的是
  [`.github/workflows/ci.yml`](.github/workflows/ci.yml) 的**实时**状态。它反映最近一次
  运行实际做了什么——它不是静态图片，也不是关于生产可用性的任何声明。
- 本地演示模式运行在合成数据上。企业适配器是可选的，且未经验证。

这个徽章**不**意味着什么：见 [限制与已知不足](#limitations)。

---

## 参与贡献

欢迎贡献。最有价值的贡献通常是一个**新场景**——而那份指南同时也是对架构最好的解释：

- [CONTRIBUTING.md](CONTRIBUTING.md) —— 环境搭建、测试、新增场景 / 能力技能、PR 检查清单
- [新增一个场景](docs/guides/adding-scenario.md) —— 完整示例，以及要避免的反模式

有两条规则比其余的更重要：**不存在 LLM 直连 SQL 的路径**，以及**业务含义永不进入 SQL 层**。

---

## 工程历史

AIRI 是分阶段构建的，每个阶段都有一份在开发当时写下的实现报告——包括那个时点**尚未**
验证的内容。

| 阶段 | 确立了 |
| --- | --- |
| 1.5 – 2.5 | 第一个端到端切片、受控执行、真实环境集成代码 |
| 3 – 3.5 | 实验闭环、证据驱动的反思 |
| 4 – 5 | 受控精炼、时间 / OOT 验证 |
| 6 – 9 | 版本化与注册表、生产集成、验证、受治理的收敛 |
| 10 | Web MVP（后端功能冻结） |
| 11 | 多场景验证 —— `New Scenario ≠ New Workflow` |
| 12 | 开源与作品集发布 —— README、一键演示、CI、Docker 配置、演示 GIF、v1.0.0 |

完整索引与摘要：[`docs/history/`](docs/history/README.md)。

---

## 许可证

[Apache License 2.0](LICENSE)。

所有随仓库提供的演示数据均为合成数据。
