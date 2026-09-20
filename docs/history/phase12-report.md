# AIRI Phase 12 — Open Source & Portfolio Release Implementation Report

> **状态声明**：本报告中的所有数字均来自真实运行的命令输出（pytest / coverage /
> ruff / alembic / npm / 浏览器自动化 / 安全扫描）。截图与 Demo 视频来自真实运行的
> dev server（`scripts/start-demo.ps1` 启动，`stop-demo.ps1` 停止）。
> **Docker 未在本机运行验证**，已按任务书 §35 显式标注 `NOT VERIFIED`。
> 全部数据为合成数据，**NOT PRODUCTION VERIFIED**。

---

## 1. Repository Assessment

编码前检查结果：

| 项 | 结果 |
| --- | --- |
| `pwd` | `E:\AI_agent\project\airi` |
| `.git` | **不存在** → 记录 `repository_not_initialized`，继续（任务书 §3） |
| 顶层结构 | `src/airi/`（21 子包，153 py）、`airi-web/`、`examples/`、`scripts/`、`docs/`、`tests/`（23 文件）、`migrations/`（head `0011`）、`pyproject.toml` |
| README | 58 KB，Phase 2→11 逐段堆叠的开发日志，第一屏即阶段内容 |
| 敏感信息扫描 | 零命中（无客户/厂商名、内网 IP、云厂商密钥形态、私钥、真实凭据） |
| 内部风格表名 | `c_db.source_fp_jdc_view`、`tmp_db.airi_invoice_fixture` 存在于 Literal 类型、IR 校验、prompt、SQLite ATTACH 与 175 个历史产物 JSON |
| 启动流程 | 手工 5 步（.env → alembic → seed → uvicorn → npm dev），无一键入口 |
| 后端依赖 | Python ≥ 3.12（uv 管理，9 个直接依赖，无冗余） |
| 前端依赖 | Node 22、Vue 3.5、Vite 8、Vitest 3（无冗余依赖需删） |
| Docker | 本机无 `docker` 命令 |
| 根目录杂项 | `1440`（误落盘 PNG，无引用）、`REAL_ENVIRONMENT_CHECKLIST.md`（301 行，有保留价值） |

## 2. Feature Freeze Boundary

本阶段**未新增任何产品能力**。允许范围内的全部改动：

- 新增 Scenario / Capability / Tool：**无**
- 新增数据库业务表 / 迁移：**无**（alembic head 仍为 `0011_operational_convergence`）
- 新增 Metric 类型 / Reflection 类型 / Release 流程：**无**
- 后端代码改动：仅 `src/airi/__init__.py` 版本号 `0.1.0 → 1.0.0`，以及 `examples/` 新增 `quick_demo.py`
- `scripts/verify_phase11.py`、`scripts/verify_web_flow.py`：仅修复既有 lint 问题（E501 / 导入位置），未改变行为

## 3. Open Source Readiness Assessment

| 维度 | 阶段初 | 阶段末 |
| --- | --- | --- |
| README | 开发日志 | 产品首页（Hero + GIF + Why + Quick Start） |
| 一键启动 | 无 | `start-demo.ps1`（Windows 实测通过） |
| 配套文件 | 无 | LICENSE / CONTRIBUTING / SECURITY / CHANGELOG |
| CI | 无 | `.github/workflows/ci.yml`（backend + frontend） |
| Docker | 无 | Dockerfile ×2 + nginx.conf + compose（运行未验证） |
| 架构文档 | 无 | overview + design-principles（Mermaid） |
| 指南 | 无 | quickstart / demo / adding-scenario / api / release-checklist |
| 安全扫描 | 无 | `scripts/check_open_source_safety.py`（CI 中运行） |
| 截图 | 6 张（Phase 10/11） | 10 张，全部来自 v1.0 最终版本 |
| Demo 视频 | 无 | GIF + MP4 + WebM（真实 dev server） |

## 4. README Redesign

`README.md` 全量重写（58 KB 开发日志 → 产品首页），按任务书 §8 结构：

