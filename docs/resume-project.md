# AIRI — 简历项目素材

> 通用求职素材。**不包含**手机号、邮箱、真实公司名或任何机密信息。
> 本文所有事实均可在本仓库内核验；未核验的内容一律显式标注。

- 项目名（中文）：**AIRI 智能风控指标研发 Agent 平台**
- 项目名（英文）：**AIRI — An AI-assisted risk metric research & development platform**
- 一句话：把自然语言风控指标需求，编译成结构化 Metric IR，再由确定性 Python 工具生成可测试、可审计、可回滚的指标工程链路。

---

## 0. 写作纪律（先读这一节）

必须遵守，否则简历会与项目本身自相矛盾：

1. **不要写 "Phase 1–12"**，不要出现阶段编号。阶段史只属于 GitHub 内部文档。
2. **不要把测试数量放在第一条**。可以写 "built comprehensive automated tests"；`675 tests` 留给 GitHub 页和面试深入讨论。
3. **措辞对齐**：写 `AI-assisted`，不写 `AI-powered`；写 `research and development platform`，不写 `production-ready system`；写 `synthetic data`，不写 `fraud detection`。
4. **只使用可核验数字**（见 §5）。禁止 "提升研发效率 80%"、"准确率提升 30%" 这类没有生产数据支撑的表述。
5. 面试若被追问"真实效果如何"，标准回答是：数据为合成数据，实验数字只证明链路可用，不代表真实风控表现。

---

## 1. 版本 A — AI Agent 应用工程师 / 大模型应用工程师

**AIRI 智能风控指标研发 Agent 平台** · 独立设计与实现 · Python / FastAPI / Pydantic / Vue 3

- 独立设计并实现 AI 辅助风控指标研发平台：把自然语言指标需求编译为结构化 **Metric IR**，再由**确定性 Python 工具**渲染 SQL，形成"模型理解意图、Python 产出工件、人工审批闸门"的全链路。
- 以 **Pydantic 严格 schema** 把 LLM 输出约束在 IR 边界内（`extra="forbid"` + 标识符正则 + 枚举约束）：模型永不直接产出上线 SQL，非法响应**显式失败**而非静默修复或回落模板答案。
- 设计 **Scenario Skill × Capability Skill** 双层技能模型，解耦"业务语义"与"执行机制"；第二个场景（企业关联关系，两跳 join、`COUNT DISTINCT`、自环排除）**复用同一 workflow**，未新增编排代码，仅新增场景声明。
- 实现确定性 SQL 生成（固定 Jinja 模板 + 工具版本钉选）与**闭式文法静态校验**（完整匹配，拒绝 `UNION`/嵌套查询/注释/多语句/UDF，仅允许 `SELECT`）；产物内容寻址，人工审批的是哈希，保证"审批的即是运行的"。
- 实现自动化指标测试（schema 一致性、空值、重复键、窗口边界、join 正确性、distinct 语义、自环排除、对账、直连 join 对照）与统计实验（覆盖率、账期分箱、KS 及方向、IV、Lift、PSI、阈值候选）。
- 引入**证据驱动反思**：把 LLM 的假设与 Python 计算的事实**分表分类型**存储，反思结论不自动生效，只能生成有界候选交由人工决策，从存储层杜绝"模型观点被洗成指标定义"。
- 设计 **Metric Registry**：指标版本不可变、带审计事件流、支持回滚；SQL 审批 → 晋升复核 → 发布复核 → 部署复核四级人工闸门。
- 前端 Vue 3 薄客户端：所有统计量与版本状态来自后端 API，前端**零业务计算**，浏览器永不直连 LLM。

---

## 2. 版本 B — AI 产品经理 / AI 解决方案

**AIRI 智能风控指标研发 Agent 平台** · 独立设计与实现

- 面向风控指标研发场景，设计并落地 AI 辅助研发平台：把"分析师手写 SQL"的低效、不可审计流程，改造成"自然语言需求 → 结构化指标定义 → 自动测试 → 统计实验 → 人工审批"的标准化链路。
- 主导架构选型上的关键取舍：**不让大模型直接生成 SQL**。以结构化 IR 作为模型与数据库之间的可评审边界，使 AI 产出从"不可审的黑盒"变为"可 diff、可测试、可版本化"的资产。
- 用第二个差异化场景（企业关联关系，两跳关系聚合）验证架构可推广性，证明新增业务场景**不需要新增流程或新工具**，显著降低后续场景扩展成本。
- 设计治理与合规边界：四级人工审批闸门、指标版本不可变与回滚、审计事件流、生产适配器默认失效（fail-closed），确保 AI 建议无法绕过人工决策。
- 输出完整开源交付：README 产品化首页、架构文档、分场景操作指南、一键本地演示脚本与录制 Demo、CI 门禁与开源安全扫描。
- 明确并公开能力边界：合成数据、生产集成未验证，避免将演示效果误读为真实风控能力。

