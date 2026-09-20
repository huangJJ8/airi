# AIRI Phase 9 Implementation Report

Phase 9 的主题是 **Operational Reliability + Governed Convergence**，不是自治。它在 Phase 8
已经建立的证据层之上回答三个新问题：

```text
注册表指针、运行时、以及"应该是什么"三者不一致时，谁错了？
一个被批准的动作发现"目标其实已经满足"时，算冲突还是算完成？
告警产生之后，谁负责把它送出去，送不出去又会怎样？
```

Phase 9 的答案是：把 desired / registry / runtime 拆成三个独立事实；把 `no_op` 提升为一等的
收敛动作；把"检查过了"和"没人检查"记成两种不同的证据；把告警和通知彻底分开。全程没有新增
调度器、队列、后台重试或自动修复。

---

## 1. Repository Assessment

进入本阶段时仓库的状态（Phase 8 结束时）：

| 项 | 状态 |
| --- | --- |
| 测试 | 608 passed / 14 skipped |
| 覆盖率 | `TOTAL 87%` |
| head 迁移 | `0010_real_production_verification` |
| 生产路由 | 51 |
| 已知缺陷 | 注册表指针被人工对齐后，回滚仍报 `rollback_reconciliation_required` |

Phase 8 遗留的缺口是本次工作的全部输入：

- `reconcile` 只回答"注册表和运行时是否一致"，不回答"它们是否一致地**错了**"；
- 对账执行完的判定只看 provider 返回值与一次复观测，缺少"目标已满足"这一合法结局；
- `MonitoringExpectation` 可以声明"多久没报算 overdue"，但**没有任何代码去检查**；
- 告警产生后没有投递概念，也就不存在"投递失败"这一事实；
- 验证报告是人读的，不是机器执行前的门禁；
- 没有任何一个对象能把"距离这个环境真正闭环还差什么"一次说清。

## 2. 交接与续作状态

Phase 8 报告的 §37 写明"下一步仅为 Phase 9"，并列出四件未实现的事：真实环境验收、注册表指针
与部署记录脱钩时的语义、监控采集器与调度、以及 convergence 的完备性。本阶段只做最后一件，
并把前两件中**不依赖真实环境**的部分做掉；真实环境验收仍然刻意未完成。

未改变：`production_deployed` 在本仓库的任何代码路径下都不可能为 `true`；`synthetic_profile`
环境永远无法靠"多通过几次"变成真实环境。

## 3. 从 Phase 8 的缺口到 Phase 9 的五个问题

```text
Desired state        ≠  Registry pointer      ≠  Runtime state
Target already met   ≠  Conflict
Alert recorded       ≠  Alert delivered
Nobody checked       ≠  Checked and fine
Row present          ≠  Row verified
```

这五行同时也是第 6、11、21、18、31 节的主线。

## 4. Architecture Changes

新增一个纯函数模块与两个执行接缝，其余都在既有模块内扩展：

```text
models.py          三态词表 + Phase 9 模型 + PROTECTED_REQUIREMENTS（唯一定义）
convergence.py     纯函数：observed_runtime_state / availability / diagnose_conflict
                   / convergence_verdict / health_of / build_convergence
expectations.py    纯函数：expectation_window / window_key / dedup_key / evaluate_expectation
gate.py            纯函数：resolve_enforcement_mode / evaluate_verification_gate
notifications.py   协议 + Disabled / Mock / DingTalk 三个 sink，全部返回结果而不抛出
service.py         编排：_converge / execute_reconciliation_plan / evaluate_expectations
                   / telemetry_source_health / closure_matrix / _deliver
api/production.py  10 条新路由
config.py          3 个新设置项
```

分层原则与 Phase 8 一致：**判定在纯函数里，编排在 service 里，证据在表里**。`convergence.py`、
`expectations.py`、`gate.py` 全部可以脱离数据库单测。

## 5. Added / Modified Files

新增：

