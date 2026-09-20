# AIRI Phase 2.5 Implementation Report

本阶段完成真实环境接入代码、只读探针、元数据校验、可重复集成测试入口及证据报告。**当前没有可用的真实 Spark/Hive/MySQL 配置，因此真实环境验收为 NOT VERIFIED。** 本地测试通过不代表真实环境已验收。

## 1. Repository Assessment

开发前阅读 execution、testing、query_executor、workflows/testing、approvals、Settings、tests、examples 和 migrations 的实际实现，确认：

1. `QueryExecutor` 是 ABC，不是 typing.Protocol；契约为 `execute(code, ExecutionPlan) -> QueryOutput`，另有可选 fixture_rows。
2. 原 SparkSQLExecutor 已有 PyHive/Thrift NOSASL 子进程适配器，但没有真实元数据/能力预检；没有实库证据。
3. 原 timeout 由父进程 poll 截止时间、子进程 socket 超时和游标轮询控制；失败尽力 cancel，不能保证 Spark Job 消失。
4. 原 fetchmany(max_rows+1) 后裁切；row_count 表示返回行数。新增配置上限与请求上限取较小值。
5. provider 错误原来通过固定关键词分类，响应仅返回安全类别；本阶段继续这一边界。
6. ExecutionRun 在执行前保存 running，结束更新 success/failed/timeout；SQLAlchemy Session 负责提交，结果行不持久化。
7. 原 schema 来自 cursor.description；Mock 类型为约定值。原 Decimal 可由 Pydantic 编码，但日期类型、精度说明和 provider 元数据不完整。
8. runner 要求准确列名和顺序，拒绝额外列；entity 为字符串，count 为整数，金额/增长率为数值。截断不能证明全量唯一或对账通过。
9. reference calculator 使用独立 Python 半开窗口和 Decimal，未使用同一个 SQL 模板；本阶段补齐 NULL amount 的 SQL 聚合语义。
10. create_app 已支持 executor 注入。保留此机制，并增加明确 disabled/mock/spark_test 模式。

## 2. Architecture Changes

领域 IR 和现有三个指标不变。新增 environments 模块，负责固定探针、源 Schema、环境证据、fixture 映射和验收报告。数据库新增一张环境验收表，无 Redis、缓存或 Agent 框架。

业务层继续只依赖 QueryExecutor；PyHive、Thrift、Connection 和 ResultNormalizer 均属于基础设施边界。环境验收使用固定内部查询，不开放任意 SQL/元数据浏览。

## 3. Added Files

- `src/airi/environments/__init__.py`
- `src/airi/environments/models.py`：Evidence、PartitionAdvisory、SourceSchema、SemanticParityReport、EnvironmentStatus、EnvironmentValidationReport。
- `src/airi/environments/fixtures.py`、`invoices.json`：固定 FixtureSourceMapping 和可打包的合成数据。
- `src/airi/environments/probes.py`：固定只读查询 allowlist。
- `src/airi/environments/metadata.py`：SchemaInspector、MetricMetadataValidator、fixture 数据集校验。
- `src/airi/environments/service.py`：SparkEnvironmentProbe、三方对账及报告持久化入口。
- `src/airi/environments/persistence.py`：EnvironmentValidationRow。
- `src/airi/infrastructure/result_normalizer.py`。
- `src/airi/api/environments.py`。
- `migrations/versions/0003_environment_validation_environment_validation.py`。
- `tests/test_phase25.py`、`tests/test_spark_integration.py`、`tests/test_mysql_integration.py`。
- `examples/spark_fixture_setup.sql`、`examples/environment_status.py`、`examples/environment_validation_report.json`。
- `phase25-report.md`。

## 4. Modified Files

`core/config.py`、`main.py`、`infrastructure/query_executor.py`、`execution/models.py`、`execution/service.py`、`testing/reconciliation.py`、`testing/failure.py`、`observability/logging.py`、`migrations/env.py`、`tests/conftest.py`、`pyproject.toml`、`.env.example`、`README.md`。

没有修改 0001/0002 迁移，没有修改业务 SQL 模板、IR 口径或加入新指标。依赖沿用 Phase 2 已锁定的可选 PyHive/thrift，无新增外部平台。

## 5. Spark Executor Architecture

```text
TestingWorkflow → ExecutionService
→ require_approved + artifact hash + SandboxGuard
→ SparkSQLExecutor → 隔离子进程 / NOSASL connection
→ 同会话能力探针 + actual timezone + DESCRIBE + MetadataValidator
→ 可选固定 fixture 映射 / 精确核对 fixture 原始行集合
→ query → ResultNormalizer → bounded QueryOutput
→ ExecutionResult → MetricTestRunner → Report → Human Review
```

