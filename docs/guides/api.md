# API 指南

后端是一个 FastAPI 应用。可交互、始终最新的参考：

| | |
| --- | --- |
| Swagger UI | <http://localhost:8000/docs> |
| ReDoc | <http://localhost:8000/redoc> |
| OpenAPI JSON | <http://localhost:8000/openapi.json> |
| 存活检查 | `GET /health` |
| 运行时事实 | `GET /api/v1/meta` |

本页讲解**领域**端点 —— 也就是承载业务含义的那些。总共有 116 条路由条目；完整
列表在 OpenAPI schema 中，此处不再重复。

---

## 约定

- 基础路径：`/api/v1`
- 所有请求/响应体都是**严格**的 Pydantic schema：未知字段会被拒绝，而不是被忽略。
- 错误返回带机器可读 `error_code` 的结构化 body（例如 `requirement_parse_failed`、
  `artifact_hash_mismatch`）。Web UI 渲染后端给出的代码，而不是自行编造消息。
- 治理冲突返回 **409** 并附带原因；校验问题返回 **422**。
- 每个响应都带 `X-Request-ID`；服务端以相同的 id、method、status 和 duration 记录
  `request_completed`。

### `GET /api/v1/meta`

UI 诚实模式横幅使用的只读运行时事实。有意不暴露任何领域状态。

```json
{
  "app_name": "AIRI",
  "version": "1.0.0",
  "environment": "local",
  "execution_mode": "mock",
  "llm_mode": "demo_mock",
  "production_deployed": false,
  "scenarios": ["enterprise_relation", "invoice_risk"]
}
```

`scenarios` 来自已注册的场景技能 —— UI 从不硬编码演示场景族。

---

## 开发

`src/airi/api/development.py`

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/development/generate` | 需求 → 指标 IR + 生成的 SQL 草案 + 校验 |
| `GET` | `/api/v1/development/artifacts/{artifact_id}` | 取回已存储的产物 |

```bash
curl -X POST http://localhost:8000/api/v1/development/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "requirement": "统计企业关联自然人控制的其他企业数量",
    "scenario": "enterprise_relation"
  }'
```

响应包含解析出的场景、选中的能力、指标 IR、生成的 SQL 以及静态校验结果。**这一步
不执行任何东西，也不批准任何东西。** 未知或有歧义的需求会以
`requirement_parse_failed` 失败，而不是靠猜。

---

## 审批

`src/airi/api/approvals.py`

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/approvals` | 为某个产物发起审批请求 |
| `POST` | `/api/v1/approvals/{approval_id}/approve` | 批准 |
| `POST` | `/api/v1/approvals/{approval_id}/reject` | 拒绝 |
| `GET` | `/api/v1/approvals/{approval_id}` | 查询状态 |

审批请求会携带它所批准的内容哈希：

```json
{
  "artifact_id": "...",
  "metric_ir_hash": "...",
  "artifact_hash": "..."
}
```

如果哈希与已存储的产物不匹配，请求会被拒绝。这正是审批意味着*“就是这份内容”*、
而不是*“这个 artifact id”*的原因。

---

## 测试

`src/airi/api/tests.py`

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/tests/run` | 对已批准的产物运行自动化测试套件 |
| `GET` | `/api/v1/tests/{test_run_id}` | 取回测试报告 |

**测试列表由后端产出**，依据场景声明的 `test_types`。`invoice_risk` 得到窗口边界
测试；`enterprise_relation` 得到 join 正确性、去重计数和自身关系排除。UI 只渲染
返回的内容（`name`、`status`、`evidence`），从不自己决定测试名称 —— 这就是没有
窗口的指标永远不会显示 “Window Semantics PASS” 一行的原因。

---

## 实验

`src/airi/api/experiments.py`

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/datasets` | 注册数据集 |
| `GET` | `/api/v1/datasets/latest` | 最新数据集（用 `?name=` 挑选演示样本） |
| `POST` | `/api/v1/labels` | 注册标签定义（outcome） |
| `GET` | `/api/v1/labels/latest` | 最新标签定义 |
| `POST` | `/api/v1/experiments` | 创建实验 spec |
| `POST` | `/api/v1/experiments/{spec_id}/run` | 运行它 |
| `GET` | `/api/v1/experiments/runs/{run_id}` | 取回该次运行 |