```text
src/airi/production/convergence.py
src/airi/production/expectations.py
src/airi/production/gate.py
src/airi/production/notifications.py
migrations/versions/0011_operational_convergence_operational_convergence.py
examples/demo_phase9.py
phase9-report.md
```

修改：

```text
src/airi/production/models.py        三态词表、Phase 9 模型、PROTECTED_REQUIREMENTS、事件词表
src/airi/production/service.py       _observe / convergence / health / gate / closure_matrix
                                     / _converge / execute_reconciliation_plan
                                     / evaluate_expectations / _deliver / notify
src/airi/production/identity.py      ACTION_ROLES += execute_production_reconciliation
src/airi/production/persistence.py   5 张新表
src/airi/registry/models.py          ReleaseEventType += Phase 9 的 8 类事件
src/airi/api/production.py           10 条新路由
src/airi/core/config.py              3 个新设置项
tests/test_production.py             Phase 9 用例
README.md                            Phase 9 章节
```

## 6. 三态分离：Desired / Registry / Runtime

Phase 9 的核心决定是把"状态"拆成三个**互不推导**的事实：

| 事实 | 来源 | 回答的问题 |
| --- | --- | --- |
| desired | `DesiredRuntimeState` | 治理上应该跑哪个版本 |
| registry | 注册表活跃指针 | 现在被授权暴露的是哪个版本 |
| runtime | provider 复观测 | 实际在跑的是哪个版本 |

`ConvergenceState` 只有 `converged` / `mismatch` / `unknown` 三个值，它是**比较的产物**，不是
任何一个来源的副本。`DeploymentStatus`（工作流）、`RuntimeState`（运行时）和 `ConvergenceState`
是三个独立的 `Literal`，可以共享同一个**词**（`failed` 在两者中都存在）却表达完全不同的事：

- 工作流的 `failed` = 这次部署尝试失败了；
- 运行时的 `failed` = 运行时自报失败状态。

共享一个词正是它们**不能**合并成一个枚举的原因。`test_the_three_state_enums_are_distinct_types`
把这个规则钉住。

## 7. 9A `DesiredRuntimeState`：目标不是指针

Phase 8 只有"注册表指针"和"运行时"两个事实，于是"注册表和运行时一致"被当成了收敛。可是两者
可以一致地指向一个**不该跑**的版本。Phase 9 引入第一个事实：

```text
DesiredRuntimeState(metric_definition_id, environment_id, desired_metric_version_id,
                    source, source_action_id, established_by, state_hash, established_at)
```

`source` 是 `deploy` / `rollback` / `reconciliation`——目标状态永远由一次**已被人批准的动作**
确立，没有任何 API 允许直接写入。历史按 `(metric_definition_id, environment_id, established_at)`
排序，最新一条生效，旧的不删除。

## 8. 9A `observed_runtime_state()`：unknown 必须传染

`DeploymentReconciliation.status == "unknown"` 意味着**运行时答不出话**。此时
`production_runtime_state` 字段里可能仍留着上一次的 `"active"`。Phase 9 的规则是：读不到就当
读不到。

```python
def observed_runtime_state(reconciliation) -> str:
    if reconciliation.status == "unknown":
        return "unknown"
    return reconciliation.production_runtime_state
```

所有判定路径（`plan_triple` / `build_convergence` / `health_of`）统一走这个函数。这条规则修掉了
两个真实的相位错误：`unknown_state.recommended_action` 曾经给出 `registry_to_runtime`——即
"让注册表跟着一个**答不出话**的运行时走"。

## 9. 9A `available_actions()` 与 `no_op` 的一等地位

`available_actions` 从"描述该做什么"变成"枚举**确实可以做**的事"：

```text
registry_to_runtime   注册表目标已知，运行时不是它  → 可用
runtime_to_registry   运行时版本已知且注册表认识它  → 可用（"运行时就是事实"）
manual_investigation  永远可用
no_op                 目标已满足 → 唯一可用动作
```