同会话预检避免用另一个连接的时区替实际指标会话背书。映射前审批的是业务 SQL；执行 metadata 同时记录原 SQL hash、mapped executed_sql_hash 和 source_mapping。业务 IR、已审批产物和其内容哈希不被改写。

## 6. Environment Configuration

```dotenv
AIRI_ENVIRONMENT=test
AIRI_EXECUTION_MODE=spark_test
AIRI_SPARK_HOST=<operator-provided-isolated-test-host>
AIRI_SPARK_PORT=10000
AIRI_SPARK_USERNAME=<test-reader>
AIRI_SPARK_PASSWORD=
AIRI_SPARK_AUTH_MODE=NOSASL
AIRI_SPARK_DATABASE=c_db
AIRI_SPARK_CONNECT_TIMEOUT_SECONDS=10
AIRI_SPARK_QUERY_TIMEOUT_SECONDS=60
AIRI_SPARK_SESSION_TIMEZONE=Asia/Shanghai
AIRI_SPARK_MAX_ROWS=1000
AIRI_SPARK_FIXTURE_MAPPING=true
AIRI_SPARK_READ_ONLY_ATTESTED=true
```

默认 disabled，不自动连接；mock 必须显式选择。旧 AIRI_QUERY_EXECUTOR 和 AIRI_SPARK_TEST_* 保留兼容，显式新模式优先。密码为 SecretStr；NOSASL 不使用密码。LDAP/KERBEROS 仅为明确的扩展点，选择后拒绝执行，未声称已支持。

只读身份由运营人员确认。不得把示例值当作真实连接信息。AIRI 不会以 DROP/DELETE 验证权限。环境标签不能验证远端身份；当前缺少认证/RBAC/TLS，API 仅限受控内部隔离环境。

## 7. Environment Probe

- `GET /api/v1/environments/spark-test/status`：reachable、engine_version、session_timezone、database、read_only_expected，不返回 host/username/凭证。
- `POST /api/v1/environments/spark-test/validate`：无请求体或 `{}`；拒绝 host、SQL、密码等额外字段，报告保存到数据库。
- 两接口要求非 production 且 execution mode=spark_test；disabled/mock 返回 403。
- `/health` 仍只表示 AIRI 进程健康。

当前真实连接：**NOT VERIFIED**。可运行 `uv run --frozen python examples/environment_status.py` 生成本地状态报告；disabled 模式会生成未配置报告，不访问集群，不使用 Mock 填充结果。

## 8. Spark Capability Matrix

| 项目 | 内部最小探针 | 当前真实状态 |
|---|---|---|
| 连接 | SELECT 1 | NOT VERIFIED |
| 版本/时区 | version() / current_timezone() | NOT VERIFIED |
| DATE literal | 固定 DATE '2026-09-09' | NOT VERIFIED |
| CASE | 常量条件，期待 7 | NOT VERIFIED |
| COALESCE | NULL 与常量，期待 7 | NOT VERIFIED |
| `<=>` | NULL 空值安全比较，期待 1 | NOT VERIFIED |
| CTE | 单行 CTE | NOT VERIFIED |
| FULL OUTER JOIN | 两个不同常量键，期待 2 行 | NOT VERIFIED |
| COUNT(*) | 含重复和 NULL 的 VALUES，期待 3 | NOT VERIFIED |
| SUM | 含 NULL 的 VALUES，期待 3 | NOT VERIFIED |