评估块由 Python 计算（确定性），包含 `coverage`、`bad_rate`、`distribution`、
`bins`、`ks`（带 `direction`）、`iv`（带 `iv_status`）以及
`threshold_candidates`。两个演示场景都用这同一个引擎 —— 没有第二套评估栈。

---

## 反思

`src/airi/api/reflections.py`

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/reflections` | 对一次实验运行反思（reflection） |
| `GET` | `/api/v1/reflections/{reflection_run_id}` | 取回反思结果 |
| `GET` | `/api/v1/reflections/{reflection_id}/proposals` | 有界提案 |
| `POST` | `/api/v1/reflections/{reflection_id}/proposals/{proposal_id}/decision` | 接受/拒绝一个提案 |

反思输出是**另一种产物类型**。它引用统计结果；不能覆盖它们，也不能修改 IR、阈值
或标签定义。提案仅供参考，需要显式决策。

---

## 注册表

`src/airi/api/registry.py`

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/metric-versions` | 注册一个已批准的指标版本 |
| `GET` | `/api/v1/metrics` | 列出指标 key |
| `GET` | `/api/v1/metrics/{metric_key}` | 指标摘要 |
| `GET` | `/api/v1/metrics/{metric_key}/active` | 当前活跃版本 |
| `GET` | `/api/v1/metrics/{metric_key}/versions` | 版本历史 |
| `GET` | `/api/v1/metrics/{metric_key}/versions/{version}` | 单个版本 |
| `GET` | `/api/v1/metrics/{metric_key}/versions/{from}/compare/{to}` | 比较两个版本 |
| `GET` | `/api/v1/metrics/{metric_key}/events` | 注册表审计事件 |
| `POST` | `/api/v1/metrics/{metric_key}/rollback` | 回滚到之前的版本 |

版本不可变。一次变更产生一个新版本；历史永不改写。每次状态变更都会追加一条审计
事件。

---

## 生命周期与生产领域

这些已实现且有测试覆盖，但本地演示不会驱动它们，而且它们在本仓库中**未针对真实
环境验证**（NOT VERIFIED）。此处归类只为便于定位：

| 领域 | 前缀 | 说明 |
| --- | --- | --- |
| 晋级评审 | `/api/v1/promotion-reviews` | 时域验证之前的门禁 |
| 时域验证 | `/api/v1/temporal-validations`, `/api/v1/temporal-series` | OOT / 稳定性 |
| 精炼（refinement） | `/api/v1/refinements` | 有界提案评估 |
| 发布 | `/api/v1/releases`, `/api/v1/release-reviews` | 门控激活 |
| 执行 | `/api/v1/executions` | 测试/执行运行时 |
| Spark 测试环境 | `/api/v1/environments/spark-test` | 由运维人员验证的测试集群，可选 |
| 生产 | `/api/v1/production-*` | 适配器默认为惰性；失败即关闭 |
| 监控 | `/api/v1/metric-alerts`, `/api/v1/monitoring-*`, `/api/v1/telemetry-sources` | 基于快照 / 期望 |

阅读这一组时，有两条不变量值得了解：

- 身份提供方与生产适配器**默认为禁用**。配置错误的生产部署会失败即关闭（fail
  closed），而不会信任客户端提供的名称或合成运行时。
- “部署返回成功”不等于“运行时已验证” —— 验证、对账和收敛是各自独立记录的产物。

---

## 下一步

- [快速开始](quickstart.md) —— 启动服务端
- [演示指南](demo.md) —— 这些端点驱动的工作流
- [设计原则](../architecture/design-principles.md) —— 这些边界为何如此划分
