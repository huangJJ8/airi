# AIRI Phase 1.5 Implementation Report

三个 Case 已完成：金额 SUM、次数 COUNT(*)、金额增长率均经 Mock LLM → IR → Planner → Python/Jinja2 → SQL Validator → 持久化 → submit → approve 验证。没有执行生产 SQL。

## 1. Repository Assessment

基于修改前实际源码，而非仅依据旧 README：

1. MetricIR 是单源原子模型：schema_version=1.0.0、name、display_name、description、entity_type、entity_key、partition_field、source、aggregation、dimensions、filters、window。严格类型、禁止 extra、冻结字段；标识符受限，SUM 必须有 field，COUNT 可省略 field；窗口正整数、维度不重复且不包含 entity_key。
2. ToolRegistry 使用 (name, version) 字典注册，重复报错；get 后 invoke 校验输入、执行、再校验输出。
3. SkillRegistry 注册 Scenario/Capability 的精确版本，保存验证后副本，读取深拷贝。
4. SkillDependencyValidator 原来检查场景与能力类型、精确版本、工具存在性，并要求选中能力集合等于场景全部声明。
5. SkillPlanner 原来仅支持 SUM + window，固定选择 metric_window / metric_sum / spark_sql_generator。
6. ToolPlanner 用 dict.fromkeys 保序去重，只接受一个原子 SQL Tool。
7. DevelopmentWorkflow.run 原链路：parse → validate_invoice_metric → plan skills → validate dependencies → plan tools → map input → invoke → validate SQL → return draft。
8. requires_human_review 只是强制 true 字段，没有审批状态或记录。
9. SQLDraft 没有唯一 ID、内容哈希、产物版本或运行 ID。
10. SQLAlchemy 已有 Base、Session 工厂、失败回滚与资源关闭，适合增加业务模型；没有业务表或 Alembic。

## 2. Architecture Changes

- 保留 MetricIR v1 为 Atomic IR，COUNT 复用已有 count/field=null 语义，不加入派生表达式。
- 新建 DerivedMetricIR，仅允许两个明确角色的原子依赖、growth_rate operator 和 null 零分母策略；不接受自由表达式或递归依赖。
- Scenario 按用户要求继续 invoice_risk@1.0.0。其 capabilities 改为可用能力集合；Planner 选择子集，Dependency Validator 检查选中引用的精确版本与类型。
- 新增 metric_count@1.0.0、metric_growth_rate@1.0.0。原子 Tool 与共享 aggregation 模板升级为 1.1.0；派生 Tool/模板为 1.0.0。旧 Prompt 原文保留为 SYSTEM_PROMPT_V1，当前 Prompt=1.1.0。
- Growth Tool 使用已注册的原子 Tool 生成两个 CTE；最终投影由单独模板组合，两个原子计划写入 derived_plan，没有 DAG 引擎。
- 新增 UUID workflow_run_id / artifact_id。artifact_version 表示该产物快照版本，当前新 ID 均从 1.0.0 开始；没有就地修改接口。
- content_hash 对 code、language、type、template、warnings、metric_name、artifact_version 做 canonical JSON SHA-256；IR 独立 canonical hash。递归排序对象键、UTF-8、固定分隔符、拒绝非有限数字；不含 UUID、时间戳、状态和 request_id。SQL 文本变更（包括空白）视为新内容。
- API 成功生成后必须持久化，两张表由 Alembic 管理，不在生产启动调用 create_all。纯 Python Workflow 仍可离线使用；HTTP API 是持久化边界。
- 审批独立于生成快照：snapshot 保留生成时 draft，数据库 status 保存当前状态，GET artifact 返回当前 status；内容哈希不因审批状态变更而改变。

## 3. Added Files