`no_op` 不是 `None`、不是空列表、不是"没有推荐"。它是一个**动作**：它让"什么都不该做"成为
可以被记录、被批准、被审计的结论，而不是一个缺失值。当目标已满足时，
`available_actions == ["no_op"]` 且 `recommended_action == "no_op"`。

## 10. 9A `diagnose_conflict()`：冲突分四类

一次 compare-and-set 失败不再是一个布尔量，而是一个 `ConflictDiagnosis`：

| category | 含义 | 需要人工 |
| --- | --- | --- |
| `target_already_satisfied` | 实际值已经等于目标 | 否 |
| `stale_expected_state` | 实际值等于期望值，但目标不同 | 否（可重试） |
| `unexpected_external_change` | 实际值是第三方，既非期望也非目标 | 是 |
| `runtime_unknown` | 运行时读不到 | 是 |

`requires_manual_review` 与 `safe_to_retry` 由 category 派生，不由调用方传入。

## 11. 9A Phase 8 回滚回归的修复（§8）

这是本阶段存在的直接原因。场景：

```text
1. 部署在 v2，回滚被请求 → 审查记录 from=v2 to=v1
2. 运行时被观测到在 v1，人工对账采纳之 → 注册表指针移到 v1
3. 回滚被批准执行 → 指针"移动"其实无事可做
```

Phase 8 在这里报 `rollback_reconciliation_required`，把一个**已经达成**的目标说成了冲突。
Phase 9 在动手之前先观测：

```python
actual = self._registry_active_version_id(review.metric_definition_id)
if actual == review.to_version_id:
    registry_status = "already_satisfied"
    diagnosis = diagnose_conflict(expected=from, actual=actual, desired=to)
```

结果：`registry_reconciliation_status="already_satisfied"`、`no_action_required=True`、
`conflict_diagnosis.category="target_already_satisfied"`、
`requires_manual_review=False`，并追加事件 `convergence_already_satisfied`。

**关键点：结果仍然是"已证明的 no-op"，而不是从"成功了"推断出来的成功。**

## 12. 9A `convergence_verdict()`：converged vs already_converged

```text
converged           真的执行了动作，并且复观测确认收敛
already_converged   什么都没执行，因为不需要执行
verification_required  执行了，但复观测仍不一致
not_executed        人工选择不执行（保留 mismatch）
```

`already_converged` 与 `converged` 分开，是因为"修好了"和"本来就没事"在运维上是两件事：
前者需要验证一次修复，后者需要确认一次判断。

## 13. 9A `_converge()`：观测先于行动

`decide_reconciliation_review` 与 `execute_reconciliation_plan` 共用同一个执行体：

```text
1. 观测（reconcile，只读）
2. 若目标已满足 → execution_status="skipped"
                   convergence_status="verified"
                   final_status="already_converged"
                   skipped_reason="target_state_already_satisfied"
3. 否则执行唯一一个被批准的动作
4. 再次观测，只有复观测才产生 convergence_status
```

第 2 步让**重复执行一个已批准的收敛计划在构造上就是安全的**：第二次运行时观测到目标已满足，
直接返回 no-op。这是幂等，不是"重试保护"。

## 14. 9A `runtime_to_registry` 重建 desired state

当人工判断"运行时就是事实"时，注册表跟着运行时走。但如果 desired 仍是旧版本，系统会在下一次
检查里立刻又报 mismatch——把人的决定撤销掉。

所以该动作在收敛确认后**重新确立** desired state，来源记为 `reconciliation`：人的决定成为新的
目标，而不是被系统悄悄推翻。事件 `desired_runtime_state_established`。

## 15. 9A 新增 API：convergence / health / execute

```text
GET  /api/v1/production-deployments/{id}/convergence
GET  /api/v1/production-deployments/{id}/health
POST /api/v1/reconciliation-plans/{id}/execute
```

