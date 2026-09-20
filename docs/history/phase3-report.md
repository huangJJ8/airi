# AIRI Phase 3 Implementation Report

2026-09-13。已完成单指标实验的本地闭环。以下数值来自固定合成样本，执行模式为 `mock`，不是实际金融风险验证。真实 Spark/Hive/MySQL：**NOT VERIFIED**。

## 1. Repository Assessment

基于实际模块检查，而非旧 README：

1. `DevelopmentArtifactRow` 保存产物；`ApprovalService.require_approved` 校验审批状态和内容哈希。
2. `MetricTestRunRow.status` 与 `MetricTestReport` 必须一致，允许 passed / passed_with_warnings，failed 拒绝。
3. 测试报告存入 `metric_test_runs.report_json`，关联 execution_run_id。
4. `ExecutionResult` 可用于有上限的实验；包含 rows、truncated 和执行元数据。实验拒绝截断结果。
5. 复用 `QueryExecutor`，为现有 Mock/Spark 适配器增加受控 `load_dataset`，没有第二套执行器。
6. artifact snapshot 已含 Metric IR、ExecutionContext、Skill/Tool Plan 和模板信息。
7. 明确 anchor 的 ExecutionContext 可回溯；实验 anchor 必须与审批产物一致。
8. 环境报告存入 `EnvironmentValidationRow.report_json`；实验按 validation_run_id 查询。现有环境 API 提供 status/validate，没有新增历史环境报告 GET。
9. 服务接收 SQLAlchemy Session，按操作 commit；实验先保存 running，再提交最终报告。数据库异常仍由基础设施处理。
10. 开始 Phase 3 时 experiments/evaluation 为预留目录；本次增加实际实现。

当前工作目录没有 Git 仓库，因此文件清单按本次实际编辑记录整理，没有声称执行 Git diff 或提交。

## 2. Architecture Changes

增加 experiments（业务门禁、模型、持久化）、evaluation（纯统计）、workflows/experiments（普通 Python 编排）。统计实现集中在 calculator.py，规模尚不需要拆成通用机器学习框架。原指标生成、审批和测试职责保持独立。

## 3. Added Files

- `src/airi/experiments/{models,validation,fixtures,persistence,service}.py`
- `src/airi/evaluation/{models,calculator}.py`
- `src/airi/workflows/experiments/{__init__,workflow}.py`
- `src/airi/api/experiments.py`
- `src/airi/tools/templates/spark/evaluation_dataset.sql.j2`
- `migrations/versions/0004_experiment_evaluation_experiment_evaluation.py`
- `tests/test_evaluation.py`、`tests/test_experiments.py`
- `examples/demo_phase3.py`
- `examples/phase3/` 中金额、次数、增长率各自的 `_spec.json` 和 `_report.json`，共六份实际输出。
- `phase3-report.md`

## 4. Modified Files

- `src/airi/infrastructure/query_executor.py`：复用执行器读取受控样本。
- `src/airi/main.py`：路由、显式 mock 模式的 200 主体 fixture。
- `src/airi/observability/logging.py`：实验关联 ID 白名单。
- `src/airi/experiments/__init__.py`、`src/airi/evaluation/__init__.py`：模块说明。
- `migrations/env.py`：导入新表元数据。
- `README.md`：Phase 3 使用说明。

迁移 0001、0002、0003 未修改。

## 5. Experiment Architecture

```text
HTTP POST /experiments/{id}/run
→ ExperimentService.run
→ run_experiment
→ ExperimentService.gates
  → require_approved / MetricTestReport / environment / LeakageValidator
→ ExecutionService.run → 已审批 SQL → QueryExecutor.execute
→ ExperimentExecutionPlan → QueryExecutor.load_dataset
→ evaluation_dataset → 样本校验、唯一性、标签映射、按主体左连接
→ calculate → Distribution / Coverage / Bad Rate / Bins / KS / IV / Lift / Candidates
→ ExperimentReport → SQLAlchemy commit → HTTP Response
→ 系统外 Human Review
```

两次受控 SELECT 的结果在 Python 内连接。对于当前至多 1000 主体的完整样本，这比拼接已审批 SQL 为新的 CTE 更直接，也不改变已审批 SQL。没有 SQL 写入、缓存语句或任意 SQL API。