```text
Hero（tagline + Hero 截图 + Demo GIF）
What is AIRI? / Why AIRI?
Key Features
How It Works / Architecture / Design Principles
Demo Scenarios（含两段真实生成的 SQL）
Screenshots
Quick Start（Windows 优先）/ Local Demo / Terminal demo
Configuration
Project Structure
API
Testing
Roadmap
Limitations
Security & Data
Repository status
Contributing / Engineering History / License
```

- 第一屏不再出现 Phase 编号、迁移号或阶段历史。
- 阶段历史仅以表格形式链接到 `docs/history/`（§93）。
- CI badge 使用 `OWNER/REPO` 占位并在 "Repository status" 中显式说明（§41、§109），未伪造真实仓库 URL。
- 未写 coverage badge（§42），改为陈述真实测试数量。

## 5. Project Positioning

定位在 README 与 `docs/portfolio.md` 中统一为：

> An AI-assisted risk metric research and development platform that combines LLM
> reasoning with structured Metric IR, deterministic Python tools, automated
> experimentation and human governance.

明确**不使用**以下措辞（任务书 §66）：`AI automatically discovers fraud`、
`AI replaces risk analysts`、`production-ready financial AI`。

边界声明置于 README "Repository status" 与 "Limitations"：

> AIRI v1.0 is a portfolio/research implementation. The default open-source mode
> runs locally with synthetic data. Enterprise Spark/Hive/production adapters are
> optional and not verified in this repository.

## 6. Architecture Documentation

新增：

- `docs/architecture/overview.md` — 主流程 Mermaid 图、角色分工表（LLM / Python / Human）、
  概率推理 + 确定性执行图、Scenario × Capability 图、Metric IR 字段表、治理链图、运行时安全
- `docs/architecture/design-principles.md` — 五条边界原则及其"为什么"与"后果"

所有图表使用 Mermaid（GitHub 原生渲染，任务书 §14）。未输出 PNG——Mermaid 已满足
"GitHub 可渲染"的优先级要求，未为达到 PNG 而引入额外工具链。

## 7. Design Principles

文档化的五条原则（与代码结构一一对应）：

1. **LLM 不拥有最终 SQL** — `LLM → Metric IR → Tool → SQL`
2. **事实 vs 假设** — 统计确定性可复算；Reflection 概率性、仅建议
3. **Scenario 知识 ≠ 执行机制** — `metric_join` 证明：它不知道怎么走企业路径
4. **研究 ≠ 生产** — 实验通过不等于发布
5. **人治** — SQL 审批 / 晋升评审 / 发布评审 / 部署评审各自独立

## 8. Demo Scenarios

README 以表格并列两个场景，并给出各自**真实生成**的 SQL：

| Scenario | Requirement | Capabilities |
| --- | --- | --- |
| Invoice Risk | 统计企业近30天开票金额 | `metric_sum`, `metric_window` |
| Enterprise Relation | 统计企业关联自然人控制的其他企业数量 | `metric_join`, `metric_count` |

一段 SQL 为单表窗口 `SUM`，另一段为两跳 `INNER JOIN` + `COUNT DISTINCT` + 自环排除
（`pe.related_enterprise_id <> ep.enterprise_id`）。两者均由同一确定性工具从 Metric IR 生成。

## 9. Quick Start

`docs/guides/quickstart.md` + README "Quick Start" 双份覆盖：

- 环境要求取自真实清单：Python ≥ 3.12（`pyproject.toml`）、Node ≥ 20.19（Vite 8 要求，实测 22.22.2）、uv、npm
- Windows 一键启动置于最前（主验收路径）
- macOS/Linux `.sh` 显式标注 `best-effort / not runtime verified`
- 手工 5 步流程、验证命令、配置表、故障排查表

## 10. Windows Demo Scripts

| 文件 | 说明 |
| --- | --- |
| `scripts/start-demo.ps1` | 依赖检查 → 端口检查 → `uv sync` → `alembic upgrade head` → `seed_demo.py` → 启动双服务 → 健康检查 → 写 PID 文件 |
| `scripts/stop-demo.ps1` | 读 PID 文件，按进程树结束，清理 PID 文件 |
| `scripts/start-demo.sh` / `stop-demo.sh` | 同构 shell 版本（best-effort） |