`/convergence` 是**只读**视图，且需要认证：它会重新读一次运行时。测试
`test_the_convergence_view_never_repairs_anything` 断言读完之后注册表指针纹丝不动。

`/execute` 只接受**已经被人工复核过**的计划：未经 review 的计划返回 409
`reconciliation_review_required`；已经 converged 时，连新计划都不会生成，返回 409
`production_reconciliation_not_required`。

## 16. 9B `expectation_status` vs `evaluate_expectations`

Phase 8 留下 `MonitoringExpectation` 却没人检查它。Phase 9 把它拆成两个语义不同的入口：

| 入口 | 语义 | 副作用 |
| --- | --- | --- |
| `GET .../monitoring-expectation/status` | 顾问式读取 | **无** |
| `POST /monitoring-expectations/evaluate` | 一次操作 | 落库 run + 可能开告警 + 投递通知 |

顾问式读取永远不开告警。`evaluate_expectation()` 是纯函数，只返回判定；`evaluate_expectations()`
才是那个"有人真的去检查了"的动作。**注意：AIRI 拥有问题，不拥有时钟。** 没有任何调度器被创建，
外部 cron / Argo / Databricks 调用 `POST .../evaluate` 即可。

## 17. 9B `no_snapshot` 与 `due_soon`

判定从三值扩到四值：

```text
no_snapshot   这个部署从来没有任何读数     （声明从未被满足）
satisfied     在声明的窗口内
due_soon      下一次读数已到期，仍在宽限期内
overdue       超过宽限期
```

`due_soon` 存在的理由是 §22：跨过 deadline 不允许从"fine"一步跳到"critical"。`no_snapshot`
存在的理由更强：对一个从未到来的窗口报 `satisfied`，等于**为一次没有发生的投递背书**。

`telemetry_missing`（窗口关了且什么都没来）与 `telemetry_late`（来了但观测到接收的时延超限）
是两个独立 finding，可以同时出现。`test_missing_and_late_are_separate_findings` 逐个钉住。

## 18. 9B `ExpectationEvaluationRun`：检查本身是证据

每次 `evaluate` 落一条 `ExpectationEvaluationRun`：`evaluated_by`、`expectation_count`、
`satisfied/due_soon/overdue/unknown_count`、`telemetry_missing_count`、`telemetry_late_count`、
`statuses[]`、`created_alert_ids[]`，附 `run_hash`。

这直接来自 §23：**"没人检查"和"检查了并且没问题"不能在审计里长得一样。** 没有 run 行 = 没人
检查；有 run 行且计数为零 = 检查过且干净。

## 19. 9B 告警去重：一个窗口一个告警

去重键是 `(deployment_id, finding, window_key)`，其中 `window_key` 由"锚点（上次观测时间或
声明时间）+ 本次 arrival_due"派生。于是：

- 同一个窗口重复评估 → 同一个告警，`occurrences` 递增，不产生第二条；
- 新的读数到达后锚点移动 → 新的窗口 → 开它自己的告警。

这让 `evaluate` 可以任意频繁地被调用而不放大告警量。

## 20. 9B `TelemetrySourceHealth`：silent ≠ degraded

生产者健康度是**关于管道**的，不是关于指标的：

```text
unknown    生产者未被 attest，任何健康结论都没有依据
silent     已 attest 但从未投递过任何读数
degraded   在投递，但迟到或漏过已声明的窗口（指标可能仍然正确）
healthy    已 attest 且在声明的窗口内投递
```

对象里带一个恒为 `False` 的 `metric_health_implied`：§29 要求"坏掉的传输永远不解释一个指标"
这件事被写下来，而不是留给读者推断。silent 与 degraded 的区别是运维的第一分诊：前者要查
生产者，后者要查链路。

## 21. 9B 告警 ≠ 通知：`NotificationDelivery`

```text
MetricAlert          一条关于指标的事实，永远存在
NotificationDelivery 某个 sink 在某次尝试上的结果，可能失败
```