## 6. ExperimentSpec

严格 Schema 包含实验名、产物 ID/版本、审批 ID、测试 ID、快照 ID、标签 ID、环境验收 ID、anchor、observation、label_window、EvaluationPolicy。完整可用实例见 [金额实验规格](../../examples/phase3/invoice_amount_30d_spec.json)。

固定 anchor/observation 为 `2026-09-09T00:00:00+08:00`，标签窗口为 `[2026-09-10, 2026-10-09)`。默认 policy 1.0.0，10 箱、epsilon=1e-6、最少标签样本 100、候选 p50/p75/p90/p95/p99。低样本仅警告。

## 7. DatasetSnapshot

一个明确源 `tmp_db.invoice_risk_sample`，主体 seller_tax_no，分区 dt=20260909，明确 snapshot_time、row_count、SHA-256 checksum。注册仅存元数据，不创建样本表。

v1 为受控完整小样本，row_count 上限 1000；checksum 必填，比任务示例的可空约束更严格。数据按主体排序后 canonical hash；读取后验证实际数量及哈希，不做隐式抽样。快照时间必须等于 observation_time。注册前可使用 `airi.experiments.validation.dataset_checksum(rows)` 计算校验和。

## 8. LabelDefinition

实体 seller_tax_no、字段 bad_flag；good_value/bad_value 必须为不同的整数 0/1，可交换定义。NULL 为 unknown 并排除；其他非二元值、布尔值拒绝。统计模块接收映射后的 is_bad，不写死 bad=1。

## 9. Leakage Guard

`LeakageValidator.validate` 要求 observation_time=anchor_time 且 anchor_time<label_window.start；LabelWindow 要求 end>start。产物 anchor 也必须一致。失败返回 experiment_invalid / leakage_detected。

报告保留 leakage_check 时间证据。这只证明声明的时间顺序，不证明上游标签生成和历史数据版本不存在泄露。合成未来标签用于演示，不能理解为已经观察到真实未来违约。

## 10. Evaluation Dataset

先检查样本主体唯一性、字段、分区、数量、校验和，再检查指标结果唯一性；重复直接失败，不静默去重。以有效标签主体为母体左连接指标，保留没有指标的标签主体为 NULL。

记录 label_entity_count、matched_count、label_only、metric_only、unknown_label_excluded、metric_non_null_count。metric_only 相对于有效标签集合，因此也可能包含标签未知的主体。连接内部保留 entity_id，统计输入只传 Decimal metric_value 和 is_bad。报告和 MySQL 不保存实体/标签明细。

## 11. Distribution

基于有效标签主体中的非 NULL 指标，输出 count、null_count、min/max/mean、p25/p50/p75/p90/p95/p99。分位数使用 `(n-1)*p` 线性插值；Decimal 精度 50，金额及增长率不先转 float。空数据返回 NULL。

## 12. Coverage

coverage=non_null_metric/labeled_sample；同时记录 total_sample、labeled_sample、non_null_metric、null_metric、good_count/bad_count。样本没有指标也在分母中；不使用 INNER JOIN 缩小分母。零标签样本返回 NULL。

## 13. Binning

默认 10 个 quantile bins，重复边界去重，相同值不拆分；落在切点上的值进入较高箱，空箱移除。记录 requested_bins 和 actual_bins（仅数值箱）。箱 lower/upper 表示实际观测最小/最大值，不是完整区间切点。

NULL 独立箱，记录 count/bad_rate；纳入 IV 和 Lift，排除 KS，避免将缺失默认为低值。

## 14. KS

基于非 NULL 两类样本，计算累计 good/good_total 与 bad/bad_total 的最大绝对差。记录 value、ascending、descending、bucket、direction、split。

同一组箱反向排序的绝对 KS 在数学上相同，不能靠二者大小判定方向。因此保留两者相同的绝对值，并用最大偏离处的有符号 CDF 差判定 higher/lower；并列取升序首个最大位置。差为零则 undetermined，不制造阈值方向。KS split 对齐 >=/<= 的实际边界。只有一类或非 NULL 部分仅一类时 not_available。