所有能力探针为常量小查询，不扫描业务大表。固定 DESCRIBE 仅开放业务源和已启用映射的 fixture 表；未开放通用 SHOW。参考 [Spark 内置函数文档](https://spark.apache.org/docs/latest/sql-ref-functions-builtin.html)，但函数在目标集群的支持情况必须由探针判定，不能由文档推断。

## 9. Source Schema Result

目标固定为 `c_db.source_fp_jdc_view`。

| 字段 | 实际类型 |
|---|---|
| seller_tax_no | NOT VERIFIED |
| invoice_amt | NOT VERIFIED |
| invoice_date | NOT VERIFIED |
| dt | NOT VERIFIED |

SchemaInspector 读取 DESCRIBE，记录列类型、Decimal precision/scale 与逻辑源是否声明分区。缺表/缺字段失败；金额 STRING、时间 STRING 等返回 unsupported_source_schema。绝不自动 cast 或 to_date。

## 10. Timezone Validation

目标 Asia/Shanghai。移除了 Phase 2 建连时设置 session timezone 的行为，不发送 SET，也不通过 connection configuration 覆盖。

读取 current_timezone() 并校验；不匹配时保守拒绝 DATE/TIMESTAMP 两种源。TIMESTAMP 与 DATE literal 的目标集群比较行为仍需真实环境证据。真实时区 **NOT VERIFIED**；Mock 的 Asia/Shanghai 仅用于分支测试。

## 11. Decimal Validation

ResultNormalizer 将 Decimal 精确编码为十进制字符串；int/float 保持原类型，date/datetime 使用 ISO 8601；拒绝非有限数和不支持对象。未统一转 float。

源 `decimal(p,s)` 记录 precision/scale；结果 cursor.description 有精度信息时保留。独立 `SUM(DECIMAL(18,2))` 探针检查 0.10+0.20=0.30 并记录 Spark typeof 的实际返回类型，不假定仍是 DECIMAL(18,2)。三方报告也记录真实结果列类型。

真实 invoice_amt 和 SUM 返回精度：**NOT VERIFIED**。

## 12. Partition Advisory

PartitionAdvisory 包含 dt 是否存在、类型、是否声明分区、format、relation_to_business_time=unknown、manual_review_required。DESCRIBE 检查逻辑源，不推断底层表关系。

fixture 的小数据集会检查 dt 样本是否为 yyyyMMdd。业务源不主动扫描数据取样，因此业务 dt 样本格式保留 NOT VERIFIED；没有把 fixture 的 dt 关系外推到业务源。没有增加分区过滤或自动 SQL 修改。

## 13. Fixture Dataset

`src/airi/environments/invoices.json` 保留 Phase 2 的 16 条数据，增加 A 在 2026-08-10 的 NULL amount 行，共 17 行。含 A/B/C/CURRENT_ONLY/PREVIOUS_ONLY/ZERO/NULL 主体，六个关键日期，正/零/负值、重复和 NULL 金额。

[operator setup SQL](../../examples/spark_fixture_setup.sql) 由运维在隔离测试集群手动执行，建 `tmp_db.airi_invoice_fixture`，invoice_date DATE、invoice_amt DECIMAL(18,2)、dt STRING 分区。AIRI API 不执行 CREATE/INSERT。

映射仅为 `c_db.source_fp_jdc_view → tmp_db.airi_invoice_fixture`，需服务器显式启用，不接受 API 动态表名。执行前以 bounded fetch 对照原始 fixture 多重集，检查重复行、NULL 和格式，数据不符则 fixture_mismatch。

## 14. invoice_amount_30d Real Result

真实结果 **NOT VERIFIED**。独立 fixture 预期：A=300、B=50、C=100、CURRENT_ONLY=25、ZERO=-10、NULL=20。NULL amount 不改变 SUM；这些是预期值，不是 Spark 实测值。

## 15. invoice_count_30d Real Result

真实结果 **NOT VERIFIED**。fixture 预期 A=4（包括重复及 NULL amount），其余当前期主体各 1。没有 DISTINCT 或预过滤。

## 16. invoice_amount_growth_30d Real Result

真实结果 **NOT VERIFIED**。预期 A=2、B=-0.5、C=0、CURRENT_ONLY=NULL、PREVIOUS_ONLY=-1、ZERO=NULL、NULL 主体=1。固定 anchor 为 2026-09-09T00:00:00+08:00；窗口沿用当前 [08-10,09-09)、前期 [07-11,08-10)。

## 17. Python / SQLite / Spark Semantic Parity

SemanticParityReport 分别保存 sqlite_vs_python、spark_vs_python、spark_vs_sqlite、result_types、differences。差异报告记录匹配状态、行数和截断，不保存敏感主体或原始数据行。

Python/SQLite 本地三个指标一致；Spark 两项比较 **NOT VERIFIED**。SQLite 不是 Spark 方言或 MySQL 的证明。环境验收内部仅执行固定生成的三项 fixture SQL；业务执行仍使用完整审批门禁。

## 18. Timeout & Cancellation Result

- 客户端截止时间：本地单元测试已验证父进程、游标超时与取消路径；真实超时 **NOT VERIFIED**。
- cancel requested：真实小型 SELECT cancel 探针已实现，但当前无集群，**NOT VERIFIED**。
- remote cancellation：可记录远端 operation 状态 cancelled/finished/not_verified；即便观察 operation cancelled，也不等同证明所有 Spark 子任务消失。
- 不启动故意耗费集群资源的长查询，真实 client timeout 证据保持 not_verified。强制终止客户端不能保证远端停止，报告保留 remote_cancellation_not_guaranteed。

QueryOutput/ExecutionResult 保存可选 provider_query_id、cancel_requested、remote_cancel_status；取消失败不会伪装成功。Provider 原始错误不进入报告。

## 19. Resource Limit Result

客户端请求与服务器上限取较小值；fetchmany(N+1)，最多返回 N 行，超出时 truncated=true。固定 range(11) 探针验证 max_rows=10。

本地回归已验证；真实生效情况 **NOT VERIFIED**。row_count 仍是返回行数，不是 total count。行数限制不是扫描量/并发配额保证。

## 20. EnvironmentValidationReport

实际本地报告见 [environment_validation_report.json](../../examples/environment_validation_report.json)。当前：

```json
{
  "environment": "spark_test",
  "overall": "not_verified",
  "connectivity": "not_verified",
  "engine_version": null,
  "session_timezone": null,
  "source_schema": null,
  "requires_human_review": true
}
```

完整报告包含每项 capability/evidence 和观察时间；没有缺失观测时自动判绿。新迁移 `0003_environment_validation` 创建 environment_validation_runs，保存 ID、版本、状态、版本/时区、时间和 report_json，不存连接信息。0001/0002 未修改。

## 21. MySQL Integration Result

**SKIPPED / NOT VERIFIED**。提供显式 mysql_integration 测试，验证 0001→0002→0003 upgrade、downgrade、JSON、索引和外键。

仅接受最初为空、名称为 airi_integration_* 的专用库，并要求进程环境变量 AIRI_MYSQL_INTEGRATION_ALLOW_RESET=true；URL 由 AIRI_MYSQL_INTEGRATION_URL 提供。测试会回滚新增表，保留空 alembic_version 表；复跑需运维准备空库。不会对已有业务库执行降级。

## 22. Unit Test Result

2026-09-11 实际验收：

| 命令 | 实际结果 |
|---|---|
| `uv run --frozen pytest` | **342 passed, 5 skipped**，2 warnings，16.04s |
| `uv run --frozen pytest --cov=airi --cov-report=term-missing` | **342 passed, 5 skipped**，23.57s；覆盖率 **96%**，2151 statements / 90 missed |
| `uv run --frozen ruff check src tests` | All checks passed! |
| `uv run --frozen ruff format --check src tests` | 91 files already formatted |
| `uv run --frozen alembic upgrade head` | exit 0 |
| `uv run --frozen alembic current` | 0003_environment_validation (head) |
| `uv run --frozen alembic check` | No new upgrade operations detected. |
| `uv run --frozen pytest -m spark_integration` | **4 skipped**, 343 deselected；未配置隔离 Spark、fixture 映射与运维声明 |
| `uv run --frozen pytest -m mysql_integration` | **1 skipped**, 346 deselected；未配置专用 MySQL 集成库 |

原有 **305 项**回归全部保留，新增 **37 项**本地测试。两个 warnings 是已有 Starlette/httpx 与 anyio BlockingPortal 弃用提示。迁移检查使用独立 `.demo/phase25_migration.db` SQLite 数据库，不能证明 MySQL 方言。覆盖率包含模拟 adapter 测试，不能证明真实连接、时区或取消。

## 23. Spark Integration Test Result

**SKIPPED / NOT VERIFIED**。四项显式 spark_integration 测试：三个指标完整 generate→approve→real execute→test，另加环境验收。使用 Mock LLM，但不会用 Mock executor 冒充 Spark。

```powershell
uv run --frozen pytest
uv run --frozen pytest --cov=airi --cov-report=term-missing
uv run --frozen pytest -m spark_integration
uv run --frozen pytest -m mysql_integration
```

普通 pytest 不连接真实环境。Spark 测试需要 mode=spark_test、environment=test、host、fixture mapping 和运维只读声明；否则 skip。项目没有新建 CI/CD 平台。

## 24. Known Limitations

- 没有真实 Spark/Hive/MySQL 连接证据、实际业务类型、时区或 Decimal 认证。
- 仅 NOSASL 内部隔离测试；未实现 LDAP/Kerberos/TLS/RBAC。密码配置只为安全类型和扩展边界，不表示密码认证可用。
- 探针依赖目标 Spark 支持 current_timezone()/typeof()/VALUES 等语法，不支持则失败，不自动兼容改写。
- 源视图 dt 与业务时间关系未知，业务 dt 样本格式未验证；fixture 数据不能代表真实口径。
- 没有自动修改 SQL、去重、过滤负数/NULL、改时区或自动修口径。
- 环境探针串行，多个连接各自受限，没有总请求预算、并发配额或调度平台。实际指标预检与执行共享一个连接和父进程截止时间。
- 真实远端取消、Spark Job 终止以及长查询 timeout 未验收；报告保留未知。
- 未实现新业务指标、Experiment、Reflection、LangGraph/Multi-Agent、RAG/Memory、前端、生产执行或自动上线。

## 25. Recommended Next Step

下一阶段仅推荐 **AIRI Phase 3 — Experiment & Metric Evaluation**，本次不开始开发。进入前须由隔离环境补齐上述 NOT VERIFIED 验收证据；本地工程完成不能替代这一前置条件。
