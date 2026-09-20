# AIRI Phase 7 Implementation Report

Production Integration, Monitoring & Feedback Loop。

Phase 6 结束时，`Active Registry Version` 只是内置注册表里的一行指针。Phase 7 把它交给一个生产运行时，但拒绝让任何一步"顺手"发生：客户端不能提交内容，Provider 不能自证健康，告警不能自己回滚，反馈不能被当作解释，回滚不能删除版本。

```text
Registry Active  ≠  Production Active
Production Deployed  ≠  Healthy
Healthy Today  ≠  Stable Tomorrow
Alert  ≠  Rollback
Feedback  ≠  Root Cause
Rollback  ≠  Delete Version
```

本阶段演示为 Mock 执行器 + Mock provider + 合成身份 + `synthetic_profile` 环境。真实 Spark/Hive/MySQL 与真实 LLM 仍未验收；`production_deployed` 在所有记录中恒为 `false`，`SparkProductionAdapter` 的真实路径标记为 SKIPPED / NOT VERIFIED。

## 1. Repository Assessment

基于工作目录实际源码、迁移、测试与 API 的逐行审阅（工作目录无 Git 仓库，结论来自文件时间戳与源码，非提交历史）：

1. Phase 6 交付物完整：`src/airi/registry/`（models / versioning / shadow / release / persistence / service）、`src/airi/api/registry.py`、迁移 `0008_metric_registry_release`、`tests/test_registry.py`、`examples/demo_phase6.py`、`phase6-report.md` 均已存在且通过。
2. Phase 6 基线：**539 passed, 5 skipped**。迁移链 `0001`–`0008` 未被 Phase 7 修改，`0009` 为新增。
3. Phase 7 到场时 `src/airi/production/` 已有脚手架，但 `service.py` 内含大量动态 `__import__(...)`、错误的模型导入路径（`MetricReleaseEventRow` 取自 `airi.registry.models`，实际在 `airi.registry.persistence`）、以及未对齐的 API / 模型签名。它只通过手工调试脚本验证过，**从未对正式测试套件执行**。
4. 复用链完好且被 Phase 7 直接沿用而非复制：`RegistryService`、`canonical_hash`、`decode`、`StrictSchema`、`ExecutionService`、Phase 5 的 `psi_bins()`、Phase 6 的 `metric_release_events` 表。
5. 迁移 `0009` 到场时不存在；`migrations/env.py` 未导入 `airi.production.persistence`；`main.py` 未注册 `production_router`。
6. `README.md` "下一步" 仍指向 Phase 7；`phase7-report.md` 缺失；`examples/phase7/` 无产物。
7. 无生产 SQL 执行 API，无第二套审计表，无第二套 PSI 实现——这是本阶段刻意保持的约束。

## 2. 交接与续作状态

Phase 7 的续作从"脚手架能跑通"推进到"能被证明"，共修复 8 类真实缺陷：

| # | 位置 | 缺陷 | 影响 | 修复 |
| --- | --- | --- | --- | --- |
| 1 | `production/service.py` | 大量动态 `__import__(...)` | 依赖关系隐式、无法静态检查、ruff 无法排序 | 全部改为顶层导入 |
| 2 | `production/service.py` | `MetricReleaseEventRow` 从 `airi.registry.models` 导入 | `ImportError`，审计写入链路整体不可用 | 改从 `airi.registry.persistence` 导入 |
| 3 | `production/service.py` `_replace` | 把 pydantic 模型 / `datetime` 直接塞进 `decode` 的 `json.dumps` | `ProductionPreflightResult is not JSON serializable`，preflight / shadow 全 500 | 新增 `_jsonable()`，按 `model_dump(mode="json")` / `isoformat()` 递归归一 |
| 4 | `production/persistence.py` | `ProductionDeploymentReviewRow` 缺 `package_hash` 列 | `decide_review` 500 | 补列 + 迁移 |
| 5 | `production/persistence.py` | `ProductionRollbackReviewRow` 缺 `provider_job_id` 列 | 回滚评审无法记录 provider 作业号 | 补列 + 迁移 |
| 6 | `production/models.py` | `verification_source` Literal 缺 `"none"` | 环境注册 `KeyError` | 补 `"none"` 并设为默认 |
| 7 | `production/models.py` | `MonitoringSnapshotRequest.observation_time` 在 strict 模式下拒绝 ISO 字符串 | 监控快照写入 422 | 加 `field_validator(..., mode="before")` 做 ISO 归一 |
| 8 | `production/adapters.py` | `adapter_factory` 返回值形态不统一（有时是实例，有时是工厂） | `service._resolve_adapter` 调用姿态不一致 | 统一为 `(session, state, request_id) -> adapter` 的 callable |

另外 API / 模型签名对齐 3 处：`preflight` / `shadow` 的 `response_model` 应从"整个部署"改为"该段产物"；`monitoring-snapshots` 的返回应是 `MonitoringIngestResult`（含 `snapshot` / `alerts` / `created_alert_ids` / `warnings`）而非单个 `MonitoringSnapshot`。

这类缺陷有共同点：**状态既要写在列上（供查询与 CAS），又要写在文档里（供读取与哈希）**。凡两者只写其一，下一次读取就会得到一份自相矛盾的数据。Phase 6 修过 `MetricDefinition` / `MetricRelease` 的同类漂移，Phase 7 在 `production_deployment_reviews` / `production_rollback_reviews` 上补齐。

## 3. 六个不等式与治理边界

| 概念 | 身份 | 谁能创建 | 本阶段是否代表生产 |
| --- | --- | --- | --- |
| Registry Active Version | 内置注册表指针（Phase 6） | Release Review + CAS | 否，仅逻辑指针 |
| DeploymentPackage | 从受治理谱系组装出的不可变交付物 | 服务器（客户端只提交 `release_id` + `environment_id`） | 否 |
| Production Environment | 独立验收的生产环境对象 | `admin` 注册 + 服务器判定 `verified` | 否 |
| Production Deployment | 一次受治理的上线流程 | Deployment Review 通过后由 provider 交接 | 状态可推进，但 `production_deployed=false` |
| Metric Alert | 观测偏离的告警 | 监控摄取时由确定性规则产生 | 否，只是信号 |
| Feedback Event | 一条**事实** | 由告警或人工记录 | 否，不含解释 |