- `src/airi/approvals/artifacts.py`
- `src/airi/approvals/schemas.py`
- `src/airi/approvals/persistence.py`
- `src/airi/approvals/service.py`
- `src/airi/api/approvals.py`
- `alembic.ini`
- `migrations/env.py`
- `migrations/script.py.mako`
- `migrations/versions/0001_reviews.py`
- `src/airi/metric_ir/derived.py`
- `src/airi/tools/growth_sql.py`
- `src/airi/tools/templates/spark/growth_rate.sql.j2`
- `src/airi/workflows/development/derived_planning.py`
- `src/airi/workflows/development/derived_validation.py`
- `tests/test_phase15.py`
- `examples/demo_phase15.py`
- `phase15-report.md`
- `examples/invoice_amount_30d_ir.json`
- `examples/invoice_amount_30d_request.json`
- `examples/invoice_amount_30d_result.json`
- `examples/invoice_amount_30d_review.json`
- `examples/invoice_count_30d_ir.json`
- `examples/invoice_count_30d_request.json`
- `examples/invoice_count_30d_result.json`
- `examples/invoice_count_30d_review.json`
- `examples/invoice_amount_growth_30d_ir.json`
- `examples/invoice_amount_growth_30d_request.json`
- `examples/invoice_amount_growth_30d_result.json`
- `examples/invoice_amount_growth_30d_review.json`
- `examples/invoice_count_30d.sql`
- `examples/invoice_amount_growth_30d.sql`

## 4. Modified Files

- `src/airi/core/exceptions.py`
- `src/airi/metric_ir/invoice.py`
- `src/airi/skills/invoice.py`
- `src/airi/skills/dependencies.py`
- `src/airi/tools/spark_sql.py`
- `src/airi/tools/templates/spark/aggregation_metric.sql.j2`
- `src/airi/agents/requirement_parser/parser.py`
- `src/airi/agents/requirement_parser/prompts.py`
- `src/airi/agents/requirement_parser/schemas.py`
- `src/airi/workflows/development/planning.py`
- `src/airi/workflows/development/schemas.py`
- `src/airi/workflows/development/validation.py`
- `src/airi/workflows/development/workflow.py`
- `src/airi/api/development.py`
- `src/airi/main.py`
- `tests/conftest.py`
- `tests/test_development.py`
- `pyproject.toml`
- `uv.lock`
- `.gitignore`
- `README.md`
- `examples/invoice_result.json`

## 5. Human Review Flow

```text
draft → POST /approvals → pending_review
pending_review → POST /{id}/approve → approved
pending_review → POST /{id}/reject  → rejected
```

- approve/reject 仅可从 pending 发起，重复决定和重复提交返回 409。
- 每个 artifact_id 只有一条审批记录。rejected 后需要重新生成新 artifact_id，开启新周期，旧记录保持不变。
- 提交时验证客户端提供的 IR/hash 与数据库快照；决定前重算哈希。
- ApprovalService.require_approved(approval_id, artifact_id) 是实际领域门禁：校验身份、状态、内容版本；即使历史记录是 approved，内容发生变化也拒绝。
- 没有执行 API；approved 表示人工确认该内容版本，不触发 SQL 执行或自动上线。
- reviewer/comment 来自请求。当前无登录认证，reviewer 是调用方声明身份，不能声称已具备身份认证或防冒名审批；应仅用于受控开发环境。

## 6. Approval Schema

approval_records：

| 字段 | 类型 / 约束 |
| --- | --- |
| approval_id | VARCHAR(36)，主键 |
| workflow_run_id | VARCHAR(36)，索引 |
| artifact_id | VARCHAR(36)，外键，唯一约束 uq_approval_artifact |
| artifact_version | VARCHAR(32) |
| metric_name | VARCHAR(64) |
| metric_ir_hash / artifact_hash | VARCHAR(64)，SHA-256 hex |
| reviewer | VARCHAR(128)，pending 时 NULL |
| decision | VARCHAR(16)：pending / approved / rejected |
| comment | VARCHAR(2000)，pending 时 NULL |
| created_at | DATETIME，服务端 UTC |
| reviewed_at | DATETIME，pending 时 NULL |
| revision | INTEGER，SQLAlchemy 乐观并发版本 |

复合索引 ix_approval_decision_created(decision, created_at)。

development_artifacts：

| 字段 | 类型 / 约束 |
| --- | --- |
| artifact_id | VARCHAR(36)，主键 |
| workflow_run_id | VARCHAR(36)，唯一 |
| artifact_version | VARCHAR(32) |
| metric_name | VARCHAR(64)，索引 |
| metric_ir_hash / artifact_hash | VARCHAR(64) |
| status | VARCHAR(20)：draft / pending_review / approved / rejected |
| snapshot | JSON，完整生成结果 |
| created_at | DATETIME，UTC |

