# AIRI —— 五件值得记住的事

如果面试官关于 AIRI 只记得五件事，那就是这五件。

---

## 1. 指标 IR（Metric IR）——让 LLM 的输出可评审的那条边界

自然语言需求在被送去做任何数据库操作之前，先被编译成一份**严格、带版本号的 Pydantic 契约**。模型可以提方案；但只有 schema 说了算。

`src/airi/metric_ir/` · `MetricIR` 承载 source、window、filters、aggregation、grouping、joins、label definition 和 evaluation spec——每个字段本身还要再按标识符模式或白名单校验一遍。

**关键在于：** 你没法拿原始的生成 SQL 去对着业务意图做评审，但你可以评审一份结构化的 IR。IR 才是人真正审批的那个产物。

---

## 2. 场景技能（Scenario Skill）× 能力技能（Capability Skill）——领域 vs. 机制

这是平台在可扩展性上唯一一次真正的架构押注：

```text
Scenario Skill   (what the business means)
   invoice_risk        @1.0.0
   enterprise_relation @1.0.0
        │  declares which mechanisms it needs
        ▼
Capability Skill (how that mechanism is realised, and which tool pins it)
   metric_window @1.0.0 → generate_spark_sql_metric @1.1.0
   metric_sum    @1.0.0 → generate_spark_sql_metric @1.1.0
   metric_count  @1.0.0 → generate_spark_sql_metric @1.1.0
   metric_growth_rate @1.0.0 → generate_growth_rate_sql @1.0.0
   metric_join   @1.0.0 → generate_spark_sql_metric @1.3.0
```

`src/airi/skills/` · 业务语义永远不进 SQL 层；执行机制在 `capabilities.py` 里声明一次，之后复用。

**关键在于：** 加第二个场景，实际上只加了一个场景文件。它需要的 join 机制被晋级成了通用能力，而不是写成一个场景专用的特例——关于 `metric_join`，见 [airi-qa.md](airi-qa.md) 里的 §Answer。

---

## 3. 确定性的 SQL 生成——模型从不写查询

SQL 来自**受控的 Jinja 模板**（`src/airi/tools/templates/`），填入的是通过了校验的 IR 值，然后还要过一遍**静态白名单语法**，才有资格被执行或审批：

- 只允许 `INNER JOIN` / `LEFT JOIN`
- join 条件里只允许等值谓词
- 不许 `UNION`、不许 CTE 注入、不许 UDF、不许 DDL/DML
- 只读执行沙箱

生成的产物是**内容寻址**的；人审批的是一个哈希，所以最终发出去的查询与被评审过的查询逐字节一致。

**关键在于：** 这就是「LLM 生成了 SQL」和「LLM 填了一张表，Python 写了 SQL」的区别。

---

## 4. 实验 + 反思（reflection）——把算出来的事实和模型的看法分开

统计量在 Python 里算出来，作为**事实**存储：coverage、bad rate、decile bins、KS 及其方向、IV、lift、PSI、threshold candidates。

然后把这些事实交给 LLM，让它给出**假设**——假设存在单独的表、单独的类型、单独的 UI 区块里。假设永远不会覆盖事实，而且**反思本身永远不会让任何改动生效**；它要么变成一个受约束、由人拍板的精炼（refinement）提案，要么就止步于此。

**关键在于：** 这样才能让模型对证据做推理，同时又不会让它把一条主观看法洗成指标定义。

---

## 5. 每一个要紧的关卡都由人治理

审批不是最后打一个勾。它是一组彼此独立、有记录的关卡：

```text
SQL approval → promotion review → release review → deployment review
```

指标版本在注册表里是**不可变**的，各自带着自己的审计事件流，并且支持回滚。测试通过数、某个统计量、或者模型的某个结论，本身从来都不是可以晋级的许可。

**关键在于：** 那句标语是一条设计约束，不是宣传语——*LLMs reason. Python verifies. Humans govern.*（模型负责推理，Python 负责验证，人负责治理。）
