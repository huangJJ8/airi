## 概要

<!-- 改了什么、为什么改。若有对应 issue 请链接。 -->

## 类型

- [ ] 缺陷修复
- [ ] 新增场景技能
- [ ] 新增能力技能 / 工具
- [ ] Metric IR 变更
- [ ] Web UI
- [ ] 文档 / CI / 工具链
- [ ] 重构（不改变行为）

## 验证

<!-- 粘贴你实际运行过的命令。没有运行的项不要勾选。 -->

```bash
uv run --frozen pytest -q
uv run --frozen ruff check src tests scripts examples
uv run --frozen ruff format --check src tests scripts examples
AIRI_DATABASE_URL="sqlite+pysqlite:///.demo/pr.db" uv run --frozen alembic upgrade head
```

```bash
cd airi-web && npm run build && npm run test
```

- [ ] 后端测试通过（不低于此前的基线）
- [ ] `ruff check` 与 `ruff format --check` 干净
- [ ] 前端构建与单元测试通过（若涉及前端）
- [ ] Alembic `upgrade head` 成功，且 `alembic check` 无漂移（若涉及模型）

## 架构边界

逐项勾选，或说明为何不适用。

- [ ] **不存在 LLM 直连 SQL 的路径。** SQL 仍由确定性工具从 Metric IR 生成。
- [ ] **保持 Scenario 与 Capability 的分离。** 业务含义在场景技能中，机制在能力 / 工具中。没有新增场景专用的 SQL 工具。
- [ ] **IR 变更是增量的**（带默认值的可选字段），或者包含并论证了迁移与版本号提升。
- [ ] **规范化哈希覆盖新的 IR 字段**，使不同指标不会碰撞。
- [ ] **治理不变。** 审批 / 晋级 / 发布闸门仍需明确的人工决策。
- [ ] **测试预期是手算的**，而不是通过调用生产代码推出来的。

## 安全

- [ ] 未新增密钥、token 或真实凭据
- [ ] 无真实客户 / 公司 / 个人姓名或真实标识符
- [ ] 未随附真实数据文件 —— demo 数据为合成数据
- [ ] `uv run --frozen python scripts/check_open_source_safety.py` 通过

## 诚实性

- [ ] 任何实际未验证的内容都描述为未验证（`NOT VERIFIED`），而不是说它能工作
- [ ] 文档 / README 中的新数字来自真实运行过的命令，而非估计

## 给评审人的说明

<!-- 棘手之处、取舍、你有意留到后续处理的事项。 -->