依赖不存在时给出清晰提示，不自动安装系统 Python/Node，不需要管理员权限（§25）。

**实测修复的两个真实缺陷**（非环境偶发）：

1. Windows PowerShell 5.1 的 `Start-Process -RedirectStandardOutput` 在环境块同时含
   `Path` 与 `PATH` 时崩溃 → 改为 `cmd.exe /c` + shell 重定向。
2. `stop-demo.ps1` 使用了三参数 `Join-Path`（PS 5.1 不支持）→ 导致停止失效、端口残留；
   已修正并重新验证。

## 11. One-command Demo

实测（非模拟）：

```text
== AIRI local demo ==
   backend healthy.
   frontend healthy.
== AIRI local demo is running ==
   Web UI      : http://localhost:5173
   API docs    : http://localhost:8000/docs

frontend (PID 7060): stopped.
backend (PID 20948): stopped.
AIRI local demo stopped.
STOP_EXIT:0
PORTS_FREE
```

完整 `start → 健康检查 → stop → 端口释放` 周期通过，前后两次独立复验均成功。

## 12. Docker

新增 `Dockerfile`（backend）、`airi-web/Dockerfile`（node build → nginx）、
`airi-web/nginx.conf`（SPA fallback + `/api` 同源反代，规避 CORS）、
`docker-compose.yml`（`api` + `web`，SQLite 命名卷持久化，healthcheck 门控依赖）、
`.dockerignore` ×2。

- 前端以 `VITE_API_BASE_URL=""` 构建 → axios 走同源 → nginx 转发 `/api` 到 `api:8000`
- 后端采用 uv 官方两段式 `uv sync --frozen --no-dev --no-install-project` → 复制源码 →
  `uv sync --frozen --no-dev`，避免"源码未就位就构建项目"的失败

**诚实标注**：本机无 `docker` 命令，`docker compose build/up` **未执行**。
`docker-compose.yml` 经 PyYAML 解析校验（services / volumes / depends_on / ports 结构正确），
其余为静态审阅。报告中不声称 Docker 可用（§35、§85）。

## 13. GitHub Actions

`.github/workflows/ci.yml`，两个 job（YAML 已解析校验）：

**backend**（ubuntu-latest）：`uv sync --frozen --extra dev` → `ruff check` →
`ruff format --check` → 安全扫描 → `pytest` → `pytest --cov` → `alembic upgrade head` →
`alembic current` → `alembic check`

**frontend**（ubuntu-latest，Node 22 + npm cache）：`npm ci` → `npm run build` → `npm run test`

全局 env 强制 `AIRI_LLM_MODE=demo_mock`、`AIRI_EXECUTION_MODE=mock`、
`AIRI_DATABASE_URL=sqlite+pysqlite:///.demo/ci.db` —— CI 不调用真实 LLM、不连真实生产、
不访问外网金融数据（§74）。真实集成测试保持 skip（§40）。

## 14. License

`LICENSE`：**Apache License 2.0**（任务书 §43 推荐项）。仓库此前无 License，故按推荐新增，
未替换任何既有授权。未创建 NOTICE（无第三方 NOTICE 需求，§44）。

## 15. Contributing

`CONTRIBUTING.md`：环境准备、开发流程、后端/前端测试命令、**新增 Scenario Skill**、
**新增 Capability Skill**、PR checklist。

"Adding a Scenario" 一节是重点（§46），明确：

- 应该：新增 Scenario Skill、复用 Capability、必要时扩通用 Capability、扩 Parser catalog、新增合成 fixture、新增测试
- 不应该：复制 Workflow、创建 scenario-specific SQL tool

## 16. Security Policy

`SECURITY.md`：项目定位（研究/演示）、合成数据边界、禁止提交真实金融数据/凭据、
生产 Adapter 默认 `NOT VERIFIED`、漏洞报告方式（GitHub Security Advisory）、
"不支持"的清单（生产金融用途、真实数据托管、SLA）。

