# AIRI Phase 8 Implementation Report

本阶段题目：**Real Production Verification + Trusted Telemetry + Auditable Reconciliation**。
它不是"再加一个 AI 能力"，而是把 Phase 7 留下的三个"接缝"变成可被证明/可被拒绝的运行时事实。

**一句话结论：Phase 8 没有让 AIRI 更聪明，它让 AIRI 更难撒谎。**

---

## 1. Repository Assessment

| 项 | Phase 7 末 | Phase 8 末 |
| --- | --- | --- |
| 测试 | 578 passed / 7 skipped | **608 passed / 14 skipped** |
| 覆盖率 | — | **TOTAL 87%** |
| 迁移 | `0001 … 0009` | `0001 … 0010` |
| 生产 API 路由 | 28 | **51** |
| `production/` 包 | adapters / models / service / persistence / packaging / monitoring / identity | 新增 **probe.py / verification.py / reconciliation.py** |

`SparkProductionAdapter` 在 Phase 7 是一个只会抛 `ProductionAdapterError` 的骨架；本阶段它有了真实的 Thrift 探测、指纹、双确认部署与带控制通道的回滚。同时新增两条 Phase 7 完全没有的边界：**可信身份**与**可信遥测**。

---

## 2. 交接与续作状态

Phase 7 报告的结尾写的是"在真实 Spark/Hive/MySQL、真实 LLM 与真实身份源的环境验收完成之前，任何 Phase 8 的生产集成动作都不应开始"。本阶段对这个前提的处理方式是：**把验收所需的全部接口与证据结构建好，同时把"尚未验收"这件事写进模型，而不是写进注释。**

续作过程中修复的真实缺陷（每一条都曾让某个测试红）：

| # | 位置 | 缺陷 | 影响 | 修复 |
| --- | --- | --- | --- | --- |
| 1 | `migrations/versions/0010_*` | 自动生成的迁移用 `op.create_unique_constraint` | SQLite 无法 ALTER 约束，基线测试 26 errors | 改用 `with op.batch_alter_table(...)`，MySQL / SQLite 双通 |
| 2 | `registry/models.py` `ReleaseEventType` | Phase 8 的 12 类事件只加进了 `DeploymentEventType`，没加进 `ReleaseEventType` | 所有 Phase 8 审计写入抛 `ValidationError`，接口 500 | 同步补进 `ReleaseEventType` |
| 3 | `production/service.py` `reconcile()` | 运行时版本不在注册表中时 `version_by_id()` 直接抛 `RegistryNotFound` | "运行时跑了注册表从未产出过的版本"这一事实无法记录 | 新增 `lookup_version()`，未知版本返回 `None`；`reconcile()` 与 `plan_reconciliation()` 共用 |
| 4 | `production/reconciliation.py` | `from typing import Callable` | ruff UP035 | 改 `collections.abc` |
| 5 | `tests/test_production_integration.py` | `telemetry_source_id="integration-monitor"` | `Identifier` 正则 `^[a-z][a-z0-9_]{0,63}$` 拒绝连字符 | 改 `integration_monitor` |
| 6 | `examples/demo_phase8.py` | 回滚演练排在 8D 对账演练之后 | 人工已把注册表指针移到运行时版本，随后回滚的注册表 CAS 必然冲突 → `rollback_reconciliation_required` | 重排为 8E 先于 8D，并把该交互写入 §37 已知限制 |
| 7 | `adapters.py` / `probe.py` / `verification.py` | 多处 E501、import 未排序 | `ruff check` 15 errors | `ruff format` + 手工修正 1 处 f-string |

第 6 条值得单独说：它不是测试写错，而是**两个正确机制的真实交互**——注册表指针 CAS（Phase 6）与"人工可以把注册表对齐到运行时"（Phase 8）叠加后，一个已经被人为对齐过的指针会让同一部署的后续回滚报出 `rollback_reconciliation_required`。CAS 的行为是对的（回滚的 from-version 已不是活跃版本，就该冲突），但系统目前不会识别"目标状态其实已经满足"。§37 把它列为 Phase 9 的首选项。

---

## 3. 七个不等式与治理边界

Phase 7 有六个不等式。Phase 8 在每一条后面加了"它凭什么算被验证"，并把第七条（回收执行 vs 收敛验证）显式拆出来：

| # | 左侧（廉价且误导） | 右侧（Phase 8 要求） | 本阶段的判定依据 |
| --- | --- | --- | --- |
| 1 | Configured | Verified | `ProductionEnvironmentFingerprint.verified`，由真实探测产生 |
| 2 | Connected | Authenticated | `ProductionRuntimeProbe.authenticated`；NOSASL 恒为 `False` |
| 3 | Authenticated | Authorized | `authorize()` 仍独立于身份来源，可信身份不隐含权限 |
| 4 | Deploy returned success | Runtime verified | `ProductionDeploymentEvidence.status_confirmed` 来自第二次 status 读取 |
| 5 | Monitoring received | Telemetry trusted | `telemetry_trust ∈ {trusted_source, synthetic, unverified}` |
| 6 | Mismatch detected | Mismatch fixed | 只有 `convergence_status == "verified"`，且它只由复观测产生 |
| 7 | Recovery executed | Convergence verified | `production_rollback_reviews.runtime_verified`，同样来自复读 |