告警创建与投递是两件事。`_deliver()` 只记录结果，绝不抛出：

- `sink.notify()` 返回 `NotificationResult(status=delivered|failed|disabled)`；
- 失败被写成一条 `NotificationDelivery(status="failed", failure_category=...)`；
- 告警本身不受影响，`status` 保持 `open`。

所以**聊天平台挂掉永远不能撤销它正在承载的告警**（§32）。

## 22. 9B 重放与显式重发

```text
重复调用（未指定 resend）  已成功投递过 → 返回那条旧记录，replayed=True；不写第二行、不重发
resend=true              故意重发 → 写新的一行，replayed=False
```

区分"重放"与"重发"，是因为两者在事故复盘里意义相反：前者是调用方重复了请求，后者是有人
刻意又发了一次。`notify()` 自己在路由层 `commit()`——`_deliver` 故意不提交，因为它平时是被
`record()` 的调用方包在更大的事务里的。

## 23. 9B 失败的 sink 被记录而非抛出

三种 sink 的行为一致性由协议保证：

| sink | 说明 |
| --- | --- |
| `DisabledNotificationSink` | 默认。没人被通知，尝试被如实记为 `disabled` |
| `MockNotificationSink` | 仅用于测试/演示的显式注入，**不可通过配置选择** |
| `DingTalkNotificationSink` | 只有运维显式配置 webhook 才存在；失败返回分类结果 |

没有后台重试队列。重试就是第二次显式调用，由人或外部调度器发起（§34）。

## 24. 9B 新增 API：evaluate / evaluation-runs / health / notifications

```text
POST /api/v1/monitoring-expectations/evaluate?deployment_id=
GET  /api/v1/monitoring-expectations/evaluation-runs/{id}
GET  /api/v1/telemetry-sources/{id}/health
GET  /api/v1/metric-alerts/{id}/notifications
POST /api/v1/metric-alerts/{id}/notifications?resend=
```

`evaluate` 需要认证身份（它是一次动作）；`evaluation-runs` 与 `notifications` 是读取；
`POST notifications` 也就需要认证——匿名触发一次投递是 401。

## 25. 9C Gate 的位置：先门禁，后 provider

Phase 9 把验证从"事后报告"变成"事前门禁"。顺序被固定为：

```text
gate → deploy
gate → rollback
gate → reconciliation
```

`test_the_gate_is_reported_before_the_provider_is_called` 断言：真正发生的那次 deploy 自己
记录了门禁决策与 `verification_gate_passed` 事件。

门禁读取本身是顾问式的：`GET .../verification-gate?action=` 需要认证，因为它会重读证据。

## 26. 9C action-aware requirements

同一个部署，不同动作需要不同的证据：

| action | required_sections |
| --- | --- |
| `deploy` | environment, identity, adapter, runtime_identity, shadow |
| `rollback` | environment, identity, adapter（**不含** monitoring_source） |
| `reconciliation` | environment, identity, adapter, runtime_identity |

第一次部署还没有生产遥测，**一次紧急回滚不能被它正要救的那条管道挡住**（§42）。反之，
reconciliation 需要 runtime_identity：不能对着一个身份不明的运行时做收敛。

## 27. 9C `PROTECTED_REQUIREMENTS` 单一来源

```python
PROTECTED_REQUIREMENTS = (
    "package_integrity", "artifact_integrity",
    "metric_version_identity", "actor_authentication",
)
```

定义在 `models.py`（与词表同处），由 `convergence.py` 重新导出。此前模型、门禁、收敛助手各
自维护一份同样的列表——三份拷贝意味着总有一天会漂移。

## 28. 9C `EmergencyOverride` 构造期拒绝 + 时限

`EmergencyOverride` 的 docstring 一直承诺"不能豁免受保护项"，但没有任何代码实现它。Phase 9
补上 `field_validator("waives")`，在**构造期**就拒绝：

