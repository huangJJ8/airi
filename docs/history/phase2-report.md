# AIRI Phase 2 Implementation Report

本报告覆盖已审批发票指标的受控执行和确定性测试。实现不包含生产执行；真实 Spark/Hive 集群尚未连接。

## 1. Repository Assessment

根据实际源代码而非仅 README 确认：

1. `ApprovalService.get_artifact` 从 `DevelopmentArtifactRow` 读取快照，校验后返回当前审批状态。执行新增服务通过同一审批服务的门禁，再读取快照。
2. `require_approved` 核验审批绑定的 artifact_id、审批 decision 和产物 status，两者必须 approved；重新计算 IR/产物哈希。不允许 draft、pending、rejected。
3. 快照保存完整 `MetricIR | DerivedMetricIR`；派生指标的两份依赖 IR 也在其中。
4. SQL 位于 `snapshot.artifact.code`，生成端只保存 draft。没有让调用者提交 SQL 的执行入口。
5. `snapshot.execution_context` 保存 anchor_time/timezone；执行计划直接使用它，静态检查再次验证窗口和 SQL 一致。
6. IR 使用 canonical JSON SHA-256；产物哈希涵盖代码、模板版本、警告和指标名等。新增 `sql_hash` 专指 SQL 文本 UTF-8 SHA-256。时间上下文原本不单独纳入审批哈希，因此执行门禁用与 SQL 的窗口一致性补充验证。
7. FastAPI lifespan 管理 Database；每请求独立 Session，服务负责 commit，异常 rollback，请求结束关闭会话。使用 Alembic，不在启动时 create_all。
8. 应用异常返回统一 code/message/request_id；新增 sandbox_violation 403、test_report_not_found 404。执行失败作为结构化结果保存，不泄露 provider 原文。
9. ToolRegistry 适合现有带输入/输出 Schema 和版本的确定性生成工具。审批执行必须依赖会话和安全门禁，因此采用 ExecutionService，不提供通用任意执行 Tool。
10. evaluation/experiments/memory 原先均为占位；新增独立 testing 包，没有实现 Experiment、Reflection 或 Memory。

## 2. Architecture Changes

保持原子/派生 IR、生成模板、审批状态机不变，增加 execution、testing、workflows/testing 三个模块。使用普通 Python 编排，无新 Agent、LLM 测试或 DAG 框架。

ScenarioSkill 增加严格 `ScenarioTestRules`，null 默认 warning，结果重复默认 error，空结果默认 warning。六类测试由一个 planner/runner 负责，不为六种检测建立无行为的 Capability/Tool 包装；这是任务允许的简化。

## 3. Added Files

- `src/airi/execution/`：`__init__.py`、`models.py`、`sandbox.py`、`service.py`、`persistence.py`。
- `src/airi/testing/`：`__init__.py`、`models.py`、`planning.py`、`runner.py`、`reconciliation.py`、`failure.py`、`persistence.py`。
- `src/airi/workflows/testing/`：`__init__.py`、`workflow.py`。
- `src/airi/infrastructure/query_executor.py`。
- `src/airi/api/executions.py`、`src/airi/api/tests.py`。
- `migrations/versions/0002_execution_testing_execution_testing.py`。
- `tests/test_phase2.py`、`tests/fixtures/invoices.json`。
- `examples/demo_phase2.py`、`examples/phase2/` 下三个指标各自的 request、execution、testing JSON，共九份实际演示产物。
- `phase2-report.md`。

## 4. Modified Files

`src/airi/main.py`（路由和 executor 注入）、`core/config.py`（测试连接配置）、`skills/models.py`（场景测试规则）、`observability/logging.py`（关联字段）、`api/development.py` 和 `api/approvals.py`（生成/审批关联日志）、`migrations/env.py`（新 metadata）、`pyproject.toml` 和 `uv.lock`（可选 spark 依赖）、`.env.example`、`README.md`。

原有 `0001_reviews.py` 未修改。没有修改生成模板或原指标业务口径。

## 5. Execution Architecture