新增的三个人工门禁（Phase 7 已各有一条，Phase 8 各加一层）：

1. **Trust Boundary**——一个 header 只有在真实边界之后才算身份（§11）；
2. **Telemetry Attestation**——注册一个生产者不等于信任它（§16）；
3. **Reconciliation Decision**——计划只列可用动作，人选一个（§24）。

---

## 4. Architecture Changes

```text
                      ┌──────────────────────────────┐
   request ──▶ gateway │ TrustedHeaderIdentityProvider │  (trusted_gateway / signed_jwt)
                      └──────────────┬───────────────┘
                                     │ ActorIdentity.trusted ?
   ┌─────────────────────────────────┴──────────────────────────────┐
   │                       _ProductionService                        │
   │  EnvironmentService ── probe ─▶ ProductionRuntimeProbe          │
   │        │                         └─▶ Fingerprint(hash)          │
   │        ├─ DeploymentService ── adapter ─▶ ProductionAdapter     │
   │        │        │                          ├─ Mock              │
   │        │        │                          └─ SparkProduction ◀─┼── ThriftSparkConnection
   │        │        └─ DeploymentEvidence (deploy + status re-read) │      └─ activation ledger
   │        ├─ MonitoringService ── TrustedTelemetrySource           │
   │        │        ├─ freshness(observation_time, received_at)     │
   │        │        ├─ source_event_id 幂等                          │
   │        │        └─ MonitoringExpectation（不建调度器）            │
   │        └─ VerificationService ── build_verification_report      │
   │                 └─ ReconciliationPlan ▸ Review ▸ Result         │
   └──────────────────────────────────────────────────────────────────┘
```

关键结构决定：

1. **适配器仍是唯一出口。** 所有运行时事实（探测、部署、状态、回滚）必须经过 `ProductionAdapter` Protocol 的五个方法；没有任何服务直接连集群。
2. **证据与状态分离。** 部署状态推进写在 `production_deployments`，而"凭什么这么判"写在 `production_deployment_evidence` / `production_verification_reports`，后者带 `evidence_hash`。
3. **没有调度器。** `MonitoringExpectation` 是一个**声明**，可以被查询为 `overdue`，不会自己醒来干活（§21）。
4. **对账不自动执行。** `build_reconciliation_plan()` 是纯函数，产出可用动作 + 理由；执行必须穿过一个 `ReconciliationReview`。

---

## 5. Added / Modified Files

**新增**

| 文件 | 作用 |
| --- | --- |
| `src/airi/production/probe.py` | `ProductionRuntimeProbe` / `run_production_probe()` / `ThriftSparkConnection` |
| `src/airi/production/verification.py` | `build_verification_report()`、逐段判定、`ProductionVerificationPolicy` 门控 |
| `src/airi/production/reconciliation.py` | `build_reconciliation_plan()`、`convergence_status()`、`DECISION_TO_ACTION` |
| `migrations/versions/0010_real_production_verification_*.py` | 8 张新表 + 历史安全列 |
| `examples/demo_phase8.py` | Synthetic Phase 8 Governance Demo |
| `REAL_ENVIRONMENT_CHECKLIST.md` | 真实环境验收清单（人执行） |

**修改**

| 文件 | 改动 |
| --- | --- |
| `src/airi/production/service.py` | 遥测信任、期望、指纹、证据、验证报告、对账计划/评审/结果；`_require_trusted_when_configured()`；`lookup_version()` |
| `src/airi/production/adapters.py` | `SparkProductionAdapter` 全量实现（probe/preflight/shadow/deploy/status/rollback） |
| `src/airi/production/models.py` | `TrustedTelemetrySource`、`MonitoringExpectation`、`ReconciliationPlan/Review/Result`、`ProductionVerificationReport`、新事件类型 |
| `src/airi/production/identity.py` | `TrustedHeaderIdentityProvider` 双模式、`ActorIdentity.trusted`、`require_trusted()` |
| `src/airi/api/production.py` | +23 条路由 |
| `src/airi/core/config.py` | Phase 8 settings（全部默认惰性） |
| `src/airi/main.py` | 身份 provider 装配、`production_connection_factory` 注入 |
| `src/airi/registry/models.py` | `ReleaseEventType` +12 |
| `tests/test_production.py` | +30 测试 |
| `tests/test_production_integration.py` | 重写为不等式守卫 + 三档 marker |
| `tests/conftest.py` / `pyproject.toml` | 新 marker `identity_integration` / `telemetry_integration` |

---

## 6. 8A `SparkProductionAdapter`：从骨架到真实实现

Phase 7 的 `SparkProductionAdapter` 构造时会因为"未配置"抛错。现在它有了完整的可行性分级：

| 属性 | 含义 | 未配置时 |
| --- | --- | --- |
| `configured` | host + username 同时存在 | `False` |
| `authoritative` | 是否代表真实运行时 | 恒 `True`（类属性），但 `configured=False` 时所有方法 `_require()` 抛错 |
| `supports_activation` | 是否配了 activation ledger | `False` |
| `supports_rollback` | `configured and supports_activation` | `False` |

`validate()` 产出的 preflight 检查项是**能力的诚实清单**，每一项非通过即 `not_verified`：