```python
EmergencyOverride(waives=["shadow", "actor_authentication"])  # ValidationError
```

同时门禁自己也不信任豁免：即使有人用别的方式构造出对象，`evaluate_verification_gate` 仍以
`integrity_requirements` 判 `blocked`，并给出"no emergency override may waive..."。豁免仍是
**限时的**：`active_at(now)` 过期即什么都不给。

## 29. 9C 执行模式按环境解析，不按策略

```text
auto        + synthetic_profile 环境 → report_only
auto        + 真实环境               → enforced
report_only / enforced               → 运维可以钉死任一侧
```

一个合成环境**不可能**产出真实证据，在那里强制门禁只会教人绕过它（§38）。所以合成环境上
门禁照常计算、照常记录缺口，结果是 `allowed` 而不是假装 `blocked`。

## 30. 9C 新增 API：verification-gate

```text
GET /api/v1/production-deployments/{id}/verification-gate?action=deploy|rollback|reconciliation
```

响应包含 `mode`、`result`（`allowed` / `blocked` / `allowed_with_override`）、
`required_sections`、`missing_requirements`、`integrity_requirements`、`evidence_refs`、
`override_id`。`test_integrity_and_authentication_outrank_every_override` 与
`test_an_unauthenticated_actor_can_never_be_waived` 分别钉住两条不可越过的线。

## 31. 9E `EnvironmentClosureMatrix`：9 行，不刷绿

九行是"距离这个环境真正闭环还差什么"的完整清单：

```text
environment_profile              real_production_deployment
environment_fingerprint          trusted_telemetry_source
identity_trust_boundary          monitoring_expectation
authoritative_adapter            notification_sink
rollback_capability
```

四条规则：

1. **发版不刷绿。** 一行只在**该环境真的产出了证据**时变绿。写 Phase 9 的代码改变不了任何一行。
2. **`not_applicable` 不是证据。** 合成环境产不出权威运行时，那一行是 `not_applicable`，
   不被算作 verified，也**不算 open**。
3. **`blocked` 专指"还无法尝试"。** 例如某个环境还没有任何部署，遥测信任就无从归属。
4. **`production_closed` 要求零 open 项且存在 profile。** 一个不存在的环境不会被"关闭"。

`open_items` 把未闭环项逐一点名，而不是给一个分数。

## 32. Audit Events：Phase 9 新增 8 类

```text
desired_runtime_state_established
convergence_already_satisfied
convergence_conflict_diagnosed
expectation_evaluated
telemetry_missing_detected
notification_attempted
verification_gate_blocked
verification_gate_passed
```

这 8 类同时加入 `DeploymentEventType` 与 `ReleaseEventType`。Phase 8 的教训是：部署事件表不是
唯一被索引的那张表，只加一边会让审计在另一条链路上断掉。
`test_every_phase9_audit_event_exists_in_both_event_tables` 断言两边都存在。

演示里同时出现的 `reconciliation_executed` / `reconciliation_verified` /
`production_rolled_back` 是 Phase 8 已有的事件，Phase 9 让它们在**新的顺序**里出现
（例如 `convergence_already_satisfied` 紧跟在 `production_rolled_back` 之后）。

## 33. Database Schema（`0011_operational_convergence`）

新增 5 张表，全部是**增量**，0001–0010 不被改写：

```text
desired_runtime_states          (desired_state_id PK, state_hash, established_at)
                                ix_desired_runtime_states_scope
expectation_evaluation_runs     (evaluation_run_id PK, run_hash, evaluated_at)
notification_deliveries         (notification_delivery_id PK, delivery_hash)
                                ix_notification_deliveries_alert (alert_id, sink)
verification_gate_decisions     (gate_decision_id PK, decision_hash, decided_at)
emergency_overrides             (emergency_override_id PK, override_hash, expires_at)
```