状态条件更新与审批写入在同一事务中。数据库 DATETIME 存储无 offset 的 UTC，API 返回带 UTC 时区的时间。状态枚举由领域层控制；迁移可 upgrade/downgrade。未把 SQLite 结果当作 MySQL 方言或并发行为证明。

## 7. Atomic Metric IR

### invoice_amount_30d

```json
{
  "schema_version": "1.0.0",
  "name": "invoice_amount_30d",
  "display_name": "近30天企业开票金额",
  "description": "统计企业近30天开票金额之和",
  "entity_type": "enterprise",
  "entity_key": "seller_tax_no",
  "partition_field": "dt",
  "source": {
    "catalog": null,
    "database": "c_db",
    "table": "source_fp_jdc_view"
  },
  "aggregation": {
    "function": "sum",
    "field": "invoice_amt"
  },
  "dimensions": [],
  "filters": [],
  "window": {
    "size": 30,
    "unit": "day",
    "time_field": "invoice_date",
    "timezone": "Asia/Shanghai"
  }
}
```

### invoice_count_30d

```json
{
  "schema_version": "1.0.0",
  "name": "invoice_count_30d",
  "display_name": "近30天企业开票次数",
  "description": "企业近30日开票原始记录行数",
  "entity_type": "enterprise",
  "entity_key": "seller_tax_no",
  "partition_field": "dt",
  "source": {
    "catalog": null,
    "database": "c_db",
    "table": "source_fp_jdc_view"
  },
  "aggregation": {
    "function": "count",
    "field": null
  },
  "dimensions": [],
  "filters": [],
  "window": {
    "size": 30,
    "unit": "day",
    "time_field": "invoice_date",
    "timezone": "Asia/Shanghai"
  }
}
```

## 8. Derived Metric IR

依赖中的原子定义名称相同是有意设计：角色和 anchor_offset_days 区分当前与前期，避免重复定义同一金额口径。

```json
{
  "schema_version": "1.0.0",
  "metric_type": "derived",
  "name": "invoice_amount_growth_30d",
  "display_name": "近30天企业开票金额增长率",
  "description": "本30日开票金额相对前30日的增长率",
  "dependencies": [
    {
      "role": "current",
      "metric": {
        "schema_version": "1.0.0",
        "name": "invoice_amount_30d",
        "display_name": "近30天企业开票金额",
        "description": "统计企业近30天开票金额之和",
        "entity_type": "enterprise",
        "entity_key": "seller_tax_no",
        "partition_field": "dt",
        "source": {
          "catalog": null,
          "database": "c_db",
          "table": "source_fp_jdc_view"
        },
        "aggregation": {
          "function": "sum",
          "field": "invoice_amt"
        },
        "dimensions": [],
        "filters": [],
        "window": {
          "size": 30,
          "unit": "day",
          "time_field": "invoice_date",
          "timezone": "Asia/Shanghai"
        }
      },
      "anchor_offset_days": 0
    },
    {
      "role": "previous",
      "metric": {
        "schema_version": "1.0.0",
        "name": "invoice_amount_30d",
        "display_name": "近30天企业开票金额",
        "description": "统计企业近30天开票金额之和",
        "entity_type": "enterprise",
        "entity_key": "seller_tax_no",
        "partition_field": "dt",
        "source": {
          "catalog": null,
          "database": "c_db",
          "table": "source_fp_jdc_view"
        },
        "aggregation": {
          "function": "sum",
          "field": "invoice_amt"
        },
        "dimensions": [],
        "filters": [],
        "window": {
          "size": 30,
          "unit": "day",
          "time_field": "invoice_date",
          "timezone": "Asia/Shanghai"
        }
      },
      "anchor_offset_days": 30
    }
  ],
  "expression": {
    "operator": "growth_rate"
  },
  "zero_division": {
    "strategy": "null"
  }
}
```

## 9. Skill Plan

### invoice_amount_30d

```json
{
  "scenario_skill": {
    "name": "invoice_risk",
    "version": "1.0.0"
  },
  "capabilities": [
    {
      "name": "metric_window",
      "version": "1.0.0"
    },
    {
      "name": "metric_sum",
      "version": "1.0.0"
    },
    {
      "name": "spark_sql_generator",
      "version": "1.0.0"
    }
  ]
}
```

### invoice_count_30d