这是分箱 KS，不声称等于未经分箱的全分辨率 KS。

## 15. IV

每箱 `good_pct=(good+epsilon)/(total_good+epsilon*K)`，bad_pct 同理；WOE=ln(good_pct/bad_pct)，IV=sum((good_pct-bad_pct)*WOE)。K 包括存在的 NULL 箱。epsilon 记录在 policy 和结果中。

核心计算用 Decimal ln，展示转 float。单类返回 iv=null、iv_status=not_available 和 warning；不返回伪造 0 或 Infinity。稀疏纯箱和很小 epsilon 会放大 IV，报告不将大 IV 自动解释为可上线。

## 16. Lift

每箱 Lift=箱 bad_rate/总体 bad_rate，总体母体包括有效标签但指标 NULL 的主体。总体 bad_rate=0 时 Lift=NULL 并警告。计数比率先经 Decimal，Lift 最终在 float 展示精度计算；手算测试用 pytest.approx 默认相对容差 1e-6、绝对容差 1e-12。

## 17. Threshold Candidate Search

仅使用配置中的 p50/p75/p90/p95/p99 和 KS best split，重复阈值合并 sources。方向高风险时 >=，低风险时 <=。NULL 不命中。

每个候选记录 threshold/operator/sources、hit_count/hit_rate、bad_count/good_count、bad_rate/precision、recall、lift。hit_rate 分母为全部有效标签主体，recall 分母为全部坏标签主体。不选择最终阈值、不修改 SQL、不硬编码 KS/IV 准入标准。

## 18. invoice_amount_30d Evaluation

固定 seed=20260911，200 主体，188 有效标签；coverage=0.9202127659574468，KS=0.576750700280112，IV=6.031774836511457，higher_is_riskier。

完整分布、箱、命中计数和阈值见 [金额实验报告](../../examples/phase3/invoice_amount_30d_report.json)。数据仅为合成演示。

## 19. invoice_count_30d Evaluation

同一 fixture，coverage=0.9202127659574468，KS=0.07563025210084033，IV=0.04532277654214983，higher_is_riskier。次数与合成标签关联弱于金额，未刻意把所有指标设计为高 KS。

详细证据见 [次数实验报告](../../examples/phase3/invoice_count_30d_report.json)。不自动给出业务优劣结论。

## 20. invoice_amount_growth_30d Evaluation

coverage=0.8138297872340425，KS=0.44606038291605304，IV=5.0975912642223005，higher_is_riskier。复用既有增长率 SQL；前期零分母导致 NULL，从而影响覆盖率。

详细证据见 [增长率实验报告](../../examples/phase3/invoice_amount_growth_30d_report.json)。current_amount/previous_amount 不参与最终效果统计。

## 21. Experiment Report Example

三份 `_report.json` 为实际 API 返回，包含 run、execution_mode、evaluation、join_summary、reproducibility、leakage_check、warnings、requires_human_review=true。分位数和金额阈值以 Decimal 字符串序列化，KS/IV/Lift 为 JSON 数值。

复现：`uv run --frozen python examples/demo_phase3.py`。脚本显式使用 Mock LLM、MockQueryExecutor、SQLite，并通过真实 FastAPI 路由完成生成、演示审批、测试、注册、实验和报告保存。无需真实 API Key。重跑生成新 UUID 和时间，统计结果稳定。演示审批不是生产授权。

## 22. Reproducibility Metadata

记录 artifact ID/hash、IR hash、Skill/Tool Plan 及版本、SQL 模板和样本模板版本、完整快照/标签定义、anchor、policy/version、环境 report ID/hash、测试 report ID/hash、spec hash、execution metadata 和指标结果 checksum。

同一 spec 再运行时必须满足原快照数量/checksum；指标结果 checksum 必须与首次成功实验一致，否则失败。首次实验尚无指标历史校验和，不能证明源表当时就是某个不可变历史版本；这是明确限制。

## 23. Database Schema

新增五张表：dataset_snapshots、label_definitions、experiment_specs、experiment_runs、experiment_evaluations。规格引用产物、快照和标签，运行引用规格，评估引用运行。摘要列可查询，完整严格模型及候选保存在 JSON，无明细表。

