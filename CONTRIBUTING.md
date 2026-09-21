# 参与贡献 AIRI

欢迎参与贡献。AIRI 是一个作品集 / 研究型项目，因此合并的门槛是：**可理解、尽可能确定性，并且对哪些已验证、哪些未验证保持诚实。**

## 环境搭建

```bash
# backend (Python 3.12+, managed with uv)
uv sync --frozen --extra dev

# frontend (Node 18+, npm)
cd airi-web
npm ci
```

## 开发流程

1. 为你的改动创建一个分支。
2. 带着测试完成改动。
3. 运行完整质量门禁（见下文）—— 必须全部通过。
4. 使用 PR 模板提交拉取请求。

## 后端测试

```bash
uv run --frozen pytest
uv run --frozen pytest --cov=airi --cov-report=term-missing
uv run --frozen ruff check src tests scripts examples
uv run --frozen ruff format --check src tests scripts examples
uv run --frozen alembic check
```

集成测试（`spark_integration`、`mysql_integration`、`production_integration`、`identity_integration`、`telemetry_integration`）在没有真实基础设施时会**有意跳过**。跳过才是正确结果；绝不允许让它们伪造通过。

## 前端测试

```bash
cd airi-web
npm run build
npm run test
```

## 开源安全检查

每次提交涉及内容的改动前，运行：

```bash
uv run --frozen python scripts/check_open_source_safety.py
```

绝不提交真实凭据、真实客户数据、真实公司名，或真实的内部主机名 / 表名。Demo 数据必须保持合成数据。

## 新增场景技能（Scenario Skill）

这是架构所支持的扩展路径。一个新的业务场景（例如一个新的风险域）应当作为**知识**加入，而不是作为新工作流：

1. 在 `src/airi/skills/` 下新增一个 Scenario Skill 来描述该领域：实体、数据源、关系语义、业务规则、已知陷阱、测试预期。
2. 尽可能复用已有的能力技能（`metric_sum`、`metric_count`、`metric_window`、`metric_join`……）。
3. 如果确实需要一种全新的*机制*，就新增一个**通用**能力技能（就像 `metric_join` 在 Phase 11 那样）—— 绝不要做 `<scenario>_sql_generator` 这类工具。
4. 扩展解析器目录 / demo LLM 映射，使该场景可以从自然语言触达，并且对歧义需求**直接拒绝**，而不是猜测。
5. 新增一个合成夹具（fixture），并附上手算的预期结果。
6. 新增测试：场景选择、规划器路由、SQL 形态，以及手算的数字。

完整的走查（以 `enterprise_relation` 为示例）见 [docs/guides/adding-scenario.md](docs/guides/adding-scenario.md)。

**不要**做这些：

- 为新场景复制开发 / 测试 / 实验工作流。
- 把 SQL 字符串或 JOIN 路径放进 Scenario Skill。
- 让 LLM 直接产出最终 SQL（Metric IR 的存在正是为了避免这一点）。
- 创建场景专用的 SQL 工具。

## 新增能力技能（Capability Skill）

能力技能编码的是*执行机制*（如何聚合、如何 join、如何生成 SQL），独立于任何场景进行版本化：

1. 声明该技能（id、version、输入 / 输出、约束）。
2. 实现或扩展确定性 Python 工具 / Jinja 模板。
3. 若引入了新的 SQL 形态，扩展静态 SQL 校验器白名单。
4. 单独测试该工具，并从至少一个场景测试规划器路由。

## 拉取请求检查清单

- [ ] `pytest` 通过（没有删除基线测试）
- [ ] `ruff check` 与 `ruff format --check` 通过
- [ ] `airi-web`：`npm run build` 与 `npm run test` 通过
- [ ] 无敏感数据（运行安全扫描器）
- [ ] 保持 Scenario 与 Capability 的边界（工具里没有领域知识，场景技能里没有 SQL 机制）
- [ ] 未引入 LLM 直连 SQL 的路径
- [ ] 任何实际未验证的内容都标注为 SKIPPED / NOT VERIFIED，而不是悄悄变绿
