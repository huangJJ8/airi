# AIRI —— 架构走查（共享屏幕时的口述稿）

用来带着面试官过一遍真实代码。每一节的结构是**目录 → 打开什么 → 说什么**。打开那个能说明问题的*最小*文件；共享屏幕时不要在大文件里来回滚。

---

## 共享屏幕之前

```bash
uv run --frozen python examples/quick_demo.py
```

先跑这个，用一个一直开着的终端窗口。它会在几秒内把一条需求跑完整条链路，并打印出真实产物——这样当你说「SQL 是 Python 生成的」时，你展示的是那个对象，而不是在断言。

---

## 0. 地图（30 秒，一屏）

```text
src/airi/
├── agents/requirement_parser/   natural language  → MetricIR        [LLM]
├── metric_ir/                   the contract + validation           [Python]
├── skills/                      domain knowledge × mechanism        [declarative]
├── tools/                       templates that emit SQL             [Python]
├── testing/                     automated metric checks             [Python]
├── experiments/                 datasets, labels, statistics        [Python]
├── reflection/                  evidence → hypotheses               [LLM, read-only]
├── refinement/                  bounded proposals                   [human-decided]
├── registry/                    versions, audit events, release     [governance]
├── production/                  adapters, identity, verification     [fail-closed]
├── temporal/                    PSI, OOT slice validation           [Python]
├── workflows/                   the pipelines that compose the above
└── api/                         FastAPI routers (116 operations)

airi-web/     Vue 3 UI — zero business computation
examples/     runnable demos, per-stage output
docs/         architecture, guides, history, interview material
```

**说：**「三种颜色。LLM 负责意图和解读。Python 负责每一个产物。人负责每一道关卡。这棵树里剩下的东西，都是这三者之一。」

---

## 1. 需求解析器——模型被允许动手的地方

**打开：** `src/airi/agents/requirement_parser/parser.py` 和 `prompts.py`

**说：** 模型拿到一个提示词，必须返回一个 Pydantic 模型。指一下 `agents/requirement_parser/schemas.py` 里的 schema——那就是契约。

**要砸下去的那句：**「这是模型唯一一处产出有结构意义的东西的地方，而且它产出的是 IR——不是 SQL。这个仓库里没有任何代码路径接收来自模型的 SQL 文本。」

---

## 2. 指标 IR——那条边界

**打开：** `src/airi/metric_ir/models.py`

先把字段展示出来，然后展示两样东西，它们让它成为一条*真正的*边界，而不是一个数据类：

- 标识符模式（`^[a-z][a-z0-9_]{0,63}$`）——不许有连字符，该放标识符的地方不许有自由文本；
- 那几个可选的扩展字段：`joins`、`column_filters`、`source_alias`、`aggregation_alias`。

**说：**「这四个字段是可选的，而且是最后才加的。因为可选，schema 版本停在 `1.0.0`，这次改动**不需要任何数据库迁移**。向后兼容是一项设计输入，不是运气。」

**打开：** `src/airi/metric_ir/joins.py`

**说：** 这就是 join 语法的全部——只允许 `INNER`/`LEFT`，只允许等值。小是故意的。这里的每一条限制，都对应一件在生产里不可能发生的事。

**打开：** `src/airi/metric_ir/semantics.py`

**说：** 规范化哈希（canonical hashing）在这里。正是它让审批哈希有意义——哈希必须覆盖所有会改变 SQL 的东西，*包括 join*。如果不覆盖，你就能改掉一个 join，然后复用一份过期的审批。

---

## 3. 技能——领域 vs. 机制

**打开：** `src/airi/skills/capabilities.py`（很短，值得整份展示），然后是 `src/airi/skills/invoice.py` 和 `src/airi/skills/enterprise_relation.py`

**说：** 能力只声明**一次**，并钉住工具版本：

```text
metric_window      @1.0.0 → generate_spark_sql_metric @1.1.0
metric_sum         @1.0.0 → generate_spark_sql_metric @1.1.0
metric_count       @1.0.0 → generate_spark_sql_metric @1.1.0
metric_growth_rate @1.0.0 → generate_growth_rate_sql   @1.0.0
metric_join        @1.0.0 → generate_spark_sql_metric   @1.3.0
```

然后展示 `invoice.py` 和 `enterprise_relation.py` 是**同一个形状**——它们的差别在语义，以及它们引用了哪些能力。两个场景都只有大约 80 行声明式内容。

**整场走查里最强的一句话：**
「第二个场景需要两跳 join。我没有去写一个 `enterprise_relation` 的 join 工具——我把这个机制晋级成了 `metric_join` 并把它钉住。这就是为什么 `New Scenario ≠ New Workflow`，也是为什么这两个场景之间*不一样的文件*只有这两个。」

---