三条独立人工门禁（Phase 1.5 SQL Approval / Phase 5 Promotion Review / Phase 6 Release Review）之上，Phase 7 又加了三条，且同样不可合并：

1. **Environment Verification**——这个环境是否被独立验收为生产可用（由服务器判定，非请求体）；
2. **Deployment Review**——这个包能不能交给 provider；
3. **Rollback Approval**——这次回滚该不该执行（角色必须是 `rollback_approver`）。

任何一条通过都不隐含另一条通过；告警不是回滚，反馈不是根因，回滚不删版本。

## 4. Architecture Changes

新增包 `src/airi/production/`，作为 Phase 6 注册表之上的一层"生产接线"，不改动 Phase 1–6 的任何语义：

```text
core/            StrictSchema / canonical_hash / decode / AIRIError（复用）
environments/    EnvironmentStatus 扩展 spark_production / production（复用）
temporal/        psi_bins() 提升为唯一 epsilon 平滑实现（复用）
registry/        RegistryService：版本 / release / 激活指针 / 审计表（复用）
production/
  identity.py    ActorIdentity / TrustedIdentityProvider / authorize / ACTION_ROLES
  models.py      Environment / Package / Deployment / Preflight / Shadow / Review
                 Monitoring / Alert / Feedback / Research / Rollback / Reconciliation
  packaging.py   DeploymentPackage 组装 + deployment_package_hash + package_failures
  adapters.py    ProductionAdapter Protocol + Disabled / Mock / Spark + adapter_factory
  monitoring.py  MonitoringAssessor：确定性发现 + finding_dedup_key
  persistence.py 9 张 Phase 7 表的 ORM 行
  service.py     Environment / Deployment / Monitoring / Alert / Feedback / Rollback / Reconciliation
api/production.py 28 个端点
main.py          identity_provider + production_adapter_factory 装配
```

关键设计取舍：

- **身份取代字符串。** Phase 6 的 `reviewer` 是调用方自报字符串；Phase 7 一律先解析 `ActorIdentity` 再授权。
- **状态机显式。** 非法迁移一律 409，不做隐式补状态。
- **两阶段回滚。** provider 与 registry 指针在两个独立事务里，部分失败有专门命名。
- **复用而不复制。** PSI 只有一份实现，审计只有一张表，release / version 读取只走 `RegistryService`。

## 5. Added / Modified Files

新增：

```text
src/airi/production/__init__.py
src/airi/production/identity.py
src/airi/production/models.py
src/airi/production/packaging.py
src/airi/production/adapters.py
src/airi/production/monitoring.py
src/airi/production/persistence.py
src/airi/production/service.py
src/airi/api/production.py
migrations/versions/0009_production_monitoring_feedback_production_monitoring_feedback.py
tests/test_production.py
tests/test_production_integration.py
examples/demo_phase7.py
phase7-report.md
examples/phase7/…（demo 产物 22 个）
```

修改（均为向后兼容的扩展）：

```text
src/airi/core/config.py         新增 production_adapter / identity / spark_production_* 设置
src/airi/environments/models.py Environment Literal 增加 spark_production / production
src/airi/main.py                identity_provider + production_adapter_factory 装配、注册 router
src/airi/observability/logging.py  JsonFormatter 白名单新增 Phase 7 字段
src/airi/temporal/statistics.py psi_bins() 提升为公共实现（Phase 5 亦改为调用它）
migrations/env.py               导入 airi.production.persistence
tests/conftest.py               集成标记列表新增 production_integration
tests/test_phase2.py            app_for() 转发 **extra 到 create_app
pyproject.toml                  注册 production_integration 标记
README.md                       新增 Phase 7 章节
```

未改动：迁移 `0001`–`0008` 一字未动；Phase 1–6 的模型、服务与 API 语义不变。

## 6. 身份边界：ActorIdentity 取代调用方字符串

```python
class ActorIdentity(StrictSchema):
    actor_id: Identifier            # ^[a-z][a-z0-9_]{0,63}$
    display_name: NonEmptyText
    auth_source: AuthSource         # trusted_header | external_jwt | mock | anonymous
    roles: tuple[Role, ...]

Role = Literal["metric_reviewer", "release_reviewer",
               "production_deployer", "rollback_approver", "admin"]
```

每个生产动作都先 `require_authenticated(identity)` 再 `authorize(identity, action)`：

- 未认证（`auth_source="anonymous"`）→ **401** `actor_identity_required`；
- 已认证但角色不符 → **403** `actor_not_authorized`；
- 未知 action → 编程错误（`KeyError` 显式抛出，不静默放行）。

Phase 6 的 `reviewer: str` 只能用于受控研究环境；Phase 7 的身份**只能来自外部信任源**，请求体里没有任何字段可以自报角色。

## 7. TrustedIdentityProvider 与失败关闭

```python
class TrustedIdentityProvider(Protocol):
    def identity_for(self, request) -> ActorIdentity: ...
```

实现：

| Provider | 用途 | 生产可选 |
| --- | --- | --- |
| `DisabledIdentityProvider` | 恒抛 `actor_identity_required`（401） | 是（默认） |
| `TrustedHeaderIdentityProvider` | 信任上游网关注入的 header | 是（需 `production_identity_provider=trusted_header`） |
| `MockIdentityProvider` | 测试与合成 demo | **否**，配置不可选 |

生产模式下若未配置 provider，`create_app` 装配的是 `DisabledIdentityProvider`，**所有生产动作失败关闭**。`MockIdentityProvider` 只能由测试显式注入；它不出现在 `Settings.production_identity_provider` 的 `Literal` 里，因此无法通过环境变量启用。演示中 401 → 403 的角色链路都被显式验证。

## 8. RBAC 矩阵：action → roles

固定矩阵（`ACTION_ROLES`），`admin` 满足每一条：