```json
{
  "scenario_skill": {
    "name": "invoice_risk",
    "version": "1.0.0"
  },
  "capabilities": [
    {
      "name": "metric_window",
      "version": "1.0.0"
    },
    {
      "name": "metric_count",
      "version": "1.0.0"
    },
    {
      "name": "spark_sql_generator",
      "version": "1.0.0"
    }
  ]
}
```

### invoice_amount_growth_30d

```json
{
  "scenario_skill": {
    "name": "invoice_risk",
    "version": "1.0.0"
  },
  "capabilities": [
    {
      "name": "metric_window",
      "version": "1.0.0"
    },
    {
      "name": "metric_sum",
      "version": "1.0.0"
    },
    {
      "name": "spark_sql_generator",
      "version": "1.0.0"
    },
    {
      "name": "metric_growth_rate",
      "version": "1.0.0"
    }
  ]
}
```

## 10. Tool Plan

### invoice_amount_30d

```json
{
  "tools": [
    {
      "name": "generate_spark_sql_metric",
      "version": "1.1.0"
    }
  ]
}
```

### invoice_count_30d

```json
{
  "tools": [
    {
      "name": "generate_spark_sql_metric",
      "version": "1.1.0"
    }
  ]
}
```

### invoice_amount_growth_30d

```json
{
  "tools": [
    {
      "name": "generate_spark_sql_metric",
      "version": "1.1.0"
    },
    {
      "name": "generate_growth_rate_sql",
      "version": "1.0.0"
    }
  ]
}
```

增长率计划列出原子和派生工具依赖：Workflow 调用派生工具，派生工具两次调用注册的原子工具，避免 Workflow 重复生成两次。DerivedMetricPlan.base_metrics 显式记录两个原子定义及各自 ExecutionContext。

## 11. Generated SQL

以下来自本次实际运行的 Mock API 产物。共同 anchor_time=2026-09-09T00:00:00+08:00。

### invoice_amount_30d

```sql
SELECT
    `seller_tax_no` AS entity_id,
    SUM(`invoice_amt`) AS `invoice_amount_30d`
FROM `c_db`.`source_fp_jdc_view`
WHERE
    `invoice_date` >= DATE '2026-08-10'
    AND `invoice_date` < DATE '2026-09-09'
GROUP BY `seller_tax_no`;
```

### invoice_count_30d

```sql
SELECT
    `seller_tax_no` AS entity_id,
    COUNT(*) AS `invoice_count_30d`
FROM `c_db`.`source_fp_jdc_view`
WHERE
    `invoice_date` >= DATE '2026-08-10'
    AND `invoice_date` < DATE '2026-09-09'
GROUP BY `seller_tax_no`;
```

### invoice_amount_growth_30d

```sql
WITH current_period AS (
SELECT
    `seller_tax_no` AS entity_id,
    SUM(`invoice_amt`) AS `invoice_amount_30d`
FROM `c_db`.`source_fp_jdc_view`
WHERE
    `invoice_date` >= DATE '2026-08-10'
    AND `invoice_date` < DATE '2026-09-09'
GROUP BY `seller_tax_no`
),
previous_period AS (
SELECT
    `seller_tax_no` AS entity_id,
    SUM(`invoice_amt`) AS `invoice_amount_30d`
FROM `c_db`.`source_fp_jdc_view`
WHERE
    `invoice_date` >= DATE '2026-07-11'
    AND `invoice_date` < DATE '2026-08-10'
GROUP BY `seller_tax_no`
)
SELECT
    COALESCE(c.entity_id, p.entity_id) AS entity_id,
    COALESCE(c.invoice_amount_30d, 0) AS current_amount,
    COALESCE(p.invoice_amount_30d, 0) AS previous_amount,
    CASE WHEN COALESCE(p.invoice_amount_30d, 0) = 0 THEN NULL
         ELSE 1.0 * (COALESCE(c.invoice_amount_30d, 0) - COALESCE(p.invoice_amount_30d, 0))
              / COALESCE(p.invoice_amount_30d, 0)
    END AS `invoice_amount_growth_30d`
FROM current_period c
FULL OUTER JOIN previous_period p ON c.entity_id <=> p.entity_id;
```

## 12. Growth Rate Semantics