## 4. 工具——SQL 到底是从哪来的

**打开：** `src/airi/tools/base.py`（接口），然后 `src/airi/tools/spark_sql.py`，再挑一个模板：`src/airi/tools/templates/spark/join_metric.sql.j2`

**说：** 工具用已校验的 IR 值渲染一个固定模板，返回 SQL 产物及其内容哈希。看一下这个模板的变化余地有多小——这正是重点。这里面没有任何一个分支会接受模型自己写的 SQL。

**然后专门打开那个 join 模板**（`join_metric.sql.j2`）——第二个场景就是靠它承载的，里面能看到 `COUNT DISTINCT` 和自环排除谓词。

---

## 5. 校验——第二道防线

**打开：** `src/airi/workflows/development/validation.py`（封闭语法）和 `src/airi/workflows/development/join_validation.py`（join 专用的语法）

**说：**「就算这条 SQL 是 Python 写的，它仍然必须过关。这个校验器是刻意做成**封闭语法，而不是通用 SQL 解析器，也不是子串检查**——它对投影列、带限定的源、两个 `DATE` 边界和一个分组键做全量匹配，因此从构造上就拒绝了注释、嵌套查询、UDF、`OR`、`UNION`，以及多余的语句和子句。另外，DDL/DML 动词是一律拒绝的：只允许 `SELECT`。」

`validation.py` 顶部的注释说的就是这个——值得展示，因为「用封闭语法，而不是一条安全正则」这个决定，面试官是认得出来的。

虽然它只是第二层，但仍然值得展示——它说明这套架构假定自己的生成器也可能出错。

---

## 6. 测试——因为 IR 是结构化的，平台知道该测什么

**打开：** `src/airi/testing/planning.py` 和 `src/airi/testing/relation.py`

**说：** `planning.py` 决定*哪些*检查适用于这个指标。`relation.py` 是 join 专用的探针集合——里面有一个能立住论点的东西：一个**直连 join 对照**，用一条独立的一跳写法来验证两跳的结果。八项检查、四个探针。

那个对照，就是「SQL 跑起来了」和「SQL 是对的」之间的区别。

---

## 7. 实验 + 反思——事实 vs. 假设

**打开：** `src/airi/evaluation/`（统计），然后是 `src/airi/reflection/models.py` 和 `src/airi/reflection/diagnostics.py`

**说：** 统计量在 Python 里算出来，作为事实存储。反思读取这些事实，写出**假设**——不同的模型、不同的表。`diagnostics.py` 展示了那些诊断规则是确定性的。

**要砸下去的那句：**「反思不能改变指标。它只能产出一个提案。这一点是由存储布局强制的，不是靠一个约定。」

**打开：** `src/airi/refinement/transform.py`

**说：** 一个提案在这里变成一个受约束的候选——而且它首先要经过人的决策。

---

## 8. 注册表——受治理的版本

**打开：** `src/airi/registry/versioning.py` 和 `src/airi/registry/models.py`

**说：** 版本是不可变的；有一个当前活跃指针；有回滚。`models.py` 同时装着状态和审计事件流——所以**加一种审计事件类型会牵动两个枚举**（我踩过的一个真 bug；这事值得主动说，这种细节听起来很真实）。

---

## 9. 生产——诚实的那部分

**打开：** `src/airi/production/adapters.py`，然后 `gate.py`，再提一下 `verification.py` 和 `identity.py`

**说：**「这些东西存在，也在边界上被跑过，但它们**没有对着真实环境验证过**——当时没有集群。所以它们默认处于惰性状态并失败关闭。`production/models.py` 里的 `PROTECTED_REQUIREMENTS` 定义了那些永远不可豁免的需求——产物完整性、指标版本标识、主体认证。」

**把这句砸下去：**「我更愿意让你看到我验证过的边界在哪里，而不是暗示一种我从没跑过的能力。这也是为什么 Spark、MySQL、生产身份和遥测的集成测试报告的是**跳过（skipped）**，而不是被 mock 成绿色。」

---

## 10. Web——刻意做薄

**打开：** `airi-web/src/api/` 和一个页面，比如 `airi-web/src/pages/ExperimentPage.vue`

**说：** 每一个 KS / IV / lift 数字、每一个版本状态都来自后端。前端不做任何业务计算，浏览器也从不跟 LLM 对话。治理冲突会直接渲染后端的 `error_code`，而不是自己编一条消息。

这是一条刻意的约束：一个会重算业务逻辑的 UI，会变成这个指标的第二个、不受治理的实现。

---

## 收尾（一句话）

「整个设计就是这样：模型负责理解和解读，Python 负责产出并验证每一个产物，人负责审批每一道关卡。如果你只记得一件事——真正跑起来的那条 SQL，从来不是模型写的。」