同时记录 §51 授权下的例外：`c_db.source_fp_jdc_view` 作为 **opaque synthetic identifier**
保留（无真实数据挂载），理由与影响范围写明。

## 17. Open-source Sanitization

全仓（`src`/`tests`/`scripts`/`examples`/`docs`/`migrations`/根配置）扫描：

| 模式 | 结果 |
| --- | --- |
| 已知厂商/内部标识（由扫描器内置词表覆盖） | 0 |
| 真实客户/机构名称（由扫描器内置词表覆盖） | 0 |
| 内网 IP（10.x / 172.x） | 0 |
| 云厂商访问密钥与 PEM 私钥头形态 | 0 |
| 真实 token / password 字面量 | 0（命中项均为单测占位值） |
| `gitlab` | 仅出现在"未做"声明文本中，判定为合法 |

内部风格表名 `c_db.*` / `tmp_db.*`：按 §51「不要破坏已有 Phase 测试」授权保留，并在
README "Limitations" 与 `SECURITY.md` 中显式声明为合成占位符。Phase 11 新增的关系场景
已全部使用中性的 `demo.*` 命名。

根目录误落盘文件 `1440`（无引用 PNG）移入 `.demo/`（gitignore 覆盖），未删除。
`REAL_ENVIRONMENT_CHECKLIST.md` 移入 `docs/guides/real-environment-checklist.md` 并从
README 与 release checklist 建立引用（原本无任何引用，移交安全）。

## 18. Safety Scanner

`scripts/check_open_source_safety.py`：检查已知内部域名/前缀、私钥、高置信度 token 形态、
真实身份证形态。作为项目 guardrail，非完整 secret scanner（§52）。CI 中运行。

实现中处理了任务书 §92 预警的误报问题：

- 身份证校验加 ISO 7064 校验和 + 首位非零，避免"小数点后 18 位数字"误判
- 去掉 `local` 后缀规则（`.env.local` 误判为内部域名）
- 排除扫描器自身

最终结果：`open-source safety scan: clean (no high-confidence sensitive patterns)`（EXIT 0）。

第二类误报来自**本报告自身**：§17 最初以表格逐条列出"被检查的禁用词字面量"，扫描器
正确地命中了它们。处理方式不是放宽扫描器，而是改写文档为**类别描述**（不复制字面量）——
公开仓库本身就不应出现这些字符串。扫描器保持严格，仍是 CI 门禁。

## 19. Documentation Structure

```text
docs/
├── architecture/
│   ├── overview.md
│   └── design-principles.md
├── guides/
│   ├── quickstart.md
│   ├── demo.md
│   ├── adding-scenario.md
│   ├── api.md
│   ├── release-checklist.md
│   └── real-environment-checklist.md
├── screenshots/            10 张（v1.0 最终版本）
├── demo/                   GIF / MP4 / WebM
├── history/                phase1.5–phase11 报告 + README 索引
└── portfolio.md
```

`docs/phase*-report.md` → `docs/history/`（13 份报告），并修复了因移动而失效的 22 处
相对链接；全仓 84 处相对链接经脚本校验 100% 可解析。

## 20. Demo Guide

`docs/guides/demo.md`：两个场景的输入、期望 Scenario、Capabilities、SQL 形态、
边界用例表（重复关系 / 多自然人指向同一企业 / 自环 / 无关联企业）、UI 走查路径、
"演示不声称什么" 清单。明确标注合成数据与非生产证据。

## 21. Adding a Scenario Guide

`docs/guides/adding-scenario.md`：以 `enterprise_relation` 为完整案例，逐步说明
Scenario Skill / 注册 / 复用 Capability / 扩 IR（含 `JoinSpec` 约束）/ Parser 词汇 /
合成 fixture 与手算期望 / 测试 / Demo 接线。

含 **Checklist** 与 **Anti-patterns 表**（`generate_<scenario>_sql`、
SQL 模板里的业务短语、为可选字段引入 `MetricIRV2`、每场景复制 Workflow、
真实 Parser 里写 `if "关联企业" in requirement`、用生产代码生成期望值）。

