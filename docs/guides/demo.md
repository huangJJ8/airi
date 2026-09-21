# 演示指南

AIRI 内置两个结构上不同的演示场景，它们走**同一套**产品工作流。这正是重点所在：
新增一个业务域靠的是提供场景知识，而不是 fork 流水线。

> 下文全部使用仓库内生成的**合成数据**。不涉及任何真实公司、个人、发票或标识符。
> 结果仅供参考，**不是**生产证据。

---

## 为什么有两个场景

| | Invoice Risk（发票风险） | Enterprise Relation（企业关联） |
| --- | --- | --- |
| 业务语义 | 随时间变化的开票金额 | 企业之间的控制关系 |
| 数据形态 | 单张事实表 | 两张表按自然人关联 |
| 实体路径 | enterprise → invoice facts | enterprise → person → enterprise |
| 实现机制 | 时间窗口 + `SUM` | join + `COUNT DISTINCT` |
| 能力 | `metric_window`, `metric_sum` | `metric_join`, `metric_count` |
| 共用 | Requirement Parser、Metric IR、Skill Planner、Tool Planner、SQL Generator、Validator、Approval、Testing、Experiment、Web UI | ← 相同 |

如果同一条流水线不用第二套工作流就能同时容纳二者，说明场景技能（Scenario
Skill）× 能力技能（Capability Skill）的拆分确实在起作用。

---

## 演示 A —— Invoice Risk（发票风险）

### 输入

```text
统计企业近30天开票金额
```

### 预期解析结果

| 步骤 | 结果 |
| --- | --- |
| 场景 | `invoice_risk@1.0.0` |
| 能力 | `metric_window`, `metric_sum`, `spark_sql_generator` |
| 指标 IR（Metric IR） | `entity_key: enterprise_id`, `aggregation: sum(amount)`, `window: 30 natural days`, 显式锚点 |
| SQL | 单表带窗口的 `SUM` |

### 关注点

- **Development** —— 指标 IR 查看器显示窗口（左闭右开，`Asia/Shanghai`）以及被求和
  的字段。
- **Testing** —— 窗口边界语义、schema、null/重复处理。
- **Experiment** —— coverage、KS、IV、lift、bins、阈值候选。
- **Reflection** —— 对统计结果的叙述性解读，以及有界提案。
- **Registry** —— 一个不可变的受治理版本。

---

## 演示 B —— Enterprise Relation（企业关联）

### 输入

```text
统计企业关联自然人控制的其他企业数量
```

### 预期解析结果

| 步骤 | 结果 |
| --- | --- |
| 场景 | `enterprise_relation@1.0.0` |
| 能力 | `metric_join`, `metric_count`, `spark_sql_generator` |
| 关系路径 | `enterprise → person → enterprise`（两跳） |
| 聚合 | `COUNT DISTINCT related_enterprise_id` |
| 业务规则 | 自身排除 —— `related_enterprise_id <> enterprise_id` |

### 生成的 SQL（形态）

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
    ep.enterprise_id
```

这由共享的确定性工具从 IR 生成 —— 工作流从不自行拼装 SQL 字符串。

### 关注点

- **Development** —— IR 查看器现在会显示一个 `joins` 块，外加一行关系摘要
  （`enterprise → person → enterprise`）和 `COUNT DISTINCT`。
- **Testing** —— 测试列表由后端按场景下发：*Join Correctness*、*Distinct Count*、
  *Self Relation Exclusion*。没有窗口测试，因为这个指标没有窗口。
- **Experiment** —— 复用同一套评估引擎（`coverage`、KS、IV、lift、bins、
  thresholds），只是换成这个指标的数据。没有第二套评估栈。

### 随仓库附带的夹具（fixture）覆盖的边界情况

合成关系数据集被构造成让下列情况全部出现，自动化测试逐条断言：

| 情况 | 预期行为 |
| --- | --- |
| 同一条关系行重复出现 | `COUNT DISTINCT` 防止重复计数 |
| 两个自然人 → 同一家其他企业 | 只计一次，不是两次 |
| 自然人的关系回指企业自身 | 由自身排除规则排除 |
| 没有任何关联关系的企业 | 不出现在结果中（不伪造零值行） |

手算的期望值就在夹具里，由人工写出 —— 测试绝不会调用生产代码来推导预期答案。

---

## UI 操作走查

两个演示走完全相同的路径。**不需要复制粘贴 UUID** —— 上下文通过路由 query 和
会话级 store 传递。

```text
/development   pick a Demo Example → Generate → review IR / Skills / SQL
               → Submit → Approve
     ↓
/testing       Run automated tests → per-test status + evidence
     ↓
/experiments   Run experiment → coverage / KS / IV / lift / bins / thresholds
               → Analyze (Reflection) → proposals
     ↓
/registry      governed metric version
```

### 诚实模式标记

UI 有意让这些一直可见。它们是特性，不是冗余：

- `LOCAL DEMO`
- `Synthetic Data`
- `NOT PRODUCTION VERIFIED`
- `Browser does not generate or modify SQL.`

---

## 演示**不**主张什么

- 不是生产风控系统。
- 合成数据不是风险证据。
- 实验数字说明的是*流水线*，而不是在真实资产组合上的预测能力。
- 企业级 Spark/Hive/生产适配器存在于代码中，但在本仓库中**未验证**（NOT
  VERIFIED）。

---

## 下一步

- [快速开始](quickstart.md) —— 把它跑起来
- [API 指南](api.md) —— 用 HTTP 驱动它
- [新增场景](adding-scenario.md) —— 演示 B 是怎样加进来的