```text
provider_connectivity       passed | failed
read_only_session           passed | not_verified
runtime_identity            passed | not_verified
provider_authentication     passed | not_verified
query_id                    passed | not_verified
cancel                      passed | not_verified
activation_control_channel  passed | not_verified
```

这张表的意义：一个 operator 不需要读代码就能知道"我现在缺什么"。没有 ledger 时，连通性和 shadow 可以是 verified，而**部署控制保持 not_verified**——这是正确的状态，不是失败。

---

## 7. Settings：不再有硬编码地址

`core/config.py` 新增的字段全部默认惰性，未配置即"未验证"，而不是回落到一个内置示例地址：

```
spark_production_host / port / username / database
spark_production_auth_mode: NOSASL | LDAP | KERBEROS | GATEWAY
spark_production_password: SecretStr("")
spark_production_connect_timeout_seconds / query_timeout_seconds
spark_production_session_timezone / catalog
spark_production_activation_ledger          # 可选控制通道
spark_production_cluster_identifier         # 指纹的一部分
spark_production_read_only_attested: bool   # 人工断言，不是探测结果
```

`production_auth_configured` 是一个 property，只在 `auth_mode == "LDAP"` 且密码非空时为真。这一点直接决定了 §2 那条不等式：**NOSASL 会话永远不可能让 `authenticated` 变成 `True`。**

---

## 8. `ProductionRuntimeProbe`：结构化证据

探测不是"ping 通了就算数"，它把能回答的问题逐项记下来：

| 字段 | 来源 | 是否可伪造 |
| --- | --- | --- |
| `reachable` | 连通性查询 | 否 |
| `auth_mode` | settings 与连接握手 | 否 |
| `authenticated` | 仅 LDAP/GATEWAY 且握手成功 | 否 |
| `runtime_identity` / `runtime_identity_source` | 会话中的真实主体 | 否，取不到就是 `"none"` |
| `query_id_available` / `cancel_capability` | 能力探测 | 否 |
| `provider_query_id` | 服务端返回 | 否 |
| `read_only_capability` | 能力探测 | 否 |
| `failure_category` | 失败时的分类 | 否 |

`test_probe_does_not_invent_a_runtime_identity` 是这条的守卫：探测在拿不到主体时返回 `runtime_identity_source="none"`，而不是把 `username` 拼一个出来。

---

## 9. `ProductionEnvironmentFingerprint`：防调包锚点

`fingerprint_hash` 由**观测到的事实**重新推导，参与哈希的是 `(engine_version, cluster_identifier, auth_mode, runtime_user, read_only_attested, …)`，不是环境名。

- `verified` 只在探测真的收到运行时回答时才为真；
- 同一个集群两次独立探测必须得到同一个 hash（集成测试直接断言）；
- 部署包在 `preflight` 时写入当时的 fingerprint hash，后续验证用它检测"环境被换过"。

`test_fingerprint_hash_is_the_anti_swap_anchor` 与 `test_fingerprint_is_only_verified_when_the_runtime_answered` 分别守住这两条。

**合成 profile 无法生成指纹。** `GET /production-environments/{id}/runtime-probe` 与 `POST /{id}/fingerprint` 对 `synthetic_profile=true` 的环境返回 `409 production_runtime_probe_not_applicable` / `409 environment_fingerprint_not_applicable`。demo 的第一幕就是这个。

---

## 10. `ThriftSparkConnection`：连接不等于认证

```python
options = {"auth": "LDAP", "password": ...} if self.auth_mode == "ldap" else {"auth": "NOSASL"}
```

- `NOSASL` 只是"能建立会话"，`authenticated` 保持 `False`；
- `LDAP` 且密码非空才算认证，`runtime_identity` 仍必须来自会话；
- 可选控制通道 `deactivate_metric()`：**没有配置 ledger 时该方法不存在**，回滚因此在 preflight 阶段就被标记为 `not_verified`，而不是等到执行时才失败。

---

## 11. 8B `TrustedIdentityProvider`：信任边界

Phase 7 的 `TrustedHeaderIdentityProvider` 直接信任 `x-airi-actor`。Phase 8 把"信任"变成显式配置：

```
production_identity_trust_boundary: none | trusted_gateway | signed_jwt   (默认 none)
```

`none` 表示**没有边界**：header 一律不可信，所有生产动作失败关闭（`identity_not_trusted`）。这是默认值，也是最安全的值。

`identify()` 的路由：

```
trust_boundary == "trusted_gateway" → _identify_gateway()
trust_boundary == "signed_jwt"      → _identify_jwt()
否则                                 → IdentityNotTrusted("no trusted identity boundary is configured")
```

---

## 12. `trusted_gateway`：共享证明头 + HMAC

```
production_identity_gateway_header  = x-airi-gateway-attestation
production_identity_gateway_secret  = <shared secret>
```

判定用 `hmac.compare_digest()` 常量时间比较，证明头缺失或不匹配即拒绝。**边界的前提是网关必须剥离客户端自带的同名头**；`REAL_ENVIRONMENT_CHECKLIST.md` §3 把"绕过网关直连仍被拒绝"列为必测项——如果这条测不过，说明边界根本没部署。

## 13. `signed_jwt`：HS256 与 issuer 校验

```
production_identity_jwt_secret = <HS256 key>
production_identity_issuer     = <expected iss>
```