```text
HTTP POST /api/v1/executions
→ ExecutionService.run
→ ApprovalService.require_approved
→ snapshot → MetricIR / DerivedMetricIR + ExecutionContext
→ SandboxGuard.validate
→ ExecutionPlan + execution_runs(running)
→ QueryExecutor.execute
→ ExecutionResult + execution_runs(success/failed/timeout)
→ HTTP Response

HTTP POST /api/v1/tests/run
→ TestingWorkflow.run → 同一 ExecutionService
→ MetricTestPlanner.plan → MetricTestRunner.run
→ classify → MetricTestReport → 三张新增表保存元数据
→ TestingResponse(execution, report) → Human Review
```

`QueryExecutor` 是基础设施接口；默认 DisabledQueryExecutor；测试明确注入 MockQueryExecutor。SparkSQLExecutor 使用隔离子进程连接 Spark Thrift Server，限制本地连接/执行/获取结果的时间，游标只 fetchmany(max_rows+1)。生成和审批接口不会自动执行。

## 6. ExecutionProfile

唯一 profile：

```json
{
  "name": "spark_test", "version": "1.0.0", "engine": "spark_sql",
  "environment": "test", "read_only": true,
  "allowed_databases": ["c_db", "tmp_db"],
  "blocked_databases": ["prod_sensitive_db"],
  "max_rows": 1000, "default_rows": 100, "max_seconds": 60
}
```

API 不接受 profile 自定义字段；可选 max_rows=1..1000、timeout_seconds=1..60。ExecutionPlan 保存 profile_version、request/workflow/artifact/approval/execution IDs、anchor_time/timezone 和 sql_hash。

## 7. Sandbox Rules

仅允许三种已支持指标的 SELECT 和增长率 WITH SELECT，包含固定 GROUP BY、两个 DATE 半开区间以及既有 FULL OUTER JOIN / `<=>`。数据库范围是上限，当前指标仍必须来自 IR 固定的 c_db.source_fp_jdc_view，tmp_db 并不意味着可提交任意查询。

显式拒绝 INSERT、UPDATE、DELETE、DROP、ALTER、CREATE、TRUNCATE、MERGE、REPLACE、LOAD、EXPORT、IMPORT、CACHE TABLE、UNCACHE、REFRESH、SET、ADD JAR、ADD FILE、DFS，以及 reflect、java_method、xpath、input_file_name。禁止多语句、评论注入、未匹配的子查询、UNION、自由 UDF 和 SQL/IR 不一致。

采用现有完整匹配的闭合语法校验，而不是仅关键词搜索。它已支持本任务 DATE、CTE、FULL OUTER JOIN、`<=>`，因此没有引入通用 sqlglot 解析器扩大接受范围。关键词拒绝只是附加防线。

## 8. Database Schema

新迁移 `0002_execution_testing`，父版本 `0001_reviews`：

| 表 | 保存内容 |
|---|---|
| execution_runs | execution_run_id PK，workflow/artifact/approval IDs，profile、engine、sql_hash、status、开始结束时间、duration_ms、row_count、truncated、failure_category、安全 error_message、created_at、metadata_json |
| metric_test_runs | test_run_id PK，execution/artifact FK，status、total/passed/warnings/failed/skipped、创建结束时间、report_json |
| metric_test_results | test_case_id PK，test_run FK，test_type/status/severity/failure_category、expected_summary/actual_summary、details_json、created_at |

不保存原始结果行或整份 SQL 到新表。SQL 留在既有产物快照；测试明细只保存计数和固定摘要，不含主体值。report_json 用于完整报告读取，测试结果另存明细表便于查询。执行崩溃留下 running 记录的恢复不属于本阶段。

## 9. Execution API

以下是已实际运行的演示请求，ID 对应 `.demo/phase2_demo.db` 的历史记录，新环境需重新运行 demo：

```json
{
  "artifact_id": "b0f99dcd-ec5c-40d1-81c8-09ed0be32cd6",
  "approval_id": "3742a449-975b-40cd-b736-e86fbf28bae3",
  "execution_profile": "spark_test"
}
```

`POST /api/v1/executions` 实际响应摘录：

```json
{
  "execution_run_id": "328f4677-9d74-48b0-941d-701a96607b82",
  "status": "success", "row_count": 6, "truncated": false,
  "columns": [{"name":"entity_id","type":"string"},
              {"name":"invoice_amount_30d","type":"double"}],
  "failure_category": null, "error_message": null
}
```