- 当前窗口 [2026-08-10, 2026-09-09)，前期窗口 [2026-07-11, 2026-08-10)，不重叠。
- FULL OUTER JOIN 保留任一周期出现的主体。使用 Spark 空值安全相等 <=>，将两个周期的 NULL 主体合并为未知主体组；此规则写入警告，仍需业务确认。
- COALESCE 将缺失周期或 NULL 聚合金额映射为 0。
- previous_amount=0 时 CASE 返回 NULL；否则 1.0 * (current-previous)/previous，输出比率而非乘以100的百分数。
- current=150/previous=100 → 0.5；50/100 → -0.5；100/100 → 0。
- 仅当前出现 → NULL；仅前期出现且前期非零 → -1；两期都不存在则无输出主体。
- 红冲票、负金额及重复发票不擅自改写。负分母按既定公式计算，业务口径待审。
- dt 的分区格式和快照关系未知，未添加可能漏数的分区过滤；日期类型/会话时区仍需确认。

Artifact 实际 warnings：

```json
[
  "dt 的格式及与 invoice_date 的关系未确认；草稿未添加分区过滤，可能全表扫描。",
  "需确认 invoice_amt 为数值类型、invoice_date 为 DATE；若为 TIMESTAMP，需确认 Spark 会话时区为 Asia/Shanghai；字符串日期需另行确认格式。",
  "FULL OUTER JOIN 保留任一周期出现的企业；缺失周期金额及 NULL 聚合金额按 0 处理。",
  "previous period amount = 0 时增长率定义为 NULL，不定义为 0 或 100%。",
  "主体采用空值安全连接；两个周期的 NULL 主体归为同一未知企业组，需人工确认。",
  "企业发票风险：常用主体字段 seller_tax_no",
  "业务时间字段 invoice_date；分区字段 dt",
  "红冲票处理属于待确认业务规则",
  "发票重复属于潜在风险"
]
```

## 13. Validator Results

### invoice_amount_30d

```json
{
  "valid": true,
  "errors": [],
  "warnings": [
    "仅静态校验；未验证源表、字段类型、数据质量或 Spark 执行结果。"
  ]
}
```

### invoice_count_30d

```json
{
  "valid": true,
  "errors": [],
  "warnings": [
    "仅静态校验；未验证源表、字段类型、数据质量或 Spark 执行结果。"
  ]
}
```

### invoice_amount_growth_30d

```json
{
  "valid": true,
  "errors": [],
  "warnings": [
    "仅静态校验；未验证 Spark 类型、数据或执行结果。"
  ]
}
```

Atomic SQLValidator 保持受限单 SELECT 语法，仅增加 COUNT(*)。DerivedSQLValidator 只接受固定两个 CTE、规定的 FULL OUTER JOIN、COALESCE、CASE 和增长率投影，逐一调用原子 Validator 检查 CTE 的字段和边界。DDL/DML、UNION、UDF、额外语句、任意子查询或更改除零策略均拒绝。静态通过不是 Spark 编译/执行通过。

## 14. Approval Example

实际本地 Mock 示例：generate 返回的 artifact_id=b53a0923-4f6a-411f-bf54-79a265f39c81，workflow_run_id=232637d5-5c59-4698-84d7-eb7277a7e8d7。

POST /api/v1/approvals：

```json
{
  "artifact_id": "b53a0923-4f6a-411f-bf54-79a265f39c81",
  "artifact_hash": "c342958daedcb1ecab265d780f007611168e1a669a71dad67712d9751aa99646",
  "metric_ir_hash": "d128a0b8e26ef05df403758b6740deecc9e4a6bb3653de8ffa6a5e48d87c5846"
}
```

返回 pending：

```json
{
  "approval_id": "1e6196a1-7f4d-47f5-9f3b-4d837dea27d7",
  "workflow_run_id": "232637d5-5c59-4698-84d7-eb7277a7e8d7",
  "artifact_id": "b53a0923-4f6a-411f-bf54-79a265f39c81",
  "artifact_version": "1.0.0",
  "metric_name": "invoice_amount_30d",
  "metric_ir_hash": "d128a0b8e26ef05df403758b6740deecc9e4a6bb3653de8ffa6a5e48d87c5846",
  "artifact_hash": "c342958daedcb1ecab265d780f007611168e1a669a71dad67712d9751aa99646",
  "reviewer": null,
  "decision": "pending",
  "comment": null,
  "created_at": "2026-09-09T09:51:52.504636Z",
  "reviewed_at": null
}
```

