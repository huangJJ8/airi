# 变更日志

AIRI 的所有重要变更都记录在此。格式基于 [Keep a Changelog](https://keepachangelog.com/)。

## v1.0.0 — 作品集版

首次公开发布。AIRI 是一个 AI 辅助风控指标研发平台：自然语言需求会变成结构化 Metric IR、确定性 SQL、自动化测试、实验、反思以及受治理的指标版本。**LLMs reason. Python verifies. Humans govern.**（模型负责推理，Python 负责验证，人负责治理。）

### 核心平台

- **需求理解**：自然语言风控需求被解析为经过严格校验的 Pydantic Metric IR（原子指标 + 派生指标，多源指标可选 Join IR）。歧义或跨域需求会被拒绝，绝不作猜测。
- **Scenario Skill × Capability Skill 架构**：领域知识（实体、数据源、业务规则）位于场景技能中；执行机制（聚合、窗口、join、SQL 生成）位于可复用、带版本的能力技能中。新增场景只是新增知识，而不是新增工作流。
- **确定性 SQL 生成**：SQL 由带版本的 Python 工具和 Jinja 模板从 Metric IR 产出 —— LLM 从不编写最终 SQL。每个产物都由严格的静态 SQL 校验器校验（数据源、字段、join 类型均在白名单内；不允许子查询、注释、DDL 或 DML）。
- **人在环治理**：SQL 审批为执行上闸；指标版本、发布、晋级、部署和回滚各自有独立的评审。评审人身份由调用方声称（demo 级，无认证）。
- **自动化指标测试**：基于确定性探针的测试，配以独立的 Decimal 参考实现（schema、空值处理、重复、窗口语义、join 正确性、distinct 语义、自关联排除、对账）。
- **指标实验**：在已注册的数据集快照和标签定义上计算 KS / IV / Lift / Coverage / bad rate，并给出阈值候选与风险方向。所有统计量都是确定性 Python。
- **证据驱动的 LLM 反思**：反思只能引用已持久化的实验报告中的证据；提案会依据白名单校验，并且始终需要人工评审。事实保持确定性，只有解释是概率性的。
- **受控精炼（refinement）**：被接受的提案通过与治理同一条流水线产出候选指标（新 IR → 新 SQL → 新审批 → 新测试 → 基线对比）。不会有任何东西被悄悄替换。
- **时序验证**：多切片历史验证 + 跨期（out-of-time）验证，配以基于 PSI 的稳定性诊断和冻结阈值迁移。
- **指标注册表与受控发布**：不可变、内容哈希的指标版本；预发 / 影子校验；发布评审；CAS 激活；带完整审计事件历史的两阶段回滚。
- **运行可靠性语义**：期望状态、注册表指针与运行时状态保持区分；收敛是被观察到的，而非被假定的；告警不等于通知；对账只检测、绝不自动纠正。

### Web UI

- Vue 3 + TypeScript + Element Plus + ECharts 前端（`airi-web/`），共六个页面：Dashboard、Metric Development、Metric Testing、Experiment、Reflection & Refinement、Metric Registry。
- 浏览器执行**零**业务计算，从不调用 LLM，并原样呈现后端的治理错误（409 状态码）。

### Demo 场景

- **Invoice Risk** —— "统计企业近30天开票金额"
  （`invoice_risk` × `metric_sum` × `metric_window` → 单表窗口 SUM SQL）。
- **Enterprise Relation** —— "统计企业关联自然人控制的其他企业数量"
  （`enterprise_relation` × `metric_join` × `metric_count` → 两跳 JOIN + 带自环排除的 COUNT DISTINCT）。

两个场景共用同一套解析器、IR、规划器、SQL 生成器、校验器、审批、测试、实验和 web 工作流。

### 开源打包

- 一条命令即可本地 demo（`scripts/start-demo.ps1` / `start-demo.sh`）—— SQLite + demo_mock LLM + 合成数据，无需 Spark / MySQL / API key。
- 终端快速 demo（`examples/quick_demo.py`）。
- GitHub Actions CI（后端测试 + ruff + alembic，前端构建 + 测试，开源安全扫描）。
- Docker Compose 配置（运行时在本仓库中未验证（NOT VERIFIED））。
- Apache License 2.0、CONTRIBUTING、SECURITY 策略、开源安全扫描器、架构文档，以及完整的工程历史（`docs/history/`）。

### 诚实的限制与已知不足

- 所有数据均为合成数据；没有任何真实的 Spark/Hive/MySQL/身份/遥测集成经过验证（集成测试会跳过并说明原因）。
- 按构造，`production_deployed` 在所有地方都是 `false`。
- demo LLM 模式是确定性替身，不是模型推理。