另有一处**可空性变更**：`reconciliation_results.reconciliation_review_id` 与 `action` 改为
nullable，因为一次 `already_converged` 的收敛**没有 review、也没有 action**，诚实的编码是
NULL 而不是编一个哨兵字符串。用 `batch_alter_table` 实现，SQLite 与 MySQL 同一条 revision。

`downgrade()` 完整反向：先恢复可空性，再按依赖顺序 drop 索引与表。

## 34. APIs（生产路由 61 条，新增 10 条）

```text
GET  /api/v1/production-environments/{id}/closure-matrix
POST /api/v1/monitoring-expectations/evaluate
GET  /api/v1/monitoring-expectations/evaluation-runs/{id}
GET  /api/v1/telemetry-sources/{id}/health
GET  /api/v1/production-deployments/{id}/convergence
GET  /api/v1/production-deployments/{id}/health
GET  /api/v1/production-deployments/{id}/verification-gate
POST /api/v1/reconciliation-plans/{id}/execute
GET  /api/v1/metric-alerts/{id}/notifications
POST /api/v1/metric-alerts/{id}/notifications
```

**刻意没有**的路由：

- 没有任何写入 desired state 的 API——它只由已批准的动作确立；
- 没有任何"自动修复"入口；
- 没有创建调度器或注册定时任务的入口；
- `emergency_override` 只有 service 层能力，没有公开路由：break-glass 是一次操作员动作。

## 35. Demo 与 Quality Gates

```powershell
uv run --frozen python examples/demo_phase9.py
```

**Synthetic Phase 9 Operational Convergence Demo**——名字本身就是一条声明：它跑在离线 fixture +
Mock provider 上，**不可能**验证真实运行时，并在每一步把这件事说出来。使用专用
`.demo/phase9_demo.db`，产物在 `examples/phase9/`。实际输出节选：

| 步骤 | 结果 |
| --- | --- |
| 生产者（无读数） | `status=silent`、`metric_health_implied=False` |
| 部署 | `status=deployed`、`production_deployed=False` |
| 期望值 | `1h / grace 0` → `overdue`、`findings=['telemetry_missing']`，**告警数 0**（读取是顾问式的） |
| 评估 | run 落库：`by=demo-admin`、`overdue=1`、`missing=1`；告警 `missing_monitoring_data` / `critical` |
| 幂等 | 同窗口再次评估 → 告警仍 1 条、`occurrences=2` |
| 生产者（漏窗后） | `silent → degraded` |
| 通知 | 默认 sink `disabled` → mock `delivered` → 重复调用 `replayed=True` → `resend` 再投一次 → 坏 sink `failed/provider_rejected`，告警仍 `open` |
| 三态分离 | `workflow=monitoring`、`runtime=active`、`convergence=mismatch` → `health=degraded` |
| 收敛视图 | `possible=['runtime_to_registry','manual_investigation']`、冲突 `unexpected_external_change`；**读完之后注册表指针未动** |
| 未复核计划 | 执行 → 409 `reconciliation_review_required` |
| 回滚回归 | 指针已被人工对齐 → `already_satisfied` / `no_action_required=True` / 冲突 `target_already_satisfied` / `requires_manual_review=False` |
| 收敛视图（回滚后） | `desired=1.0.0 source=rollback`、`state=converged`、`possible=['no_op']`、`auto=False` |
| 幂等执行 | 同一已批准计划再执行 → `skipped` / `already_converged` / `target_state_already_satisfied` |
| 无事可做 | 再要一个计划 → 409 `production_reconciliation_not_required` |
| 门禁 | `deploy/rollback/reconciliation` 均 `mode=report_only result=allowed`；rollback 的 missing 比 deploy 少一项 |
| 门禁鉴权 | 匿名读取 → 401 |
| 闭环矩阵 | `5 verified / 3 not_verified / 1 not_applicable / 0 blocked`，`production_closed=False` |
| 配置 sink | 配之前 `notification_sink=not_verified`，配之后 `verified` |
| 审计 | 19 条相关事件，顺序完整（含 Phase 8 的 `reconciliation_executed` / `reconciliation_verified` / `production_rolled_back`） |