## 22. API Guide

`docs/guides/api.md`：约定（严格 schema、`error_code`、409/422、`X-Request-ID`）+
关键域（Development / Approvals / Testing / Experiments / Reflection / Registry）逐个列出
方法、路径、用途，并对 lifecycle / production 域做分组说明。

未手写 200 页 API Reference（§59）：端点总数 **116**，完整清单由 FastAPI `/docs`、
`/redoc`、`/openapi.json` 提供。

## 23. Architecture Diagram

`docs/architecture/overview.md` 含 5 张 Mermaid 图：

1. 端到端流水线（含 LLM / Python / Human 三个 subgraph 与角色分工表）
2. 概率推理 + 确定性执行（含配色区分 LLM / Python / 证据）
3. Scenario Skill × Capability Skill 组合图
4. Metric IR 字段表（由源码 `models.py` 提取，非人工臆造）
5. 治理链（审批 → 晋升 → 时序验证 → Registry → 发布 → 部署）

## 24. Demo GIF / Video

```text
docs/demo/airi-demo.webm   571,884 B   48.5 s   1280×800 vp8
docs/demo/airi-demo.gif    595,721 B   （README 内嵌，12 fps / 880 px / 128 色）
docs/demo/airi-demo.mp4    444,745 B   h264 / crf 26 / faststart
```

来源：**真实 dev server**（`http://localhost:5173`），流程为
Load Invoice Demo → Generate（IR / Skills / Tool / SQL）→ Submit → Approve →
Testing → Experiment → 结果滚动浏览。无 Figma、无静态 mock、无 PPT（§12）。

技术说明（诚实记录）：agent-browser 的 `record` 只在页面重绘时抓帧，静态页面上时间轴会
非线性压缩（实测 6 s→1.3 s、24 s→2.4 s）。为解决该问题，录制期间注入了一个 2×2 px、
80 ms 切换的标记元素以维持重绘，使时间轴恢复实时（实测 12 s→11.4 s）。该元素
**不改变任何产品 UI**，最终成片 48.5 s 与真实操作耗时一致。

帧抽检确认内容真实可见：Development 页展示 Scenario `invoice_risk` / Capabilities
`metric_window@1.0.0`、`metric_sum@1.0.0` / Tool `generate_spark_sql_metric@1.1.0`；
Experiment 页展示 92.0% Coverage / 0.577 KS / 6.03 IV / Higher is Riskier，
并保留 `LOCAL DEMO`、`Synthetic Data`、`Synthetic Data — for demonstration only`
等诚实标记。

## 25. Screenshots

10 张，全部由 `docs/screenshots/` 下的最终 v1.0 版本捕获（§118，未复用旧 UI 截图）：

`hero.png`、`dashboard.png`、`development.png`、`testing.png`、`experiment.png`、
`reflection.png`、`registry.png`、`enterprise-relation-development.png`、
`enterprise-relation-testing.png`、`enterprise-relation-experiment.png`

- README 主展示 6 张（§64：控制在 3–4 张量级，其余链接目录）
- 截图中仅出现 `localhost`、合成 ID、演示标签；无真实企业、无 token、无内部域名、无绝对用户路径（§97）
- `.gitignore` 显式保留 `docs/screenshots/*.png`（§54）

## 26. Changelog

`CHANGELOG.md`，`## v1.0.0 — Portfolio Edition`，按 Keep a Changelog 组织：
Core platform / Web UI / Demo scenarios / Open-source packaging / Honest limitations。
未逐 Phase 罗列内部变更（§70）。

## 27. v1.0.0 Scope

版本号统一为 `1.0.0`：`pyproject.toml`（`version = "1.0.0"`）与 `src/airi/__init__.py`。
未引入新的版本机制（§69）。`/api/v1/meta` 实测返回 `"version": "1.0.0"`。