| action | 允许的角色 |
| --- | --- |
| `register_production_environment` | `admin` |
| `request_production_deployment` | `production_deployer`, `admin` |
| `review_production_deployment` | `production_deployer`, `admin` |
| `approve_production_rollback` | `rollback_approver`, `admin` |
| `publish_monitoring_snapshot` | 任意已认证角色 |
| `record_feedback` | 任意已认证角色 |
| `decide_feedback_research` | `metric_reviewer`, `admin` |

规则被显式测试：`production_deployer` 批准**回滚** → 403；`rollback_approver` 批准回滚 → 通过。角色不能互相覆盖，`admin` 是唯一短路项。

## 9. 生产环境是独立验收对象（ProductionEnvironmentProfile）

```python
class ProductionEnvironmentProfile(StrictSchema):
    environment_id: Identifier
    environment_kind: Literal["spark_production", "production"]
    verified: bool = False
    verification_source: Literal["environment_validation_report",
                                 "synthetic_profile", "none"] = "none"
    environment_validation_run_id: str | None = None
    deployment_enabled: bool = True
    read_only_preflight: bool = True
    profile_hash: str
```

- `spark_test`**永不复用**为生产：`environment_kind` 只接受 `spark_production` / `production`。
- `verified` **由服务器判定**：要么来自 Phase 2.5 环境验收报告（`environment_validation_report`），要么来自显式 `synthetic_profile`（仅测试/demo）。
- 生产模式下提交 `synthetic_profile` → **409** `synthetic_production_profile_forbidden`；且 `synthetic_profile` 要求 `execution_mode=mock`。

## 10. 环境门禁：verified 由服务器判定

未验收环境（`verified=false`）在 **4 处**独立拦下，任何一处都不会被绕过：

| 位置 | 错误码 | HTTP |
| --- | --- | --- |
| 创建部署 | `production_environment_not_verified` | 409 |
| preflight | `production_environment_not_verified` | 409 |
| deployment review 决策 | `production_environment_not_verified` | 409 |
| deploy | `production_environment_not_verified` | 409 |

演示验证：已注册但 `verified=false` 的环境 → 409；未注册环境 → 404 `production_environment_not_found`。`deployable` = `verified and deployment_enabled`；`deployment_enabled=false` 同样在 preflight 的 `environment_verified` 检查上失败关闭。

## 11. DeploymentPackage：客户端只能提交标识符

`POST /production-deployments` 的请求体只有：

```python
class ProductionDeploymentRequest(StrictSchema):
    release_id: Identifier
    environment_id: Identifier
```

IR / SQL / artifact / 版本号 / 内容哈希 / 阈值**全部从受治理谱系恢复**：

```text
release_id → MetricRelease
           → ReleaseValidationReport
           → MetricVersion(不可变, content_hash)
           → metric_ir_hash / artifact_id / artifact_hash / sql (from artifact)
           → release_review_id
environment_id → ProductionEnvironmentProfile(profile_hash)
```

客户端无法注入任何内容字段。这不是"校验后再用"，而是**根本没有接收内容的通道**。同样地，**不存在任何生产 SQL 执行 API**；SQL 只能作为 `DeploymentPackage` 的一部分交给 provider，且只能来自受治理 artifact。

## 12. deployment_package_hash 与包完整性

```text
deployment_package_hash = sha256(canonical_hash({
    metric_version_id, metric_version_content_hash, metric_ir_hash,
    artifact_id, artifact_hash, sql_hash,
    release_id, release_review_id, release_validation_report_hash,
    environment_id, environment_profile_hash,
}))
```

任何一环被改动（例如有人绕过 registry 改了 SQL 或换了环境 profile），`package_failures()` 立即列出不一致字段，部署读取 / review / deploy 三处返回 **409** `deployment_package_integrity_error`。这个哈希同时被持久化到 `production_deployment_reviews.package_hash`，因此 review 决策时的包与被部署的包可证明是同一份（不一致 → `production_deployment_evidence_changed`）。

## 13. Preflight：只读、失败关闭

`preflight` 由 **8 项治理门禁 + provider 3 项**组成（Mock provider 共 11 项）：

| # | 检查 | 通过条件 |
| --- | --- | --- |
| 1 | `environment_verified` | `profile.deployable` |
| 2 | `environment_validation` | `verification_source != "none"`（`synthetic_profile` 标 `not_verified`） |
| 3 | `release_approved` | release 处于可发布状态 |
| 4 | `release_evidence` | validation report `passed`/`passed_with_warnings` 且 eligible |
| 5 | `package_integrity` | `package_failures()` 为空 |
| 6 | `metric_version_immutable` | 包内 `content_hash` == 注册表谱系 |
| 7 | `read_only_preflight` | profile 声明只读 |
| 8 | `release_target_environment` | `release.target_environment == "production"` |
| 9 | `read_only_session`（provider） | Mock 声明只读会话 |
| 10 | `governed_artifact_approval`（provider） | artifact 经过审批 |
| 11 | `runtime_identity`（provider） | 运行身份已验证（Mock 恒 `not_verified`） |

语义是**失败关闭**：`check(name, ok, detail, not_verified=False)`，`ok=False` 且非"诚实未验证"即记 `failed`；只要有一项 `failed`，整体 `status=failed`，状态迁移到 `failed` 并抛 **409** `production_preflight_failed`。**没有 warning 通道能到达 deploy**。`missing_evidence` 收集所有 `not_verified` 项，`production_deployed` 在 preflight 结果里恒为 `false`。

## 14. Production Shadow 与 baseline

Shadow 在 `preflight_validated` 之后、`deployment_review` 之前运行，使用 Phase 6 的执行器加载随包 fixture：

- `write_business_output=false`——只读观测，绝不写生产业务输出；
- 结果成为后续监控的 `baseline`（来源标记 `production_shadow`）；
- 执行失败 → `production_shadow_execution_failed`；无实体 → 不静默通过；重复实体 → 显式记录；
- 未通过 shadow 不能进入 review（`production_shadow_not_passed`）。

演示：rows 401 / entities 401 / coverage 1.0，`write_business_output=false`。

## 15. 部署状态机

```text
created
  → preflight_validated        (preflight 通过)
  → shadow_running             (shadow 开始)
  → shadow_validated           (shadow 通过)
  → pending_deployment_review  (提交评审)
  → approved                   (人工 Deployment Review 通过)
  → deployed                   (provider 交接完成)
  → monitoring                 (首个监控快照)

旁支：failed
      rolled_back
      rollback_reconciliation_required
```