`_verify_hs256()` 校验签名与 `iss`；角色只取注册表内已知的 `ROLES` 子集，未签名的 token 拒绝。`test_a_signed_token_is_trusted_and_an_unsigned_one_is_not` 与 `test_a_gateway_attestation_is_what_makes_the_header_trusted` 覆盖两条路径。

---

## 14. `ActorIdentity.trusted` 与失败关闭 `identity_not_trusted`

```python
@property
def trusted(self) -> bool:
    return self.auth_source in {"trusted_header", "external_jwt"}
```

`_require_trusted_when_configured(identity)` 只在**配置了信任边界**时生效：边界为 `none` 时，`MockIdentityProvider` 的本地身份照旧可用（否则测试与 demo 无法运行）；一旦配置了边界，本地身份在部署评审与回滚决策上被直接拒绝。

`test_a_deployed_trust_boundary_refuses_a_local_identity` 是这个开关的守卫。

---

## 15. 决策身份证据（reviewer_auth_source / reviewer_issuer）

每条人工决策都记录"是谁、从哪来、谁担保"：

| 表 | 新增列 |
| --- | --- |
| `production_deployment_reviews` | `reviewer_auth_source`, `reviewer_issuer` |
| `production_rollback_reviews` | `reviewer_auth_source`, `reviewer_issuer` |

这样验证报告里的 `identity` 段落才能判断"这些决策是不是在真实边界之后做出的"，而不是只看有没有 reviewer 名字。

---

## 16. 8C `TrustedTelemetrySource`：注册不等于验证

```text
POST /telemetry-sources            → verified = false   （注册）
POST /telemetry-sources/{id}/verify → verified = true + 谁attest的（验证）
```

`telemetry_source_id` 必须是合法 `Identifier`（`^[a-z][a-z0-9_]{0,63}$`），因此 `synthetic_monitor` 而不是 `synthetic-monitor`。

**未注册的来源直接拒绝：** `ingest()` 先按 `payload.telemetry_source_id` 查表，查不到即 `404 telemetry_source_not_registered`。这一条把"谁都能往监控表写"这个 Phase 7 的洞补上了。

---

## 17. `source_event_id` 幂等

`metric_monitoring_snapshots` 上加了唯一约束 `(telemetry_source_id, source_event_id)`，`ingest()` 先查后写，重复投递返回原快照并附 `duplicate_telemetry_event_ignored` 警告。

语义：**生产者的一个观测事件是一个事实，重放不会变成两个事实。** 注意幂等键是 `(来源, 事件)` 而不是 `(事件)`——两个不同生产者各自上报同一个 id 不应互相吞掉。

---

## 18. `observation_time` 与 `received_at`：两个时钟

| 列 | 含义 | 由谁决定 |
| --- | --- | --- |
| `observation_time` | 观测**发生在**何时 | 生产者 |
| `received_at` | AIRI **收到**它时 | 服务器 `datetime.now(UTC)` |
| `observation_lag_hours` | 两者之差 | 派生 |

生产者时钟错、链路积压、补数据回放——这三种情况都会体现在 lag 上。把它们合成一个"时间戳"就是把这三件事永久隐藏。

---

## 19. `telemetry_freshness()`：fresh / stale / late

按 lag 与策略阈值判定：

- `fresh`——在期望窗口内到达；
- `stale`——超出窗口但仍在容忍范围；
- `late`——远超窗口（如 demo 里的 72 小时回填观测）。

`test_telemetry_freshness_keeps_the_two_clocks_apart` 直接构造两个时钟不同步的输入。
注意：freshness **不改变数据本身**，它只是让"这条数据有多旧"无法被静默忽略。

---

## 20. `telemetry_trust`：trusted_source / synthetic / unverified

| 取值 | 条件 | 效果 |
| --- | --- | --- |
| `trusted_source` | 已注册 + 已 attest + `synthetic=false` | 计入信任遥测 |
| `synthetic` | 已注册但标注为合成 | 记录，但不计入 |
| `unverified` | 已注册但尚未 attest | 记录 + 警告 `telemetry_source_not_trusted` |

三态而不是布尔，是因为"有一个生产者注册了但没人担保"和"这是个假数据源"是两件不同的事，运维需要能区分。

---

## 21. `MonitoringExpectation`：不创建调度器

```text
POST /production-deployments/{id}/monitoring-expectation
GET  /production-deployments/{id}/monitoring-expectation
GET  /production-deployments/{id}/monitoring-expectation/status   → satisfied | overdue | no_snapshot
```

它回答"按声明，现在应该有遥测了吗"，`overdue` 时**只报告，不告警、不重试、不唤醒任何东西**。Phase 8 明确不引入调度器：一个没有观测的期望值本身就是证据，而自动重试会把"上游断了"伪装成"稍后就有"。

`test_monitoring_expectation_schedules_nothing` 与 `test_an_expectation_reports_overdue_telemetry_without_alerting` 守住这条。

---

## 22. Baseline hash 与 monitoring policy 版本化

- `baseline_hash` 记录部署时 shadow 基线（分布、覆盖率、行数）的摘要，随快照一起存；
- `monitoring_policy_version` / `monitoring_policy_hash` 写进快照与告警行。

这样"这次告警是按哪一版策略、对哪一份基线判出来的"可以在事后完整重建。阈值本身仍是经验常量（Phase 7 遗留问题，本阶段未解决，见 §37）。

