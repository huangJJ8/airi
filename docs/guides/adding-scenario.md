# 新增场景

本指南是一则案例研究：`enterprise_relation` 是如何作为第二个场景被加入的，以及
这对下一个场景意味着什么。

要达成的结果是用否定形式表述的，因为这才是难的部分：

> **新场景 ≠ 新工作流。**

如果新增一个场景需要新流水线、新 SQL builder 或场景专属端点，那说明抽象失败了。

---

## 规则

```text
Scenario Skill      → business semantics        (domain knowledge)
Capability Skill    → execution mechanics       (mechanism)
Tool                → deterministic realisation (pinned by a capability)
```

问一句：*另一个领域是否也可能需要它？*

- “Enterprise → person → enterprise” → **场景知识**。别的场景不需要它。
- “按等值条件 join 两个结构化数据源” → **能力**。很多领域都需要它。
- “把 join 渲染成经过校验的 SQL” → **工具（Tool）**。一份实现，被固定引用。

无论往哪个方向弄错都是失败模式：业务含义落进 SQL 层会 fork 流水线；机制落进领域
层则无法复用。

---

## 分步说明（Phase 11 的实际做法）

### 1. 场景技能 —— 只放语义

新增 `src/airi/skills/<scenario>.py`。它声明*业务含义*，与 SQL 无关：

```python
KNOWLEDGE = [
    "业务域：企业关联关系风险（enterprise relationship risk）。",
    "主体：enterprise（企业），主体字段 enterprise_id。",
    "关系路径：enterprise -> person -> enterprise（两跳，不做多跳与股权穿透）。",
    "业务规则：自身排除——关联企业等于主体企业时不计入。",
    "业务规则：同一企业由多个自然人共同指向时仍只计一次（按企业去重）。",
    "注意事项：直接按 enterprise_id 关联 related_enterprise_id 会跳过自然人中转，语义错误。",
]

TEST_RULES = ScenarioTestRules(
    entity_null=TestRule(severity="warning"),
    result_duplicate=TestRule(severity="error"),
    empty_result=TestRule(severity="warning"),
    test_types=["schema", "null", "duplicate", "join", "distinct",
                "self_exclusion", "missing_relation", "reconciliation"],
)

def enterprise_relation_skills():
    capabilities = [metric_join(), metric_count(), spark_sql_generator()]
    scenario = ScenarioSkill(
        name="enterprise_relation",
        version="1.0.0",
        intent="研发企业关联自然人控制或参股的其他企业数量（related_enterprise_count）",
        capabilities=[VersionedReference(name=s.name, version=s.version) for s in capabilities],
        knowledge=KNOWLEDGE,
        test_rules=TEST_RULES,
        requires_human_review=True,
    )
    return [*capabilities, scenario]
```

注意**没有**什么：没有 join 语法、没有别名处理、没有方言、没有 SQL。`test_types`
声明这个领域需要哪*几类*测试 —— 后端把它们变成 UI 实际渲染的测试列表。

### 2. 注册它

把该技能加入 `src/airi/main.py` 中与 `invoice_risk` 并列的注册表装配。注册表固定
`name@version`；没有 “latest”。

### 3. 复用能力 —— 不要 fork

`metric_join` 已经作为*通用*能力存在，所以场景直接引用它即可。`metric_count` 本
就支持 `COUNT(DISTINCT field)`。

如果 `metric_count` 不支持去重计数，正确的做法是对既有能力做最小扩展
（`distinct: true`）—— **而不是**新增 `enterprise_relation_count_skill`。把业务与
机制重新耦合，正是这套架构要防止的事。

只有当*机制*确实既新又可复用时，才新增一个能力。如果它新但不可复用，那就是放错
位置的场景知识。

### 4. 扩展 IR，而不是扩展工作流

`MetricIR` 增加了可选的 `joins: list[JoinSpec]`
和 `column_filters: list[FieldComparison]`（`src/airi/metric_ir/joins.py`）。

```text
JoinSpec
├── alias        : Identifier
├── join_type    : "inner" | "left"          # only these two
├── source       : DataSource
└── conditions   : list[FieldComparison]      # 1..4, operator "=" or "<>"
```

让这次改动保持增量式的设计约束：