每个动作都校验前驱状态：

| 动作 | 要求状态 | 违规错误码 |
| --- | --- | --- |
| preflight | `created` | `production_deployment_not_preflightable` |
| shadow | `preflight_validated` | `production_deployment_not_shadowable` |
| review | `shadow_validated` | `production_deployment_not_ready_for_review` |
| deploy | `approved` | `production_deployment_not_approved` |
| rollback | `deployed`/`monitoring` | `production_deployment_not_rollbackable` |

`created → deployed` 这类跳转不是部署流程，一律 **409** `production_deployment_state_invalid`。同一 `metric_version` 不能同时进入两条生产流程（`version_already_in_production_flow`）。

## 16. ProductionDeploymentPolicy@1.0.0

生产模式**没有 canary / 蓝绿**。策略是版本化常量：

```python
deployment_strategy: Literal["shadow", "manual_cutover"] = "shadow"
cutover_mode:       Literal["manual"] = "manual"
shadow_write_business_output: Literal[False] = False
automatic_rollback: Literal[False] = False
```

即策略本身在模型层就禁止了"自动切换"和"自动回滚"——不是靠调用方守规矩，而是靠类型。演示里 `test_deploy_policy_is_versioned_and_never_automatic` 直接断言这三个常量。

## 17. 诚实标记：production_deployed 的模型级不变量

`ProductionDeployment` 的校验器要求：

```text
production_deployed=true  ⟹  runtime_mode == "spark_production"
                         and  runtime_identity_verified == true
```

Mock provider 可以推进状态机（`status=deployed` / `monitoring`），但 `production_deployed` **恒为 `false`**，并把缺失证据写入 `missing_evidence`：

```text
["real production runtime identity not verified",
 "synthetic mock provider; no real production runtime"]
```

这不是"忘记设置"，而是模型级不变量：任何试图在 Mock 上声称 `production_deployed=true` 的构造都会 `ValidationError`。这条不变量把"治理状态推进"和"真实生产已部署"彻底分开。

## 18. ProductionRuntimeBinding：运行时身份

```python
class ProductionRuntimeBinding(StrictSchema):
    runtime_mode: Literal["disabled", "mock", "spark_production"]
    runtime_identity_verified: bool
    provider_job_id: str | None
    bound_at: datetime | None
```

绑定记录运行时实际身份。**身份未验证时 `provider_job_id` 为 `null`**——不伪造一个作业号。reconcile 用它对比注册表活跃指针与运行时状态。

## 19. Provider 抽象：Disabled / Mock / Spark

```python
class ProductionAdapter(Protocol):
    runtime_mode: Literal["disabled", "mock", "spark_production"]
    configured: bool
    authoritative: bool
    def validate(self, package) -> ProductionValidationResult: ...
    def observe(self, package) -> ...: ...
    def deploy(self, package, deployment_id) -> ...: ...
    def status(self, deployment_id) -> ProductionRuntimeStatus: ...
    def rollback(self, deployment_id, target_version_id=None) -> ...: ...
```

| Adapter | `configured` | `authoritative` | 用途 |
| --- | --- | --- | --- |
| `DisabledProductionAdapter` | `false` | `false` | 默认；preflight 直接失败关闭（`adapter` 检查 `failed`） |
| `MockProductionAdapter` | `true` | **`false`** | 测试 / demo；可推进状态机但绝不权威 |
| `SparkProductionAdapter` | 视配置 | `true` | 真实集群接缝；未配置即失败关闭 |

`adapter_factory(config)` 统一返回 `(session, state, request_id) -> adapter` 的 callable，并在生产模式下拒绝 Mock（`mock_not_allowed_in_production`）。`test_adapter_factory_returns_uniform_callable` 断言了这一形态。

`rollback(deployment_id, target_version_id=None)` 的第二个参数是 Phase 7 新增的：两阶段回滚需要显式指定回滚目标版本，而不能"回到上一个"这种模糊语义。

## 20. SparkProductionAdapter：SKIPPED / NOT VERIFIED

这是本报告最需要诚实的一节。

`SparkProductionAdapter` 是**真实集群的集成接缝**，本仓库**没有**可用的生产集群。因此：

- 未配置时它**失败关闭**，`configured=false`、`authoritative=false`，`status()` 只能返回 `unknown`，绝不猜测；
- 它的真实路径（连接集群、读取权威身份、执行回滚）在当前环境**从未被执行**；
- 专用测试 `tests/test_production_integration.py` 由 `production_integration` 标记守卫，默认跳过（`Explicit integration marker selection required`），且 fixture 要求 `environment=production` + `production_adapter=spark_production` + `spark_production_host` + `spark_production_username` + `spark_production_read_only_attested` 全部满足，否则 `pytest.skip`；
- 两个集成测试 `test_real_adapter_is_reachable_and_authoritative` / `test_real_status_comes_from_the_cluster` 在本环境**全部跳过**。

**结论：`SparkProductionAdapter` = NOT VERIFIED。** 任何"生产集成已完成"的说法都必须排除这一项。

## 21. Monitoring：复用 Phase 5 PSI

Phase 5 已经实现了 PSI 分箱。Phase 7 **不新增第二套实现**：把 `psi_bins()` 提升为 `temporal/statistics.py` 中的唯一 epsilon 平滑实现，runtime PSI 直接调用它。

```python
def runtime_psi(baseline: MonitoringDistribution,
                current: MonitoringDistribution) -> MonitoringPSI:
    ...
```

`test_runtime_psi_reuses_the_phase5_binning` 直接断言 runtime PSI 与 Phase 5 `psi_bins()` 在相同输入下给出相同结果——如果将来有人复制粘贴出第二套实现，这条测试会失败。分布模型保证 NULL 分箱只能在末尾且唯一（`MonitoringDistribution` 校验器）。

## 22. MonitoringPolicy@1.0.0 与阈值