---

## 23. 8C `ProductionDeploymentEvidence`：两次确认

`deploy()` 的返回值**不是**证据。`ProductionDeploymentEvidence` 由两步构成：

```
1) deploy  → result.status
2) status  → 独立复读，status_confirmed = (runtime_state == "active")
production_deployed = authoritative AND runtime_identity_verified AND status_confirmed
```

demo 里这一段是刻意的对照：mock provider 报告 `deployed` **且** status 复读也返回 `active`，`status_confirmed=True`，而 `production_deployed` 仍然是 `False`——因为没有权威运行时参与。

`test_deployment_evidence_records_what_the_provider_confirmed` 覆盖。

---

## 24. 8D `ReconciliationPlan`：只列可用动作，不宣判真相

`build_reconciliation_plan()` 是纯函数，输入是证据，输出是**可用动作集合 + 理由**：

```python
ACTION_ORDER = ("registry_to_runtime", "runtime_to_registry", "manual_investigation")

registry_to_runtime_available = (
    registry_active is not None
    and registry_active == deployment.metric_version_id   # 本系统确实持有这个包
    and reconciliation.status == "mismatch"
    and adapter_authoritative
    and adapter_supports_recovery
)
runtime_to_registry_available = (
    runtime_active is not None and version_lookup(runtime_active) is not None
)
```

两条硬规则：

1. **计划永远不写"哪一侧是对的"**，只写"能做什么、为什么"；
2. **注册表从未产出过的版本不能被"对齐"**——`version_lookup` 返回 `None` 时 `runtime_to_registry` 不可用，只剩 `manual_investigation`。

`recommended_action` 是推荐，不是判决；`DECISION_TO_ACTION` 把人的决策映射回动作，且**必须落在 `possible_actions` 内**，否则拒绝执行。

`test_reconciliation_plan_never_declares_which_side_is_true` 与 `test_reconciliation_withholds_a_recovery_it_cannot_execute` 覆盖。

---

## 25. 8D `ReconciliationReview`：人工决策

```text
POST /reconciliation-plans/{id}/review        → 开一张评审（不执行任何东西）
POST /reconciliation-reviews/{id}/decision    → 决策 + 执行 + 复观测
```

- `align_runtime_to_registry` → `registry_to_runtime`
- `align_registry_to_runtime` → `runtime_to_registry`
- `keep_mismatch_for_investigation` → `manual_investigation`

评审决策需要角色授权（`align_*` 为 `production_deployer`/`admin`，`keep_*` 更宽），且配置了边界时要求可信身份。

---

## 26. 8D `ReconciliationResult` 与 convergence 复观测

```python
def convergence_status(after: DeploymentReconciliation) -> str:
    if after.status == "consistent":
        return "verified"
    return "still_mismatch" if after.status == "mismatch" else "not_verified"
```

执行完动作后**必须重新 `reconcile()` 一次**，用新的观测结果判定收敛。动作自己的返回码不参与判定。执行不可行时（例如缺少执行通道）结果落在 `reconciliation_verification_required`。

`test_convergence_is_only_ever_confirmed_by_re_observation` 与 `test_reconciliation_executes_then_verifies_convergence` 覆盖。

---

## 27. 允许保留 mismatch 打开

`keep_mismatch_for_investigation` 是一个**一等决策**，不是"什么都没做"：

```text
execution_status = "skipped"
final_status     = "reconciliation_not_executed"
```

注册表指针不会因为"反正先对齐一下"而移动。demo 里第一幕对账就是这种：运行时跑着注册表从未产出过的版本，计划只给 `manual_investigation`，人被允许把问题留在台面上。

---

## 28. 8E Rollback Preflight

回滚前先自检，检查项同样不会"通过式撒谎"：

| 检查 | passed 条件 |
| --- | --- |
| `target_version_exists` | 目标版本在注册表存在 |
| `target_version_released` | 有 release 记录且状态为 `approved`/`active`/`rolled_back` |
| `target_targets_production` | 该 release 的目标环境是 production |
| `environment_unchanged` | 当前指纹与部署包里的指纹一致 |
| `runtime_supports_rollback` | 适配器声明支持控制通道 |
| `human_approval_required` | 恒 passed（这是流程断言） |

`status` 只在有检查项 `failed` 时为 `failed`；`not_verified` 的项全部进 `missing_evidence`，让"缺什么"在**人工批准之前**就被写出来。demo 里输出的是 `missing=['rollback:target_targets_production']`——因为该 release 的目标环境是 staging。

---

## 29. 8E 两阶段回滚 + runtime 复读

```
Phase 1   provider.rollback(deployment_id, to_version_id)      ← 无数据库事务
Phase 1b  adapter.status(deployment_id) → runtime_verified     ← 复读，不是采纳返回值
Phase 2   registry.reconcile_active_version(from, to)           ← 独立事务
```

- Phase 2 失败 → `rollback_reconciliation_required`（具名部分失败，Phase 7 遗留语义保留）；
- `runtime_verified = (runtime_state == "active" and active_version_id == to_version_id)`；
- 决策本身记录 `reviewer_auth_source` / `reviewer_issuer`。

`test_rollback_records_its_preflight_and_runtime_verification` 与 `test_two_phase_rollback_moves_provider_then_registry` 覆盖。