- 字段是**可选的、默认空** → 既有 IR 仍然校验通过
- `schema_version` 保持 `1.0.0` → 无需迁移，joins 存在于 JSON 文档里
- **没有 DAG**，没有递归 join，没有 `RIGHT`/`FULL`/`CROSS`/`LATERAL`
- 规范化哈希**包含** `joins`，因此不同的关系路径不会与既有哈希碰撞

想要一个 `MetricIRV2`，通常说明这次改动没有被建模为可选。

### 5. 把新词表教给解析器

两处，且有意不同：

- **Demo LLM**（`src/airi/infrastructure/demo_llm.py`）—— 从演示语句到结构化结果的
  确定性映射。它明确*不是*真实解析器里的模式匹配。
- **真实解析器 prompt**（`src/airi/agents/requirement_parser/prompts.py`）—— 对新
  场景和预期 join 形态的结构化描述，使真实 LLM 能翻译成已知语法。

未知或有歧义的需求必须**被拒绝**，而不是靠猜。在 Phase 11 中，有歧义的表述
「统计企业关联数量」会被拒绝，跨场景的表述则无法通过场景隔离。

### 6. 带手算期望值的合成夹具

新增一个合成数据集（`src/airi/infrastructure/relation_fixture.py`），有意包含这些
棘手情况：

| 情况 | 原因 |
| --- | --- |
| 重复的关系行 | 证明 `COUNT DISTINCT` |
| 两个自然人 → 同一家其他企业 | 证明去重是按企业进行的 |
| 关系回指主体企业 | 证明自身排除 |
| 没有任何关联关系的企业 | 证明“不伪造零值行” |

期望值**由人工写出**。测试绝不调用生产代码来推导预期答案 —— 否则测试只是在断言
代码等于它自己。

### 7. 测试

新增 `tests/test_<scenario>.py`，覆盖声明的 `test_types`，外加对本场景之外也重要
的回归项：

- join 正确性 —— 路径是 `enterprise → person → related_enterprise`，**不是**
  `enterprise_id → related_enterprise_id` 的直连捷径
- 去重语义
- 自身关系排除
- null / 重复实体
- 缺失关系
- 对账

然后加入**规划器回归**和**场景隔离**测试：

- `invoice_risk` 规划为 `metric_sum + metric_window`
- `enterprise_relation` 规划为 `metric_join + metric_count`
- 两者不会互相泄漏

### 8. 接好演示入口

- `scripts/seed_demo.py` —— 通过*真实*的受治理 API 链播种新场景（而不是直接插入
  数据行）
- `examples/demo_phase11.py` —— 并排运行两个场景的终端演示
- Web 端 `Demo Examples` 下拉菜单 —— 一个条目，背后是同一批页面

没有新页面。复用 `/development`、`/testing`、`/experiments`。

---

## 检查清单

- [ ] 已新增场景技能，含语义、`test_types` 和人工评审标记
- [ ] 技能已注册（固定版本，没有 “latest”）
- [ ] 复用了既有能力；扩展保持通用
- [ ] IR 改动是可选的/向后兼容的（或迁移 + 版本号提升有充分理由）
- [ ] 规范化哈希覆盖新字段
- [ ] Demo LLM 与真实 prompt 已更新；未知/有歧义输入仍被拒绝
- [ ] 合成夹具包含重复 / 自身 / 多自然人 / 空结果等情形
- [ ] 测试中有手算的期望值
- [ ] 规划器回归 + 场景隔离测试
- [ ] 播种脚本与演示脚本已更新
- [ ] Web 端展示新场景而**没有新页面**

## 反模式

| 不要 | 原因 |
| --- | --- |
| `generate_<scenario>_sql` 工具 | 场景专属 SQL 会 fork 生成器 |
| SQL 模板里出现业务措辞 | 业务含义属于场景技能 |
| 为增量字段引入 `MetricIRV2` | 可选字段 + JSON 文档列让它无需升级版本 |
| 每个场景复制一套工作流 | 重点就是只有一套工作流 |
| 在真实解析器里写 `if "关联企业" in requirement` | 硬编码语句无法泛化；应使用技能目录 / 结构化 prompt |
| 用生产代码计算期望值的测试 | 什么都断言不了 |

---

## 下一步

- [架构总览](../architecture/overview.md) —— 各部分如何拼合
- [设计原则](../architecture/design-principles.md) —— 为什么要这样拆分
- [演示指南](demo.md) —— 看两个场景实际运行