```python
max_execution_failure_rate: float = 0.2
latency_review_above_ms:     int   = 600_000
coverage_drop_review:        float = 0.05
null_rate_increase_review:   float = 0.05
duplicate_rate_review:       float = 0.02
psi_review_above:            float = 0.2
psi_critical_above:          float = 0.35
psi_epsilon:                 float = 0.0001
```

阈值是版本化常量，**只用于产生告警，永不触发回滚**。PSI 有两个档位（review 0.2 / critical 0.35），其余阈值通过确定性升级规则决定 severity。

## 23. MonitoringAssessor：确定性发现与去重键

`MonitoringAssessor` 对同一份快照给出**确定性**发现（同输入同输出，无随机、无时间依赖）：

- `_escalate(value, threshold)` → `critical` if `value > 2 × threshold` else `warning`；
- 发现类型：`execution_failure` / `latency_degradation` / `coverage_drop` / `null_rate_increase` / `duplicate_increase`；

```text
finding_dedup_key = stable_hash(deployment_id, finding_type, severity)
```

去重键**只**依赖 (deployment, type, severity)，因此同一异常重复上报不会产生新告警，只累加 `occurrences`。`test_dedup_key_is_stable_and_scoped` 断言稳定性与作用域隔离；`test_severity_escalation_is_deterministic` 断言升级规则。

## 24. MetricAlert：告警不是回滚

```python
class MetricAlert(StrictSchema):
    alert_id: str
    deployment_id: str
    alert_type: MonitoringFindingType
    severity: Literal["warning", "critical"]
    dedup_key: str
    occurrences: int = 1
    recommended_action: Literal["monitor", "investigate", "consider_rollback"]
    automatic_action_taken: Literal[False] = False
    status: Literal["open", "acknowledged", "resolved"] = "open"
```

- 由 (deployment, type, severity) 去重键聚合；重复只累加 `occurrences`；
- `automatic_action_taken` **恒为 `false`**——类型级不变量；
- `recommended_action` 由 severity 推导：`warning → investigate`，`critical → consider_rollback`；即便建议 `consider_rollback`，它也只是**建议**；
- 告警决策 API **不含任何回滚动作**。

`test_alert_decision_never_rolls_back` 断言：即便产生 critical 告警并要求 `consider_rollback`，注册表活跃指针与部署状态都未被改动。

## 25. 告警处置（AlertDecision）

`POST /metric-alerts/{id}/decision` 接受 `acknowledge` / `resolve`：

- `acknowledge` → `acknowledged`，部署**仍为 `monitoring`**（无任何执行）；
- `resolve` → `resolved`；
- 处置不改变运行时，不移动指针，不删除任何东西。

演示：`acknowledge` 后部署仍 `monitoring`。

## 26. MetricFeedbackEvent：反馈是事实

```python
class FeedbackEvent(StrictSchema):
    record_kind: Literal["fact"] = "fact"
    interpretation: None = None
    automatic_research_started: Literal[False] = False
    evidence_refs: tuple[str, ...]
    source_alert_id: str | None
```

- `record_kind="fact"`、`interpretation=null`、`automatic_research_started=false`——三者都是**类型级**约束；
- 证据引用必须能被解析，否则 **404** `feedback_evidence_ref_not_found`（防止凭空捏造证据）；
- 反馈由告警生成（`source_alert_id`）或人工记录。

`test_feedback_is_a_fact_not_an_interpretation` 断言：反馈记录里没有解释字段，且不会自动开始研究。**反馈不是根因**——它只是"发生了什么"，不是"为什么"。

## 27. FeedbackResearchRequest：不会启动 reflection

`POST /metric-feedback-events/{id}/research-requests` 记录"人类要求重新验证 / 新实验"，其决策（`POST /feedback-research-requests/{id}/decision`）由 `metric_reviewer` 作出：

- 决策结果带 `automatic_reflection=false`；
- **不会**启动 reflection、不会创建实验、**不会改写任何指标**；
- 演示直接断言 `version_registered` 事件数保持不变（2 个，只有 v1/v2）——指标谱系一字未动。

这样"反馈闭环"不会变成"自动改指标"的旁路。要改指标，必须重新走 Phase 4/5/6 的完整治理链。

## 28. Rollback：两阶段（provider 先，registry 后）

回滚**不是删除版本**，而是移动注册表活跃指针并把被替换版本置 `retired`。流程：

```text
请求(production_deployer) → ProductionRollbackReview(pending)
  → 人工批准(rollback_approver)：
     阶段 1：provider.rollback(deployment_id, target_version_id)   [独立事务]
     阶段 2：RegistryService.reconcile_active_version(...)          [独立事务]
```

- 阶段 1 与阶段 2 在**两个独立事务**里，各自提交；
- `retired` 版本保留在版本表中，历史可查；
- 回滚目标不能已是活跃版本（`rollback_target_already_active`）；
- 角色校验：`production_deployer` 批准 → **403**；`rollback_approver` 批准 → 执行。

`test_two_phase_rollback_moves_provider_then_registry` 断言正确顺序与最终指针移动。

## 29. 部分失败的命名：rollback_reconciliation_required

最关键的诚实设计：**provider 成功而 registry 失败时，绝不静默成功**。

| 阶段 1（provider） | 阶段 2（registry） | 结果 |
| --- | --- | --- |
| 成功 | 成功 | `production_rollback_status=succeeded`、`registry_reconciliation_status=reconciled` |
| 成功 | 失败 | **`rollback_reconciliation_required`**，两侧状态分别记录 |
| 失败 | 未执行 | `production_rollback_failed` |

部署状态机进入 `rollback_reconciliation_required`（一个**专门的、命名了部分失败**的状态），而不是笼统的 `failed`。`test_partial_rollback_failure_names_the_reconciliation_state` 构造了这种部分失败并断言状态命名正确、两侧证据都在。

命名即诚实：一个含糊的 `failed` 会让人以为回滚整体没发生；`rollback_reconciliation_required` 明确告诉运维"provider 侧已经动了，registry 侧没动，需要人工对账"。

## 30. Reconciliation：只检测

`GET /production-deployments/{id}/reconcile` 对比注册表活跃指针与运行时状态，输出三种结果：