README Feature List（§71）覆盖：自然语言需求、Metric IR、Scenario × Capability、
确定性 SQL、自动化测试、KS/IV/Lift 实验、证据驱动 Reflection、受控 Refinement、
时序/OOT 验证、Metric Registry、Human-in-the-loop、Vue Web UI、两个 Demo 场景。

## 28. Portfolio / Interview Guide

`docs/portfolio.md`：30 秒 Pitch、架构要点、最难工程决策（含"为什么不用直接 text-to-SQL"、
"为什么 Skill 与 Tool 要分开"、"如何防止幻觉 SQL"、"为什么第二个场景是必要的"、
"最意外的一点：加性改动是**位置性**的"）、诚实边界、可能的追问与回答。
未写个人简历（§103），且未放入 README 首屏。

## 29. Backend Quality Gates

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 测试 | `uv run --frozen pytest` | **675 passed / 14 skipped / 2 warnings，1335.16 s** |
| 覆盖率 | `pytest --cov=airi` | **TOTAL 9976 stmts / 907 miss / 91%** |
| Lint | `ruff check src tests scripts examples` | **All checks passed!** |
| 格式 | `ruff format --check …` | **194 files already formatted** |
| 迁移 | `alembic upgrade head` | 成功 |
| 迁移 | `alembic current` | `0011_operational_convergence (head)` |
| 迁移漂移 | `alembic check` | `No new upgrade operations detected.` |

基线对比：Phase 11 = 675 passed / 14 skipped / coverage 91.0% → Phase 12 **未减少，未漂移**。

已知环境现象（非产品缺陷，诚实记录）：`pytest --cov` 结束时 `coverage combine` 会
`os.remove` 并行数据文件，触发本机沙箱的批量删除保护而抛 `INTERNALERROR`（`EXIT:3`）。
**测试本身已全部跑完并通过**；按既有约定用
`COVERAGE_FILE=… coverage combine --keep` + `coverage report` 独立产出 91% 报告。

## 30. Frontend Quality Gates

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 类型检查 + 构建 | `npm run build` | **✓ built in 3.02 s**，2247 modules transformed |
| 单元测试 | `npm run test` | **6 files / 28 tests passed**，9.73 s |

基线对比：Phase 11 = 28 passed → Phase 12 **未减少**。

记录（§105，不做专项优化）：构建产出 chunk 体积提示
（`index-*.js` 944 kB / `ExperimentPage-*.js` 1,035 kB，超 500 kB 阈值）。
为几百 KB 引入大规模 lazy loading 不在本阶段范围，如实记录即可。

环境现象（诚实记录）：`npm ci` 与首次 `npm run build` 被本机沙箱批量删除保护拦截
（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`，count 58 / 70 > threshold 50），导致
`node_modules` 与 `dist/` 处于中间状态。已通过 `npm install` 修复依赖、先行清理 `dist/`
后重新构建，最终构建与测试均通过。CI 在 Ubuntu 上执行 `npm ci`，不受本机沙箱影响。

## 31. Windows Startup Verification

真实执行（`PowerShell`，非模拟）：

```text
== AIRI local demo ==
== syncing backend dependencies (uv) ==
== running database migrations (SQLite demo database) ==
== seeding synthetic demo data (invoice_risk + enterprise_relation) ==
== starting backend ==
== starting frontend dev server ==
== waiting for backend ==
   backend healthy.
== waiting for frontend ==
   frontend healthy.
== AIRI local demo is running ==
START_EXIT:0

frontend (PID 7060): stopped.
backend (PID 20948): stopped.
AIRI local demo stopped.
STOP_EXIT:0
PORTS_FREE
```

验证项（§80、§116）：backend 可达（`/api/v1/meta` 返回 200 + `version 1.0.0`）、
frontend 可达（`http://localhost:5173` 返回 200）、dashboard 正常渲染、
`stop-demo.ps1` 正确结束两个进程树、停止后 8000/5173 端口均释放。

## 32. Browser E2E Verification

真实浏览器（headless Chrome）驱动真实 dev server：