质量门禁：

```powershell
uv run --frozen pytest
uv run --frozen pytest --cov=airi
uv run --frozen ruff check src tests
uv run --frozen ruff format --check src tests
uv run --frozen alembic upgrade head && uv run --frozen alembic current && uv run --frozen alembic check
```

结果：

| 门禁 | 结果 |
| --- | --- |
| pytest | **639 passed, 14 skipped**（Phase 8 基线 608 passed / 14 skipped） |
| coverage | `TOTAL 91.0%`（9243 stmts / 834 miss；138 文件中 87 个 100% 覆盖） |
| ruff check | All checks passed |
| ruff format --check | `src tests` 163 files already formatted（含 `examples` 共 175 files） |
| alembic | head = `0011_operational_convergence`，`check` 无漂移 |
| demo | exit 0 |

## 36. Known Limitations 与 Phase 10 建议（未实现）

本阶段**没有**做的事，按重要性排序：

1. **真实环境验收仍然未完成，且这是刻意的。** `production_integration` /
   `identity_integration` / `telemetry_integration` 三档集成测试在未配置时全部跳过并说明
   缺什么，绝不回落为 mock 成功。`REAL_ENVIRONMENT_CHECKLIST.md` 依然是需要人去执行的清单。
2. **监控仍然没有采集器，也没有调度。** `evaluate` 是一个等待被调用的端点；把它接到 cron /
   Argo / Databricks 是运维的事，AIRI 只拥有"问题"，不拥有"时钟"。
3. **没有自动修复，且不应有。** 每个收敛动作都需要一次人工复核；`no_op` 是一等动作而不是
   自动跳过。
4. **闭环矩阵的 9 行覆盖的是"阶段性验收"，不是合规认证。** 它说明"离真实生产还差什么"，
   不声称任何安全或合规资质。
5. **`DingTalkNotificationSink` 只被单元测试覆盖，从未对真实 webhook 发过消息。** 失败路径
   （http_error / transport_error / provider_rejected）是分类过的，但都是合成出来的。
6. **单实例假设。** 幂等依赖数据库唯一性与"先观测后行动"的顺序，没有分布式锁；多副本部署
   下的并发收敛尚未验证。

### Phase 10 建议（建议，未实现）

Phase 10 应当是 **Controlled Autonomy & Multi-Environment Governance**，而不是"让 AI 自己
做决定"。建议方向，按依赖顺序：

1. **把 break-glass 变成受治理的流程**：`EmergencyOverride` 目前有模型、有校验、有门禁读取，
   但没有审批链、没有事后复盘记录。Phase 10 应补上"谁批的、为什么、事后是否被复核"。
2. **收敛动作的部分排序与依赖**：目前一次只能执行一个动作，且必须人工选择。可以引入**已声明
   的**动作图（例如 registry 对齐必须先于 runtime 对齐），但仍然不允许自动选路。
3. **多环境 desire 传播**：staging → production 的目标状态目前各环境独立。若要做"晋升即
   目标"，需要一个显式的、可审计的传播机制，而不是让生产环境自动跟随。
4. **告警收敛与风暴抑制**：目前去重粒度是"窗口"。真正的告警风暴需要按 deployment / metric
   家族的聚合策略，这是策略问题不是调度问题。
5. **真实环境验收**（与 Phase 8 相同，仍然最优先）：三档集成测试必须在真实集群、真实身份源、
   真实遥测管道上跑通，`production_deployed` 才可能第一次为 `true`。

一句话：Phase 9 让"完成"必须能被证明；Phase 10 才轮到讨论"在什么条件下可以少问一次人"。
在那之前，`no_op` 会被继续当作一个有记录的决定，而不是一个可以省略的步骤。