| 结果 | 含义 |
| --- | --- |
| `consistent` | 注册表活跃版本 == 运行时活跃版本 |
| `mismatch` | 二者不一致 |
| `unknown` | 运行时身份未验证，无法判断 |

```python
automatic_correction: Literal[False] = False
```

- `mismatch` **只产生 critical 告警**，不移动任何指针、不改任何状态；
- `unknown` 是诚实结果，不冒充 `consistent`。

`test_reconciliation_detects_mismatch_without_repairing` 故意让运行时指向旧版本，断言 reconcile 报 `mismatch`、产生 critical 告警，但注册表指针与部署状态**都未被改动**。

## 31. Audit Events：单表 13 类

Phase 7 把审计写入 Phase 6 已有的 `metric_release_events`，**不新增第二张审计表**。13 类事件：

```text
1  production_environment_registered     环境注册（保留作用域 production_environment）
2  production_preflight_validated        preflight 结果
3  production_shadow_started             shadow 开始
4  production_shadow_validated           shadow 结果
5  production_deployment_approved        人工 Deployment Review 通过
6  production_deployed                   provider 交接完成（含 production_deployed=false 的诚实值）
7  monitoring_snapshot_ingested          监控快照摄取
8  monitoring_alert_created              告警产生
9  feedback_recorded                     反馈记录
10 rollback_started                      回滚开始
11 production_rolled_back                回滚完成
12 deployment_reconciled                 对账一致
13 deployment_reconciliation_required    对账需要人工处理
```

环境注册不属于任何指标，故以其**保留作用域 `production_environment`** 记账，而不是硬塞给某个 `metric_definition_id`。演示结尾打印的事件尾部包含 `deployment_reconciled` / `deployment_reconciliation_required` / `monitoring_alert_created` / `rollback_started` / `production_rolled_back`。

## 32. 门禁错误码总表

所有治理失败都是 409 域错误（`ProductionError`），资源不存在是 404（`ProductionNotFound`），身份缺失是 401，角色不符是 403。

| 错误码 | HTTP | 触发 |
| --- | --- | --- |
| `actor_identity_required` | 401 | 未认证 / 生产模式 provider 缺省 |
| `actor_not_authorized` | 403 | 角色不在 `ACTION_ROLES[action]` |
| `production_environment_not_found` | 404 | 未注册环境 |
| `environment_validation_not_found` | 404 | 验收报告缺失 |
| `production_deployment_not_found` | 404 | 部署不存在 |
| `production_deployment_review_not_found` | 404 | 部署评审不存在 |
| `production_rollback_review_not_found` | 404 | 回滚评审不存在 |
| `metric_alert_not_found` | 404 | 告警不存在 |
| `feedback_event_not_found` | 404 | 反馈不存在 |
| `feedback_evidence_ref_not_found` | 404 | 反馈证据无法解析 |
| `feedback_research_request_not_found` | 404 | 研究请求不存在 |
| `synthetic_production_profile_forbidden` | 409 | 生产模式提交 synthetic profile |
| `synthetic_profile_requires_mock_execution` | 409 | synthetic profile 非 mock 执行 |
| `production_environment_already_registered` | 409 | 环境重复注册 |
| `production_environment_integrity_error` | 409 | 环境列/文档漂移 |
| `production_environment_not_verified` | 409 | `verified=false`（4 处门禁） |
| `deployment_package_integrity_error` | 409 | 包哈希不一致 |
| `production_deployment_integrity_error` | 409 | 部署列/文档漂移 |
| `production_deployment_state_invalid` | 409 | 非法状态迁移 |
| `production_deployment_not_preflightable` | 409 | 非 `created` 状态做 preflight |
| `production_deployment_not_shadowable` | 409 | 非 `preflight_validated` 做 shadow |
| `production_deployment_not_ready_for_review` | 409 | 非 `shadow_validated` 提交评审 |
| `production_deployment_not_approved` | 409 | 非 `approved` 做 deploy |
| `production_deployment_not_rollbackable` | 409 | 非 deployable/monitoring 做回滚 |
| `production_deployment_not_monitoring` | 409 | 非 monitoring 摄取监控 |
| `production_deployment_review_already_exists` | 409 | 重复提交评审 |
| `production_deployment_review_already_decided` | 409 | 重复决策 |
| `production_deployment_review_missing` | 409 | 缺评审就 deploy |
| `production_deployment_review_not_approved` | 409 | 评审未通过 |
| `production_deployment_evidence_changed` | 409 | 包哈希与评审时不一致 |
| `production_preflight_failed` | 409 | preflight 有 `failed` 检查 |
| `production_shadow_failed` | 409 | shadow 未通过 |
| `production_shadow_not_passed` | 409 | 未通过 shadow 就进 review |
| `production_deployment_failed` | 409 | provider 交接失败 |
| `production_rollback_already_decided` | 409 | 回滚评审重复决策 |
| `production_rollback_failed` | 409 | provider 回滚失败 |
| `rollback_reconciliation_required` | 409 | provider 成功、registry 失败 |
| `rollback_target_already_active` | 409 | 回滚目标已是活跃版本 |
| `release_not_approved_for_deployment` | 409 | release 未批准 |
| `release_review_not_approved` | 409 | release review 未通过 |
| `release_validation_failed` | 409 | release 验证未通过 |
| `release_not_eligible` | 409 | release 不 eligible |
| `version_already_in_production_flow` | 409 | 同版本已在生产流程中 |
| `feedback_research_already_decided` | 409 | 研究请求重复决策 |

## 33. Database Schema（0009_production_monitoring_feedback）

`down_revision="0008_metric_registry_release"`，`revision="0009_production_monitoring_feedback"`。9 张新表，`0001`–`0008` 未改动：

| 表 | 用途 | 关键约束 |
| --- | --- | --- |
| `production_environment_profiles` | 生产环境验收对象 | 唯一 `environment_id` |
| `production_deployment_plans` | 部署计划（shadow 优先） | — |
| `production_deployments` | 部署 + 状态机 | 列与 `deployment_json` 同写 |
| `production_deployment_reviews` | 部署人工评审 | **`package_hash` 列**、唯一 `deployment_id` |
| `production_rollback_reviews` | 回滚人工评审 | **`provider_job_id` 列** |
| `metric_monitoring_snapshots` | 监控快照 | 关联 deployment |
| `metric_alerts` | 告警 | **`UniqueConstraint("dedup_key")`** |
| `metric_feedback_events` | 反馈事实 | — |
| `feedback_research_requests` | 研究请求 | — |