---

## 3. 版本 C — NLP / LLM 应用研发

**AIRI 智能风控指标研发 Agent 平台** · 独立设计与实现 · Python / Pydantic / FastAPI / Vue 3

- 设计并实现面向结构化输出的 LLM 应用链路：需求解析模型仅产出受 schema 约束的 `MetricIR`，通过 `extra="forbid"`、标识符正则与枚举约束，把非确定性生成收敛为可校验对象。
- 实现**模型输出不可信假设下的工程闭环**：非法响应显式失败、无静默修复、无回落答案；所有下游产物（SQL、测试、统计）均由 Python 确定性产出，模型不参与任何数值计算。
- 实现"事实 / 假设分离"的 LLM 反思机制：Python 计算统计事实，LLM 仅基于事实产出假设，二者分表分类型存储，假设无法覆盖事实、亦无法自动生效，从数据结构层面保证可审计性。
- 设计可组合的技能声明体系（Scenario Skill × Capability Skill），技能为可审计的声明式数据并钉选工具版本，机制层可跨场景复用；新增场景无需修改解析器、工作流、实验层、注册表与前端。
- 为生成物构建**闭式文法**校验器（非通用 SQL parser、非子串黑名单）：完整匹配投影、限定数据源、两个 DATE 边界与单一分组键，从构造上排除注释、嵌套查询、多语句、`UNION` 与 DDL/DML。
- 实现内容寻址的审批与规范化哈希（含 join 结构），保证"审批工件"与"运行工件"字节一致。
- 工程化交付：FastAPI 后端 + Vue 3 前端、Alembic 迁移（无漂移）、跨平台启停脚本、GitHub Actions CI、开源安全扫描。

---

## 4. 英文版（投递海外岗位用）

> **AIRI — An AI-assisted risk metric research & development platform** · Independent project (Python, Pydantic, FastAPI, Vue 3)

- Designed and built a platform that compiles natural-language risk metric requirements into a strict, versioned **Metric IR**, then uses **deterministic Python tools** — not the model — to generate the SQL. The model is trusted to understand intent and never to produce the deployed artifact.
- Enforced a hard boundary with **Pydantic strict schemas**; invalid model output fails loudly rather than being silently repaired or falling back to a canned answer.
- Decoupled **domain semantics from execution mechanics** via Scenario Skills × Capability Skills. A second, structurally different scenario (a two-hop enterprise relationship metric with `COUNT DISTINCT` and self-loop exclusion) reused the same workflow; the join mechanism was promoted to a **general capability** instead of a scenario-specific tool.
- Built a **closed-grammar** static SQL validator (full-match, not a substring blacklist) plus content-addressed approvals, so the query that ships is byte-identical to the query a human reviewed.
- Separated **computed facts from model hypotheses**: statistics are produced in Python, reflections are stored in a different table and type, and can never activate a change on their own.
- Delivered a governed metric registry (immutable versions, audit-event stream, rollback) with four distinct human approval gates, and a thin Vue 3 client that performs **zero business computation**.
- Documented the boundary honestly: all bundled data is synthetic, and Spark/Hive/MySQL/IAM integrations exist as fail-closed, **unverified** adapters.

---

## 5. 可核验数字（只能用这些）

| 指标 | 数值 | 核验方式 |
| --- | --- | --- |
| 差异化场景数 | 2（`invoice_risk@1.0.0`、`enterprise_relation@1.0.0`） | `src/airi/skills/` |
| 后端测试覆盖率 | 91% | `uv run pytest --cov` |
| 后端测试 | 675 passed / 14 skipped | 同上（skip 为缺真实基础设施的集成测试） |
| 前端测试 | 28 passed | `cd airi-web && npm run test` |
| API 操作数 | 116 | `GET /openapi.json` |
| 迁移 head | `0011_operational_convergence`，无漂移 | `alembic check` |
| 一键启动 | Windows 实测通过（start → healthy → stop → 端口释放） | `scripts/start-demo.ps1` |

**禁止使用的表述**（无生产数据支撑）：

- ❌ "研发效率提升 80%" · ❌ "指标准确率提升 30%"
- ❌ "已在生产环境验证" · ❌ "可识别真实欺诈"
- ❌ "支持 Spark/Hive 生产集群"（应写"提供适配器接口，生产集成未验证"）

---

## 6. STAR 故事（3 个）

面试被问"讲一个你解决过的最难的问题"时使用。**不虚构线上事故，不虚构客户收益。**