完整响应含 IDs、资源上限、时间上下文、起止时间、duration、bounded rows、warnings；见 [实际执行响应](../../examples/phase2/invoice_amount_30d_execution.json)。row_count 表示返回行数，不是截断前全量行数。执行失败也返回 HTTP 200 的 failed/timeout 结果；安全拒绝 403/409、非法请求 422、不存在记录 404。

## 10. Metric Test Model

严格 Pydantic 模型：ExecutionProfile、ExecutionPlan、ExecutionRun、ExecutionResult、MetricTestCase、MetricTestPlan、MetricTestResult、FailureClassification、MetricTestReport。禁止未知字段和隐式宽松转换。

| 类型 | 检查 | 证据/严重度 |
|---|---|---|
| schema | 列名、顺序及类型；额外列拒绝 | error；entity 字符串，count 整数，金额/增长率数值 |
| null | 返回结果空主体数量 | 默认 warning；截断且未发现时 skipped |
| duplicate | 最终 entity_id 是否重复，含 NULL | 默认 error；源表重复保留，截断无重复不能证明全量唯一 |
| window | SQL、IR 和 anchor 对应的完整窗口 | error；派生指标检查两期 |
| boundary | 左边界包含、右边界排除，前后期独立探针 | error；SQLite fixture 证据，不声称 Spark 等价 |
| reconciliation | SQL fixture 结果与独立 Python Decimal 计算对账 | error，tolerance=1e-8；无参考数据/截断时 skipped |

空结果是 execution success，不默认 fail；结果相关测试 warning/empty_result。任何 error 失败或执行失败导致报告 failed；warning 或 skipped 导致 passed_with_warnings；六项全部通过才 passed。

## 11. Test Plan Example

```json
{"tests":[
  {"type":"schema","severity":"error"},
  {"type":"null","severity":"warning"},
  {"type":"duplicate","severity":"error"},
  {"type":"window","severity":"error"},
  {"type":"boundary","severity":"error"},
  {"type":"reconciliation","severity":"error"}
]}
```

真实 plan 还包含 test_plan_id、artifact_id、execution_run_id 和每个 case 的唯一 ID，见下方完整报告链接。没有第二次 LLM 调用。

## 12. Test Report Example

```json
{
  "test_run_id": "9162f762-e041-456c-9b56-78fd22c05c76",
  "status": "passed_with_warnings", "total": 6,
  "passed": 5, "warnings": 1, "failed": 0, "skipped": 0,
  "requires_human_review": true
}
```

唯一 warning 为 fixture 中 1 个 NULL 主体；没有隐藏或自动删除该主体。完整 [金额测试报告](../../examples/phase2/invoice_amount_30d_testing.json) 可重现；`GET /api/v1/tests/{test_run_id}` 返回已保存报告，未找到时 test_report_not_found。

## 13. invoice_amount_30d Test Result

SQL fixture 结果 6 行，schema/window/boundary/duplicate/reconciliation 全部 passed，null warning。固定 anchor=2026-09-09T00:00:00+08:00，当前窗口 [2026-08-10,2026-09-09)。

| entity | amount |
|---|---:|
| A | 300 |
| B | 50 |
| C | 100 |
| CURRENT_ONLY | 25 |
| ZERO | -10 |
| NULL | 20 |

## 14. invoice_count_30d Test Result

6 行，5 passed + 1 warning；A=3，其余当前期主体各 1。A 的重复原始行计入 COUNT(*)，没有自动去重。完整 [次数测试报告](../../examples/phase2/invoice_count_30d_testing.json)。

## 15. invoice_amount_growth_30d Test Result

7 行，5 passed + 1 warning。前期 [2026-07-11,2026-08-10)，当前期同上。

| entity | current | previous | growth |
|---|---:|---:|---:|
| A | 300 | 100 | 2 |
| B | 50 | 100 | -0.5 |
| C | 100 | 100 | 0 |
| CURRENT_ONLY | 25 | 0 | NULL |
| PREVIOUS_ONLY | 0 | 20 | -1 |
| ZERO | -10 | 0 | NULL |
| NULL | 20 | 10 | 1 |

覆盖增长、下降、持平、仅当前期、仅前期、零分母，以及空主体连接。完整 [增长率测试报告](../../examples/phase2/invoice_amount_growth_30d_testing.json)。

## 16. Failure Classification