---

## 30. `ProductionVerificationPolicy`

```python
require_rollback_verified: bool = False   # 默认：回滚不是部署成为 verified 的必要条件
```

这条默认值是刻意的。Phase 8 **不允许为了满足策略而制造回滚**：如果策略要求"必须有已验证的回滚"，而事实上没有发生过回滚，报告就停在 `not_verified`，而不是补一条回滚记录。`test_verification_policy_never_requires_a_manufactured_rollback` 守住。

---

## 31. `build_verification_report`：逐段判定

报告的段落是**独立判定**的，不是一个布尔值：

| 段落 | verified 条件（摘要） |
| --- | --- |
| `environment` | 存在指纹且 `verified=true`（非合成） |
| `identity` | 配置了信任边界 **且** 相关决策者身份可信 |
| `adapter` | 适配器 `authoritative` 且已配置 |
| `deployment` | 证据 `status_confirmed` 且 `production_deployed` |
| `runtime_identity` | 探测到运行时主体 |
| `shadow` | shadow 观测成功且只读 |
| `monitoring_source` | 绑定了已 attest 的遥测源（非 synthetic） |
| `rollback` | 回滚评审 `runtime_verified=true` |
| `reconciliation` | 收敛 `verified` |

`overall` 由 `ProductionVerificationPolicy` 对 `blocking` 段做聚合；`production_runtime_verified` 是更窄的"真实运行时确实验过"标记。demo 的输出是：

```text
overall = partially_verified , production_runtime_verified = False
sections = {environment: not_verified, identity: not_verified, adapter: not_verified,
            deployment: not_verified, runtime_identity: not_verified, shadow: verified,
            monitoring_source: verified, rollback: verified, reconciliation: verified}
```

一个 boolean 会把这一切压成"假的"，而真实情况是"环境与身份没验，遥测/回滚/收敛验了"。

---

## 32. Audit Events：Phase 8 新增 12 类

`ReleaseEventType` 新增（同步补进 `DeploymentEventType`）：

```text
production_environment_verified      runtime_identity_verified
production_deployment_evidence_recorded
telemetry_source_registered          telemetry_source_verified
monitoring_expectation_registered     production_verification_recorded
production_rollback_preflight_validated
reconciliation_planned                reconciliation_approved
reconciliation_executed               reconciliation_verified
```

环境与遥测事件记账在环境/遥测自身的作用域上，因此不会出现在某个指标的 event 流里；demo 打印的 `metrics/invoice_amount/events` 是部署维度可见的那一部分。

---

## 33. Database Schema（`0010_real_production_verification`）

`down_revision = "0009_production_monitoring_feedback"`。0001–0009 未改动。

**新表（8）**

| 表 | 主键 | 关键列 |
| --- | --- | --- |
| `production_environment_fingerprints` | `environment_fingerprint_id` | `fingerprint_hash`, `verified`, `read_only_attested`, `fingerprint_json` |
| `production_deployment_evidence` | `deployment_evidence_id` | `deploy_status`, `status_confirmed`, `production_deployed`, `evidence_hash` |
| `trusted_telemetry_sources` | `telemetry_source_id` | `verified`, `synthetic`, `auth_identity`, `registered_by` |
| `monitoring_expectations` | `monitoring_expectation_id` | `expected_interval_hours`, `grace_hours` |
| `reconciliation_plans` | `reconciliation_plan_id` | `verdict`, `recommended_action`, `plan_json`, `plan_hash` |
| `reconciliation_reviews` | `reconciliation_review_id` | `decision`, `decided_by` |
| `reconciliation_results` | `reconciliation_result_id` | `execution_status`, `convergence_status`, `final_status` |
| `production_verification_reports` | `production_verification_run_id` | `overall`, `sections_json`, `blocking_json`, `real_environment_verified` |

**列新增（历史安全）**——所有新列都带 `server_default`，已有行不会变成 `NULL`：

| 表 | 列 | 默认 |
| --- | --- | --- |
| `metric_alerts` | `monitoring_policy_version` | `'1.0.0'` |
| `metric_monitoring_snapshots` | `received_at` | `'1970-01-01 00:00:00'` |
| `metric_monitoring_snapshots` | `telemetry_trust` | `'unverified'` |
| `metric_monitoring_snapshots` | `telemetry_freshness` | `'late'` |
| `metric_monitoring_snapshots` | `monitoring_policy_version` | `'1.0.0'` |
| `production_rollback_reviews` | `runtime_verified` | `false` |

默认值刻意取**最保守**的一侧：老快照一律是 `unverified` + `late`，老回滚一律 `runtime_verified=false`。历史数据不会因为迁移而"变得可信"。

唯一约束 `(telemetry_source_id, source_event_id)` 用 `op.batch_alter_table` 创建，MySQL 与 SQLite 均可用。

---

## 34. APIs

生产 API 从 28 条增至 **51 条**，新增 23 条：