`UniqueConstraint("dedup_key")` 是告警去重的**数据库级**保证——即便应用层去重逻辑被绕过，重复键也无法插入。`alembic check` 无模型漂移。

## 34. APIs 与 Demo

**28 个端点**，全部在 `/api/v1` 下，全部先解析 `ActorIdentity`：

```text
POST /production-environments
GET  /production-environments
GET  /production-environments/{id}
POST /production-deployments
GET  /production-deployments
GET  /production-deployments/{id}
POST /production-deployments/{id}/preflight
POST /production-deployments/{id}/shadow
POST /production-deployments/{id}/review
GET  /production-deployment-reviews/{id}
POST /production-deployment-reviews/{id}/decision
POST /production-deployments/{id}/deploy
POST /production-deployments/{id}/rollback
GET  /production-rollback-reviews/{id}
POST /production-rollback-reviews/{id}/decision
GET  /production-deployments/{id}/reconcile
POST /monitoring-snapshots
GET  /production-deployments/{id}/monitoring-snapshots
GET  /metric-alerts
GET  /metric-alerts/{id}
POST /metric-alerts/{id}/decision
POST /metric-alerts/{id}/feedback
POST /metric-feedback-events
GET  /metric-feedback-events
GET  /metric-feedback-events/{id}
POST /metric-feedback-events/{id}/research-requests
GET  /feedback-research-requests/{id}
POST /feedback-research-requests/{id}/decision
```

**没有任何生产 SQL 执行 API。**

复现：

```powershell
uv run --frozen python examples/demo_phase7.py
```

使用专用 `.demo/phase7_demo.db`、Mock 执行器、Mock provider 与显式注入的合成身份，产物在 `examples/phase7/`。

**Demo 第一段——环境、preflight 与部署：**

| 步骤 | 结果 |
| --- | --- |
| 注册环境（匿名） | 401 `actor_identity_required` |
| 注册环境（admin） | `prod_synth`，`verified=true` / `verification_source=synthetic_profile` |
| 未验收环境 | `verified=false` → 409 `production_environment_not_verified`；未注册 → 404 |
| 从谱系组装包 | v2.0.0，`deployment_package_hash` 由 release/artifact/environment 全链推出 |
| preflight | `passed`，11 项检查中 `release_target_environment` 与 `runtime_identity` 诚实标 `not_verified`，`read_only_preflight=true`、`production_deployed=false` |
| shadow | `passed`，rows 401 / entities 401 / coverage 1.0，`write_business_output=false`，baseline 来源 `production_shadow` |
| deploy | `status=deployed`，但 `production_deployed=false`、`runtime_identity_verified=false`、`missing_evidence=['real production runtime identity not verified', 'synthetic mock provider; no real production runtime']` |
| reconcile（告警前） | `consistent`，`automatic_correction=false` |

**Demo 第二段——异常监控、反馈与两阶段回滚：**

| 步骤 | 结果 |
| --- | --- |
| 异常快照 | coverage 下降 0.4 → `coverage_drop critical`；分布全挤入首箱 → `population_shift critical`；两条告警 `recommended_action=consider_rollback` 且 `automatic_action_taken=false`；部署进入 `monitoring`，注册表指针未动 |
| 告警去重 | 同一异常第二次 → `created_alert_ids=[]`、`occurrences=2`，告警数仍为 2 |
| 告警处置 | `acknowledge` → `acknowledged`，部署仍 `monitoring`（无任何执行） |
| 反馈 | 由告警生成 `data_quality_degradation`，`record_kind=fact`、`interpretation=null`、`automatic_research_started=false` |
| 研究请求 | 人工批准 → `automatic_reflection=false`，`version_registered` 事件数不变（2，无指标改写） |
| 不一致演练 | 运行时指向旧版本 → reconcile `mismatch`，`automatic_correction=false`，注册表指针与部署状态均未被改动 |
| 回滚角色校验 | `production_deployer` 批准 → 403；`rollback_approver` 批准 → 通过 |
| 回滚结果 | 注册表活跃指针 `2.0.0 → 1.0.0`，版本表 `[1.0.0 active, 2.0.0 retired]`，部署 `rolled_back`；`production_rollback_status=succeeded`、`registry_reconciliation_status=reconciled` |
| 审计 | 事件尾部含 `deployment_reconciled` / `deployment_reconciliation_required` / `monitoring_alert_created` / `rollback_started` / `production_rolled_back`（demo 打印的是该指标事件流的最后 8 条，因此尾部同时出现 Phase 6 注册表侧的 `rollback_approved` / `rolled_back`——两张流程共用 `metric_release_events` 一张表，正是 §31 的设计） |

## 35. Test Results 与 Quality Gates

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 单元 / 集成测试 | `uv run --frozen pytest` | **578 passed, 7 skipped** |
| 覆盖率 | `uv run --frozen pytest --cov=airi` | 见下 |
| Lint | `uv run --frozen ruff check src tests` | **All checks passed!** |
| 格式 | `uv run --frozen ruff format --check src tests` | **156 files already formatted** |
| 迁移升级 | `uv run --frozen alembic upgrade head` | 成功 |
| 迁移头 | `uv run --frozen alembic current` | `0009_production_monitoring_feedback (head)` |
| 迁移漂移 | `uv run --frozen alembic check` | **No new upgrade operations detected.** |

测试增量：

- Phase 6 基线：**539 passed, 5 skipped**；
- Phase 7 新增 `tests/test_production.py` **39 个单元测试**（29 个治理判据 + 10 个包完整性判据，全部通过）；
- Phase 7 新增 `tests/test_production_integration.py` **2 个集成测试**（默认跳过，`production_integration` 标记）；
- 最终：**578 passed, 7 skipped**（+39 passed、+2 skipped）。

7 个 skipped 的构成：