支持 syntax_error、schema_error、data_type_error、permission_error、timeout、resource_limit、empty_result、duplicate_entity、window_mismatch、boundary_mismatch、reconciliation_mismatch、execution_error、unknown。

真实单元测试注入语法错误、未知列、超时、权限和资源错误；确定性检测测试修改窗口、边界、金额并验证相应分类。失败摘要例如 `Test execution failed (timeout); contact the test environment operator`。分类附带 possible_causes 和 recommended_actions，明确原因未被证实；不由 LLM 推断事实。Provider 原始消息仅用于内部分类，不进入响应、数据库或应用日志。

## 17. Security Boundaries

应用不能从 API 接收生产 profile、任意 SQL、连接地址或不受限参数。默认执行禁用；只读闭合 SQL、审批状态与哈希、防多语句、白名单、固定时间、结果上限构成执行门禁。测试日志关联 request/workflow/artifact/approval/execution/test IDs，不写 SQL 或结果值。

这不是对远端环境身份或权限的密码学保证。管理员仍须配置真实隔离测试端点和只读身份；当前 NOSASL 适配器没有 TLS/Kerberos。既有 reviewer 自报身份尚未实现认证授权，不能部署为公开审批执行服务。生产环境配置不会自动启用真实执行器。

## 18. Test Results

2026-09-11 最终验收结果：

| 实际命令 | 结果 |
|---|---|
| `uv run --frozen pytest` | **305 passed**, 2 warnings，12.50s |
| `uv run --frozen pytest --cov=airi --cov-report=term-missing` | **305 passed**, 2 warnings，23.07s；**96%**，1744 statements / 71 missed |
| `uv run --frozen ruff check src tests` | All checks passed! |
| `uv run --frozen ruff format --check src tests` | 79 files already formatted |
| `uv run --frozen alembic upgrade head` | exit 0 |
| `uv run --frozen alembic current` | 0002_execution_testing (head) |
| `uv run --frozen alembic check` | No new upgrade operations detected. |

Phase 2 新增 83 项测试，包括三个指标的审批→执行→测试、未审批拒绝、内容篡改、危险语句、数据库白名单、Schema、空主体、重复、窗口/边界变异、独立对账、超时/错误持久化、截断、错误脱敏及适配器游标/子进程边界。保留此前 222 项回归。

两个 warnings 为 Starlette/httpx 与 anyio BlockingPortal 第三方弃用提示。测试全部使用 Mock LLM，不需要 API Key。迁移验证使用独立 SQLite `.demo/phase2_migration.db`，不连接默认 MySQL。覆盖率是应用代码行覆盖率，不代表 Spark 实库覆盖。

## 19. Known Limitations

- 未真实连接 Spark、Hive、MySQL 或 Metastore。适配器验证是 mocked provider/process 单元测试；可选依赖 PyHive 0.7.0 和 thrift 0.24.0 已安装。
- SQLite fixture 翻译 DATE literal / `<=>`，仅验证受控关系语义；Mock schema 类型为约定值，不能证明真实源字段类型。独立 Python oracle 使用 Decimal，SQLite 数值计算仍不是 Spark Decimal 精度认证。
- 真实执行无匹配 reference dataset，reconciliation 会 skipped；boundary 仅本地 fixture。返回截断时不作全量正确性结论。
- 子进程截止时间约束本地等待，清理有短暂额外开销。游标失败尽力 cancel，但强制终止连接不能保证远端任务结束；需要集群端超时和资源策略验证。
- 尚无全局并发配额、作业重试、崩溃恢复或认证授权；不支持公开、多租户或生产使用。
- 红冲、源数据去重、dt 分区口径、NULL 金额及类型转换仍需业务确认。金额0/缺期/零分母沿用 Phase 1.5 口径。
- 报告标记 requires_human_review=true；没有新增报告审批状态机、自动上线或自动修改 SQL。
- 未实现 LangGraph/Multi-Agent、Experiment/Reflection、Threshold、Memory/RAG、GitLab、前端、Scala、调度或生产执行。

## 20. Recommended Next Step

只推荐隔离 Spark 测试集群集成验收：由运营人员提供受控只读端点与同一 fixture 数据，验证真实字段类型、DATE/时区、FULL OUTER JOIN、Decimal 精度和超时取消；获得真实证据后再决定扩展能力。本次不继续开发下一阶段。