运行保存 running/completed/failed 及创建/开始/结束时间和安全失败类别；模型保留 pending，但同步运行无需另设队列。运行失败的报告同样可查询；门禁失败在运行创建前返回应用错误。

迁移 revision=`0004_experiment_evaluation`，parent=`0003_environment_validation`。SQLite 新库和已有 0003 库升级均已实际执行；MySQL 尚未实库验收。

## 24. API

| 方法 | 路径 | 返回 |
|---|---|---|
| POST | /api/v1/datasets | 201，登记已有快照 |
| POST | /api/v1/labels | 201，登记标签定义 |
| POST | /api/v1/experiments | 201，校验门禁并保存规格 |
| POST | /api/v1/experiments/{id}/run | 200，完成或失败的报告 |
| GET | /api/v1/experiments/runs/{run_id} | 200，历史报告 |

未知字段/非法模型 422，不存在资源 404，实验门禁 409，响应关联 request_id。run 请求体只接受空对象或省略，SQL 字段拒绝。真实 spark_test 必须有已通过环境报告且连接通过、时区正确；not_verified 拒绝。mock 可用于离线开发。

## 25. Test Results

2026-09-13 最终执行结果：

| 命令 | 真实结果 |
|---|---|
| `uv run --frozen pytest` | 365 passed、5 skipped、2 warnings，14.70 秒 |
| `uv run --frozen pytest --cov=airi --cov-report=term-missing` | 365 passed、5 skipped，96%，2717 statements / 118 missed，26.39 秒 |
| `uv run --frozen ruff check src tests` | All checks passed |
| `uv run --frozen ruff format --check src tests` | 103 files already formatted |
| `uv run --frozen alembic upgrade head` | 成功，专用 SQLite `.demo/phase3_migration.db` |
| `uv run --frozen alembic current` | 0004_experiment_evaluation (head) |
| `uv run --frozen alembic check` | No new upgrade operations detected |

5 项跳过为 4 项 Spark 和 1 项 MySQL 集成测试；普通测试未显式选中集成标记，也没有真实环境配置。两个提示为 Starlette/httpx 和 AnyIO 的第三方弃用提示。

测试全部使用 Mock LLM；新增 23 项手算统计与完整三个指标 E2E，并验证重复实体、标签交换、NULL、单类、空样本、门禁（包括已存储 not_verified 环境报告）、校验和变化、执行失败/截断、持久化及 API 错误。

独立手算基准：值 [1,2,3,4]，标签 [0,0,1,1]，两箱，KS=1、mean=2.5、p25=1.75、Lift=[0,2]；IV 由测试中的独立 math.log 公式计算。反向标签验证 lower_is_riskier；常量、相同分布验证 KS/IV=0。

## 26. Known Limitations

- 真实 Spark/Hive/MySQL 未配置，不能将 Mock 或 SQLite 结果当作集群验收。
- 当前最多完整读取 1000 主体，一个受控样本表/分区；不支持大样本分布式统计、多源 Dataset DAG。
- 两次 SELECT 没有跨源原子快照；checksum 可检测内容变化，但不替代上游数据版本管理。并发首次运行的历史基准绑定未做锁定。
- 环境报告尚无集群端点指纹或有效期绑定；生产身份认证和不可冒名审批仍未实现。
- 单次样本内计算，没有独立留出集；候选存在样本内选择偏差。小样本和纯箱的 IV 对 epsilon 敏感。
- 时间检查验证声明顺序，不能自动审核标签来源及业务泄露。
- 进程异常退出可能留下 running，需要运维识别；未添加队列、取消和自动重试。
- 人工审阅实验报告在系统外进行；未实现第二套报告审批或自动阈值应用。
- 没有 Reflection、LangGraph、多 Agent、RAG、Memory、模型训练、新指标发现、生产策略发布。

## 27. Recommended Next Step

仅推荐 **AIRI Phase 3.5 / Phase 4 — Experiment Reflection & Metric Refinement**：以本阶段可审计证据为输入，形成待人工审阅的改进建议。本次未开始实现。使用真实数据前仍需完成现有 Spark/MySQL 环境验收与样本快照核验。