### STAR 1 — 架构：把审批哈希变成真正的安全属性

- **S**：平台采用内容寻址审批——人工审批的是 SQL 工件的哈希，这样"审批的"与"运行的"必然一致。
- **T**：第二个场景需要新增 join 能力，join 成为 IR 的一部分。如果规范化哈希不覆盖 join，就可以**改动 join 而哈希不变**，从而用一个旧审批放行一条没人评审过的查询。
- **A**：扩展规范化哈希使其覆盖 `joins`、`column_filters`、`source_alias`、`aggregation_alias`，并补齐钉住哈希的测试；同时把这些字段设计为 Optional，使 `MetricIR` schema 版本保持 `1.0.0`、**无需数据库迁移**。
- **R**：审批的安全属性（哈希覆盖全部影响 SQL 的因素）与扩展性需求（新增字段）同时成立。这是一类典型缺陷：**安全属性与扩展点相互作用的裂缝**，修在哈希而不在文档。

### STAR 2 — 多场景泛化：证明"新场景 ≠ 新工作流"

- **S**：项目只有一个场景（发票风险，单源聚合 + 时间窗）时，"架构是否真的通用"是无法证伪的宣称。
- **T**：选择一个**结构上必须不同**的第二场景，用最小改动验证架构，而不是靠再写一个同类场景自证。
- **A**：引入两跳企业关联关系场景（enterprise → person → enterprise），带 `COUNT DISTINCT` 与自环排除。关键决策：**不写 `enterprise_relation_join` 专用工具**，而是把 join 提升为通用能力 `metric_join@1.0.0` 并钉选 `generate_spark_sql_metric@1.3.0`，配套闭式文法 join 校验与"直连 join 对照"探针。
- **R**：两个场景走完全相同的 workflow；除场景声明外无分叉编排。新增场景只需一个声明文件 + 合成 fixture，解析器、工作流、实验层、注册表与前端均不变。

### STAR 3 — 可靠性 / 治理：让 AI 的建议无法绕过人工

- **S**：反思环节由 LLM 基于统计事实生成结论。若反思可直接写入指标定义，模型观点会**静默变成**受治理工件，且事后无法区分哪一条是事实、哪一条是观点。
- **T**：在保留 LLM 推理价值的前提下，从结构上杜绝"观点洗成事实"。
- **A**：把事实与假设做成**不同的 Pydantic 模型、不同的数据表**；反思对指标只读，只能产出有界候选（bounded proposal），由人工在独立闸门决策；保护性要求（工件完整性、指标版本标识、执行者认证）在单点定义且**永不可豁免**。
- **R**：可审计性成为数据结构保证而非文档约定；同时保留完整的四级人工审批与版本回滚。整体收敛出一句设计约束：`LLMs reason. Python verifies. Humans govern.`

---

## 7. GitHub Profile / 仓库展示文案

### Repository name

```text
airi
```

### Description（可直接粘贴）

```text
AI-assisted risk metric research & development platform: natural language → Metric IR → deterministic SQL → automated testing → experiments → reflection → human-governed metric registry.
```

### Topics

```text
ai-agent  llm  fastapi  vue  pydantic  risk-management
agentic-workflow  metric-engineering
```

### Pinned repo 一句话简介

```text
LLMs reason. Python verifies. Humans govern.
An agentic pipeline for risk metric engineering — the model never writes the SQL that ships.
```

### Profile 展示建议（不修改你的 GitHub Profile，仅建议）

1. 把 `airi` 置顶（Pin），它是当前最能体现"架构取舍 + 工程纪律"的项目。
2. 置顶顺序建议：`airi` → 你最能体现工程深度的第二项目 → 与目标岗位最相关的第三项目。
3. 若补充个人简介，建议突出取舍本身而非技术清单，例如
   `Backend / AI application engineer. Interested in the boundary between what a model proposes and what a system is allowed to do.`
4. **不要**在 Profile 里放任何未验证的性能宣称，与本仓库文档保持同一口径。

---

## 8. 面试前自检

- [ ] 能 30 秒讲清问题与解法（见 [airi-pitch.md](interview/airi-pitch.md)）
- [ ] 能讲清"为什么不直接 text-to-SQL"（见 [airi-qa.md](interview/airi-qa.md) 第 1 题）
- [ ] 能讲清"第二个场景如何证明架构通用"（第 12–13 题）
- [ ] 能主动、准确地说出未验证项（第 15–16 题）
- [ ] 5 分钟 Demo 已用秒表完整排练 3 次（见 [demo-script.md](interview/demo-script.md)）
- [ ] 简历中未出现阶段编号、未把测试数当第一卖点、未使用越界措辞