| 分组 | 路由 |
| --- | --- |
| 环境证据 | `GET /production-environments/{id}/runtime-probe`；`POST /production-environments/{id}/fingerprint`(201)；`GET /production-environments/{id}/fingerprints` |
| 可信遥测 | `POST /telemetry-sources`(201)；`GET /telemetry-sources`；`GET /telemetry-sources/{id}`；`POST /telemetry-sources/{id}/verify` |
| 监控期望 | `POST /production-deployments/{id}/monitoring-expectation`；`GET …/monitoring-expectation`；`GET …/monitoring-expectation/status` |
| 部署证据 | `GET /production-deployments/{id}/deployment-evidence`；`GET /production-deployment-evidence/{id}` |
| 验证报告 | `POST /production-deployments/{id}/verification`(201)；`GET …/verification-reports`；`GET /production-verification-reports/{id}` |
| 对账 | `POST /production-deployments/{id}/reconciliation-plans`；`GET …/reconciliation-plans`；`GET /reconciliation-plans/{id}`；`POST /reconciliation-plans/{id}/review`；`GET /reconciliation-reviews/{id}`；`POST /reconciliation-reviews/{id}/decision`；`GET /production-deployments/{id}/reconciliation-results`；`GET /reconciliation-results/{id}` |

错误码语义保持 Phase 7 的形状：**未找到 → 404**（如 `telemetry_source_not_registered` 之外的 `*_not_found`）、**治理冲突 → 409**（`identity_not_trusted`、`reconciliation_not_executed` 之外的 `production_rollback_failed` 等）、**schema 不合法 → 422**。

---

## 35. Demo 与真实环境清单

### `examples/demo_phase8.py` — "Synthetic Phase 8 Governance Demo"

名字本身就是一条声明：**它不是 Real Production Verification Demo**。它跑在离线 fixture + `MockProductionAdapter` 上，因此**不可能**验证真实运行时，并且在每一步都把这件事说出来。它演示的是证据层：

```text
--- 8A  environment registered: prod_synth verified_by=synthetic_profile synthetic=True
        runtime probe       -> 409 production_runtime_probe_not_applicable
        runtime fingerprint -> 409 environment_fingerprint_not_applicable
        => 环境"已验证"为合成 profile，运行时证据 0 字节。Configured is not verified.

--- 8C  producer registered: synthetic_monitor verified=False
        deployment evidence: deploy_status=deployed status_confirmed=True production_deployed=False
        snapshot: trust=unverified freshness=fresh lag_hours=0.0 warnings=['telemetry_source_not_trusted']
        replay of the same source_event_id -> ['duplicate_telemetry_event_ignored']
        backdated observation 3 days old -> freshness=late lag_hours=72.0
        after attestation -> trust=trusted_source warnings=[]
        expectation every 24h (+/-6h) -> status satisfied — no scheduler was created

--- 8B  verification overall: partially_verified ; identity: not_verified
        => 一个布尔会把这些藏起来

--- 8E  rollback preflight: passed missing=['rollback:target_targets_production']
        rollback status=succeeded registry=reconciled runtime_verified=True
        => runtime_verified 来自第二次 provider 读取，不是回滚的返回值

--- 8D  detected: registry 1.0.0 runtime ghost_version - repaired automatically: False
        plan possible=['manual_investigation'] → kept open → reconciliation_not_executed
        plan possible=['runtime_to_registry','manual_investigation'] → executed, convergence=verified
        => 收敛由复观测确认，而不是信任恢复动作的返回值

final  overall=partially_verified production_runtime_verified=False
       rollback: verified  reconciliation: verified
```

产物落在 `examples/phase8/`。运行：

```bash
uv run --frozen python examples/demo_phase8.py
```

### `REAL_ENVIRONMENT_CHECKLIST.md`

一份**给人执行**的清单：前置条件、环境变量、以及每一节末尾那条"区分左右两侧的观测"。它的结论是显式的：

> **A checklist that cannot be completed in your environment is a valid outcome.** Record the gap; do not close it with a simulation.

---

## 36. Test Results 与 Quality Gates

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 全量测试 | `uv run --frozen pytest` | **608 passed, 14 skipped**（基线 578/7） |
| 覆盖率 | `uv run --frozen pytest --cov` | **TOTAL 87%** |
| Lint | `uv run --frozen ruff check src tests` | exit 0 |
| 格式 | `uv run --frozen ruff format --check src tests` | exit 0 |
| 迁移升级 | `AIRI_DATABASE_URL=sqlite+pysqlite:///.demo/phase8_freshchain.db uv run --frozen alembic upgrade head` | 0001 → 0010 全链成功 |
| 迁移当前 | `… alembic current` | `0010_real_production_verification (head)` |
| 迁移漂移 | `… alembic check` | `No new upgrade operations detected.` |
| Demo | `uv run --frozen python examples/demo_phase8.py` | exit 0 |

**新增测试 30 个**（`tests/test_production.py` 由 36 → 66），全部为不等式守卫，例如：

```text
test_fingerprint_hash_is_the_anti_swap_anchor
test_probe_does_not_invent_a_runtime_identity
test_telemetry_freshness_keeps_the_two_clocks_apart
test_monitoring_expectation_schedules_nothing
test_verification_policy_never_requires_a_manufactured_rollback
test_reconciliation_plan_never_declares_which_side_is_true
test_convergence_is_only_ever_confirmed_by_re_observation
test_verification_report_flags_a_swapped_environment
test_a_header_is_untrusted_without_a_real_boundary
test_rollback_preflight_names_missing_evidence
```

**集成测试独立成文件**（`tests/test_production_integration.py`，9 个），三档 marker 各自 gating：