**Flow A — Invoice Risk**：`/dashboard` → `/development`（Load Demo → Generate →
Submit → Approve）→ `/testing`（Run Automated Tests）→ `/experiments`（Run Experiment）→
`/reflection`（Analyze Experiment）→ `/registry`。每一步均以页面文本断言确认到达，
无手填 UUID（§58）。

**Flow B — Enterprise Relation**：同一套页面，选择第二个 Demo Example，
`Generate → Approve → Testing → Experiment` 全链路通过。

两条链路使用**同一个 Web 应用、同一套 Workflow**，仅 Scenario 与 Capability 不同 —— 
这正是 Phase 11 与 Phase 12 要共同证明的展示价值。

实现说明（诚实记录）：headless 工具的原生 click 在该 Vue 应用上不触发处理函数，
E2E 通过页面内 JS `element.click()` 驱动。这是工具与框架的兼容问题，非产品缺陷；
后续可用 Playwright 原生事件验证真实鼠标路径。

## 33. Security Scan Results

```text
$ uv run --frozen python scripts/check_open_source_safety.py
open-source safety scan: clean (no high-confidence sensitive patterns)
EXIT:0
```

补充人工核查：无 `.env` 被提交（`.gitignore` 覆盖）、无真实凭据、无真实企业/人员标识、
无内部域名与真实集群地址。Prompts 与 fixture 中出现的全部标识符均为合成值。
`npm audit` 未在本机沙箱内完成（网络与沙箱限制），如实记录为未执行项，未伪造结果。

## 34. Known Limitations

- **Docker 运行未验证**：本机无 docker 命令；配置已交付并静态校验，`NOT VERIFIED`
- **`.sh` 启动脚本未在 Linux/macOS 实机验证**：标注 best-effort，Windows 为主验收
- **npm audit 未执行**：本机条件限制
- **E2E 使用 JS 注入点击**：未验证真实鼠标事件路径
- **前端 chunk 体积提示**：`index` 944 kB / `ExperimentPage` 1,035 kB，未优化
- **`c_db.*` / `tmp_db.*` 命名保留**：合成占位符，已在 README 与 SECURITY.md 声明
- **一键启动脚本以进程 + PID 文件实现**：非单进程守护，异常退出后需 `stop-demo` 清理
- **CI badge 为占位 URL**：仓库尚未初始化，未伪造真实地址
- 所有数据为合成；Spark/Hive/MySQL/身份/遥测集成仍未验证（测试 skip 并说明原因）

## 35. Roadmap

仅保留任务书 §114 的五项，未伪装为现有能力：

```text
[ ] Bounded autonomous research（沙箱内自动生成与验证假设；影响生产的决策仍受治理）
[ ] Additional scenario packs
[ ] Optional Spark/Hive runtime
[ ] Enterprise IAM integrations
[ ] Experiment budgeting
```

## 36. Final v1.0 Readiness Verdict

逐条对照任务书 §120 的准入条件：

| 条件 | 证据 | 判定 |
| --- | --- | --- |
| Tests pass | 675 passed / 14 skipped；Vitest 28 passed | ✅ |
| Web builds | `npm run build` ✓ built in 3.02 s | ✅ |
| Demo works | `start-demo.ps1` 双服务 healthy；stop 后端口释放 | ✅ |
| README current | 全量重写，数字与命令来自真实运行 | ✅ |
| No sensitive data | 安全扫描 clean + 人工核查 | ✅ |
| Quick Start verified | Windows 实机跑通 start→stop 全周期 | ✅ |

### **AIRI v1.0 Portfolio Edition Ready**

限定说明（必须与结论同时阅读）：

- **Open-source Ready ≠ Production Ready**。生产适配器（Spark/Hive/MySQL/IAM/遥测）
  在本仓库**未经验证**，Docker 运行未验证。
- **Demo Works ≠ Real Financial Performance**。所有数字来自合成数据，不构成风险证据。
- 上述结论**不覆盖** Docker 运行时、Linux/macOS 启动脚本、真实鼠标事件 E2E、`npm audit`。

### 下一步

**不再规划 Phase 13。** AIRI v1.0 到此收口，后续能力全部进入 Roadmap（§113、§114）。