```text
tests/test_mysql_integration.py       1  （显式集成标记选择）
tests/test_spark_integration.py       4  （显式集成标记选择）
tests/test_production_integration.py  2  （显式集成标记选择）
```

Phase 7 测试覆盖的判据（39 个单元测试）：

```text
身份 / RBAC：anonymous 401、角色矩阵、未知 action 编程错误
Adapter：disabled 失败关闭、spark 未配置失败关闭、生产禁 Mock、
         synthetic profile 禁用于生产、factory 返回统一 callable
监控：bins 聚合且 NULL 在末、分布计数对齐校验、runtime PSI 复用 Phase 5 分箱、
     去重键稳定且作用域隔离、severity 升级确定性
治理链：环境注册需 admin、verified 由服务器判定、部署需已验收环境、
      完整受治理生产流、非法迁移拒绝、同版本不重复建部署、
      Mock 部署绝不声称 production_deployed、部署策略版本化且不自动
告警 / 反馈：监控摄取开告警并去重、告警决策绝不回滚、
           反馈是事实非解释、研究请求绝不启动 reflection
回滚 / 对账：两阶段回滚先 provider 后 registry、部分失败命名对账状态、
           对账检测不一致但不修复、读取与 not-found
包完整性（10）：干净包重验为空、sql_hash 篡改、environment_id 篡改、
              metric_version 篡改、artifact 缺失、环境 profile 被改、
              metric_ir_hash 不一致、SQL 非受治理 artifact、sql_hash 不覆盖 SQL、
              artifact 文档不可读、行/版本哈希漂移（4 项重推导）、
              库内 deployment_json 被篡改后读取即 409
```

其中「包完整性」一组直接对应 §11–§12 的承诺：**客户端不能提交内容，且交付物与谱系逐字段可核对**。它此前完全没有测试——判据写在代码里却无人验证；现在每一条篡改都有对应的 409 断言。

覆盖率（`--cov=airi`，总语句 6932）：

| 模块 | 覆盖率 |
| --- | --- |
| TOTAL | 94%（miss 384） |
| `airi/api/production.py` | 95% |
| `airi/production/service.py` | 85% |
| `airi/production/adapters.py` | 83% |
| `airi/production/models.py` | 99% |
| `airi/production/monitoring.py` | 89% |
| `airi/production/packaging.py` | 94% |
| `airi/production/identity.py` | 86% |
| `airi/production/persistence.py` | 100% |

`packaging.py` 在补入包完整性测试前只有 **69%**——也就是说，"逐字段重推导、任何篡改即 409"这条核心承诺的失败分支**一行都没被执行过**。补齐后到 94%，`service.py` 也从 84% 升到 85%。剩余的未覆盖行主要是 `SparkProductionAdapter` 的真实路径与 `DisabledIdentityProvider` / `TrustedHeaderIdentityProvider` 在真实网关下的分支——它们在本环境不可能被执行，这正是 §7 与 §20 的诚实声明。

保留项：2 条第三方弃用警告（`starlette.testclient` 的 `httpx` 提示与 `anyio` 别名）。`pytest` 退出时沙箱的批量删除守卫会拦截 `pytest` 临时目录清理（`.airi_tmp/` 日志末尾可见 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`），影响的是临时目录回收，不影响任何测试结论与退出码。

## 36. Known Limitations 与 Recommended Next Step

**已知限制（均为实质性，不掩饰）：**

1. **真实生产运行时未验收。** 全部 deploy / rollback 结果来自 `MockProductionAdapter`，`production_deployed` 恒为 `false`，`missing_evidence` 恒含真实运行时身份未验证。`SparkProductionAdapter` 的真实路径 SKIPPED / NOT VERIFIED（§20）。
2. **身份由上游信任源授予，AIRI 不实现凭证。** `TrustedHeaderIdentityProvider` 要求调用方在受控网关上正确配置 header 注入；若网关配置错误，本阶段无法察觉。Mock provider 只用于测试/demo，配置不可选。
3. **监控数据来自人工/上游上报的快照。** 没有采集器、没有流式管道；`metric_monitoring_snapshots` 是"谁上报什么就是什么"，快照缺失本身只能产生一条 `snapshot_missing` 类信号。
4. **阈值未被真实数据校准。** `MonitoringPolicy@1.0.0` 的 0.2 / 0.35 PSI 档位与其余阈值是经验常量，没有生产分布支撑。
5. **Reconciliation 只检测不修复。** `mismatch` 只产生 critical 告警；修复必须走人工回滚。这是刻意取舍，但意味着漂移会一直存在直到人介入。
6. **两阶段回滚的部分失败需要人工对账。** `rollback_reconciliation_required` 明确命名了这种状态，但系统不提供自动收敛；运维必须按两侧证据手动收尾。
7. **没有 canary / 蓝绿 / 渐进切换。** `deployment_strategy=shadow`、`cutover_mode=manual` 是刻意的——本阶段只做 `shadow → 人工` 切换。
8. **审计表不是投递机制。** 13 类事件写入 `metric_release_events`，未接入任何外部审计/监控系统，只能在库内查询。
9. **单实例假设。** 部署状态与注册表指针的 CAS 只保证单表一致性；多实例并发下若绕过本服务直接改库，守卫会检测到漂移并 409，但不自动修复。
10. **环境注册以保留作用域记账。** `production_environment` 不是真实的 `metric_definition_id`，跨指标查询环境审计需要额外拼接。

**Recommended Next Step（唯一推荐，本次不实现）：**

```text
# AIRI Phase 8
```

Phase 7 把"注册表活跃版本"接到了一个受治理的生产运行时上，并让告警、反馈、回滚三者彼此不可混淆。但它刻意止步于"接缝"：真实集群、真实身份源、真实监控管道都还是 NOT VERIFIED，回滚的部分失败仍需人工收尾。Phase 8 应回答本阶段回避的问题：如何在真实集群上完成端到端验收、如何把监控从"人工上报快照"推进到"可信采集管道"，以及如何让对账从"只检测"走向"可审计的自动收敛"。在真实 Spark/Hive/MySQL、真实 LLM 与真实身份源的环境验收完成之前，任何 Phase 8 的生产集成动作都不应开始。