```
production_integration  真实集群可达性 / 状态 / 指纹 / 运行时身份 / shadow / 部署状态 / 回滚
identity_integration    真实信任边界（网关证明头 / 签名 token）
telemetry_integration   真实生产者 attest
```

未配置时**全部 skip 并说明缺什么**，绝不回落到 mock 成功。这就是 `14 skipped` 的构成（9 个 Phase 8 集成 + 3 个 Spark 集成 + 1 个 MySQL 集成 + 1 个显式 marker 选择）。

**Phase 8 模块覆盖率**

| 模块 | 覆盖率 |
| --- | --- |
| `production/reconciliation.py` | 100% |
| `production/persistence.py` | 100% |
| `production/models.py` | 99% |
| `api/production.py` | 95% |
| `production/identity.py` | 88% |
| `production/service.py` | 82% |
| `production/verification.py` | 72% |
| `production/adapters.py` | 58% |
| `production/probe.py` | 22% |

`probe.py` 与 `adapters.py` 的低覆盖是**设计结果而非疏漏**：真实 Thrift 路径只能在配了真实集群时被执行，单测覆盖的是其失败关闭与能力声明分支。

---

## 37. Known Limitations 与 Recommended Next Step

**已知限制（均为实质性）：**

1. **真实生产运行时仍未验收。** 本阶段交付的是"验收所需的证据结构 + 失败关闭路径"。`SparkProductionAdapter` 的真实探测、指纹、双确认部署与真实回滚，只有 operator 执行 `REAL_ENVIRONMENT_CHECKLIST.md` 后才是 VERIFIED。demo 中的一切仍是 mock。
2. **NOSASL 会话永远无法证明运行时身份。** 这是刻意的（连接 ≠ 认证），代价是在只提供 NOSASL 的集群上，`runtime_identity` 段将永久 `not_verified`，`production_verified` 永远达不到。
3. **监控仍是"生产者上报"模型。** 本阶段加的是**信任与新鲜度**，不是采集器：没有流式管道、没有主动拉取。注册+attest 只解决"谁在报"，不解决"有没有报"——后者只能靠 `MonitoringExpectation` 的 `overdue` 被人看见。
4. **期望值不会自我修复。** 没有调度器是刻意的取舍（§21），`overdue` 需要人响应。
5. **对账动作集合仍受适配器能力限制。** `registry_to_runtime` 需要 `adapter_authoritative and adapter_supports_recovery`；`MockProductionAdapter.authoritative=False`，所以 mock 场景下该动作永远不可用，可用集合通常只剩 `runtime_to_registry` 与 `manual_investigation`。
6. **注册表指针被人工对齐后，同一部署的后续回滚会报 `rollback_reconciliation_required`。** 详见 §2 第 6 条：回滚的注册表 CAS 以 `deployment.metric_version_id` 为 from，而该版本可能已不是活跃版本。**系统不会识别"目标状态其实已满足"**，只报具名部分失败要求人工收尾。这是当前最值得修的一处。
7. **阈值仍未用真实数据校准。** `MonitoringPolicy@1.0.0` 的 PSI 档位与其余阈值依旧是经验常量；Phase 8 让它们**可追溯（policy version + hash）**，但没让它们更准。
8. **身份边界依赖网关正确部署。** AIRI 只能验证"证明头与共享密钥匹配"；若网关忘记剥离客户端自带的同名字段，风险仍在边缘。清单 §3 把这条列为必测。
9. **`GATEWAY` 认证模式已建模但未实现握手。** `spark_production_auth_mode` 接受 `GATEWAY`，`production_auth_configured` 对其返回 `False`——即"不可认证"，失败关闭路径已就位，真实握手待环境验收时补。
10. **审计仍是库内单表。** 12 类新事件写入 `metric_release_events`，未投递到外部审计/监控系统。
11. **单实例假设不变。** 指纹/证据/计划都靠哈希自校验，多实例并发绕过服务直接改库会被 `*_changed` 类错误检测到，但不自动修复。
12. **`examples/` 未纳入 ruff 门禁。** 门禁命令是 `ruff check src tests`；`examples/environment_status.py` 存在一处既有的未排序 import，本阶段未处理（不在 Phase 8 范围内）。

**Recommended Next Step（唯一推荐，本次不实现）：**

```text
# AIRI Phase 9
```

Phase 8 把"能不能被证明"这件事落到了运行时、身份、遥测与对账四条线上，并留下了三类**明确没有被解决**的问题：

1. **收敛的完备性**——§37 第 6 条。注册表指针与部署记录在多次人工介入后可能互相脱钩，系统只会报 `rollback_reconciliation_required`，不会判断"目标状态其实已满足"。下一步应把"已满足但 CAS 冲突"与"真的冲突"分开。
2. **监控的采集侧**——§37 第 3、4 条。信任与新鲜度已经就位，但没有采集器与调度，`overdue` 只能被人看见。下一步应回答"谁来报、多久没报、谁来叫醒人"。
3. **验收的强制执行**——`ProductionVerificationPolicy` 现在只影响报告的聚合与标记，不影响动作能否通过。下一步可考虑把"必须真实环境验证"变成一条可在真实 profile 上开启的硬门禁。

在这些成立之前，Phase 8 的交付边界保持不变：**接缝是真的，证据是真的，验没验也是真的。**