POST /api/v1/approvals/1e6196a1-7f4d-47f5-9f3b-4d837dea27d7/approve：

```json
{
  "reviewer": "iris-demo",
  "comment": "本地 Mock 演示审批，非生产授权"
}
```

返回：

```json
{
  "approval_id": "1e6196a1-7f4d-47f5-9f3b-4d837dea27d7",
  "workflow_run_id": "232637d5-5c59-4698-84d7-eb7277a7e8d7",
  "artifact_id": "b53a0923-4f6a-411f-bf54-79a265f39c81",
  "artifact_version": "1.0.0",
  "metric_name": "invoice_amount_30d",
  "metric_ir_hash": "d128a0b8e26ef05df403758b6740deecc9e4a6bb3653de8ffa6a5e48d87c5846",
  "artifact_hash": "c342958daedcb1ecab265d780f007611168e1a669a71dad67712d9751aa99646",
  "reviewer": "iris-demo",
  "decision": "approved",
  "comment": "本地 Mock 演示审批，非生产授权",
  "created_at": "2026-09-09T09:51:52.504636Z",
  "reviewed_at": "2026-09-09T09:51:52.510215Z"
}
```

GET /api/v1/development/artifacts/{artifact_id} 返回当前 artifact.status=approved，原始生成快照保持 draft。所有示例审批均明确是本地演示，不是生产授权。

离线复现：`uv run --frozen python examples/demo_phase15.py`。脚本只操作 .demo/reviews.db 和 examples，Mock LLM 不发网络请求；会为三个指标分别生成、提交、审批并保存完整样例。

## 15. Test Results

本次真实命令结果：

| 命令 | 结果 |
| --- | --- |
| uv run --frozen pytest | 222 passed，2 warnings |
| uv run --frozen pytest --cov=airi --cov-report=term-missing | 222 passed，行覆盖率 97%（1141 statements，36 missed） |
| uv run --frozen ruff check src tests | All checks passed |
| uv run --frozen ruff format --check src tests | 61 files already formatted |
| uv run --frozen alembic upgrade head | 成功，独立 SQLite 检查库 |
| uv run --frozen alembic current | 0001_reviews (head) |
| uv run --frozen alembic check | No new upgrade operations detected |

单元测试另外验证迁移 upgrade → downgrade → upgrade、索引/唯一约束/外键。增长率通过 SQLite 关系语义校验（仅替换 Spark DATE 字面量和 <=> 的等价语法）验证上涨、下降、持平、零分母和两类缺失周期，不执行 Hive/Spark，不宣称验证了 Spark 方言行为。已有测试中的 SUM-only 断言按新需求调整为拒绝 avg；原有失败路径仍保留。

两条 warnings 来自 Starlette TestClient/httpx 与 anyio 的弃用提示。首次沙箱测试因临时目录访问失败，已获得授权后执行完整测试；依赖安装也已完成并锁定。

## 16. Known Limitations

- 未接真实 LLM、MySQL、Hive Metastore 或 Spark 集群验证。LLM 理解质量不能由 Mock 测试证明。
- 无身份认证/权限体系；reviewer 是自报值。单级审批、每个 artifact 一个周期，不支持原地编辑、重新提交旧 artifact、取消、过期、多级会签。
- 哈希用于版本一致性，不是加密签名或抵御数据库管理员篡改的机制。
- 只支持这三个固定业务指标、上海零点 anchor、30日自然日窗口；不支持任意 derived operator 或 DAG。
- 字段类型、分区语义、红冲票、去重口径尚需业务确认。
- API 生成结果需要先迁移数据库；没有自动 create_all，也没有自动 SQLite fallback。
- 未实现 LangGraph、Multi-Agent、ReAct、Experiment、Reflection、Threshold、RAG、Memory、向量库、GitLab、前端、Scala、指标探索、阈值搜索、监控、执行或上线。

## 17. Recommended Next Step

只建议下一阶段补齐审批人的真实身份认证与授权，将 reviewer 绑定可信登录身份，使现有内容版本审批门禁可以安全供团队使用。本次不继续开发该阶段。

参考：[Alembic 迁移环境](https://alembic.sqlalchemy.org/en/latest/tutorial.html)、[Spark JOIN](https://spark.apache.org/docs/latest/sql-ref-syntax-qry-select-join.html)。
