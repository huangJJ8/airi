# 真实环境检查清单 —— Phase 8

Phase 8 只有由站在真实运行时前的运维人员才能*完成*。本仓库中的一切都是**接缝加
不等式守卫**：代码拒绝声称它没有观察到的事实，而这份清单就是人把“未验证
（NOT VERIFIED）”变成“已验证（VERIFIED）”的方式 —— 或者诚实地让它保持未验证。

> 测试套件绝不会为此制造一个绿色结果。标记为 `production_integration`、
> `identity_integration`、`telemetry_integration` 的集成测试，除非下面这套环境已
> 配置，否则**跳过（SKIPPED）**。当清单尚未执行时，跳过就是正确的结果。

---

## 0. 你要验证的不等式

Phase 8 之所以存在，是因为这七对*并不*是同一个断言。下文每一节都以一条能把左边
和右边区分开的观察作结。

| # | 左（廉价、有误导性） | 右（Phase 8 所要求的） |
|---|---|---|
| 1 | 已配置 | 已验证 |
| 2 | 已连接 | 已认证 |
| 3 | 已认证 | 已授权 |
| 4 | 部署返回成功 | 运行时已验证 |
| 5 | 已收到监控数据 | 遥测可信 |
| 6 | 已检出失配 | 已修复失配 |
| 7 | 已执行恢复 | 已验证收敛 |

---

## 1. 运行时访问（Phase 8A）

### 前置条件

- [ ] 一个可达、且你有权探测的 Spark Thrift Server（或兼容实现）。
- [ ] 一个对生产 catalog 只读的服务账号。
- [ ] 从 AIRI 主机到 Thrift 端口的网络通路。
- [ ] 就“用哪个集群标识符字符串标识这个运行时”达成一致（它会成为指纹的一部分；
      它不能是那种别人随手就能改、而真实变化并未发生的人类昵称）。

### 配置

```
AIRI_PRODUCTION_ADAPTER=spark_production
AIRI_SPARK_PRODUCTION_HOST=<thrift-host>
AIRI_SPARK_PRODUCTION_PORT=10000
AIRI_SPARK_PRODUCTION_USERNAME=<service-account>
AIRI_SPARK_PRODUCTION_DATABASE=c_db
AIRI_SPARK_PRODUCTION_READ_ONLY_ATTESTED=true
AIRI_SPARK_PRODUCTION_AUTH_MODE=NOSASL        # LDAP | KERBEROS | GATEWAY
AIRI_SPARK_PRODUCTION_PASSWORD=<secret>      # required when AUTH_MODE=LDAP
AIRI_SPARK_PRODUCTION_SESSION_TIMEZONE=Asia/Shanghai
AIRI_SPARK_PRODUCTION_CATALOG=<catalog-name>
AIRI_SPARK_PRODUCTION_CLUSTER_IDENTIFIER=<stable-cluster-id>
AIRI_SPARK_PRODUCTION_ACTIVATION_LEDGER=     # optional control channel, see §4
```

`AIRI_SPARK_PRODUCTION_READ_ONLY_ATTESTED` 是**人工声明**，不是检测出来的能力。
在没有真正只读授权的情况下把它设为 `true`，是让这份清单说谎最容易的一种方式。
先核实授权。

### 关注点分离

- [ ] `SparkProductionAdapter.configured` 为 true（host 与 username 均已提供）。
- [ ] `adapter.authoritative` 为 true，且 `adapter.runtime_mode == "spark_production"`。
- [ ] 一次探针返回 `reachable=True`。
- [ ] `probe.fingerprint(...)`.fingerprint_hash 是 64 位十六进制字符，且在**两次
      独立探测之间保持稳定** —— 重新探测并比较。两次探测之间发生变化的哈希，说明
      指纹读到了易变的东西。

**不等式 1（已配置 ≠ 已验证）：** 填好的 `.env` 证明不了任何东西。运行探针；只有
带稳定哈希的 `reachable=True` 才算数。

```
AIRI_ENVIRONMENT=production uv run --frozen pytest -m production_integration -q
```

---

## 2. 凭据（不等式 2）

- [ ] `AUTH_MODE=NOSASL` → 会话**仅限连接**。`probe.authenticated` 必须保持
      `False`，`probe.runtime_identity_verified` 也必须保持 `False`。不要通过断言
      username 能证明身份来“修好”它。
- [ ] `AUTH_MODE=LDAP` 并带密码 → `probe.authenticated` 必须为 `True`，**并且**
      必须观察到运行时身份，而不是构造出来的身份。
- [ ] 在服务端（审计日志 / 会话列表）确认来自本主机的会话确实出现。客户端自认为
      连上了，不算证据。

**不等式 2（已连接 ≠ 已认证）：** 开一个 NOSASL 会话，确认适配器报告
`authenticated=False`。如果它报告 `True`，那就是 bug。

---

## 3. 可信身份（Phase 8B）

### 前置条件

- [ ] AIRI 前面有一个 API 网关，会**剥除**来自不受信客户端的任何入站
      `x-airi-actor` / attestation 头。
- [ ] 网关与 AIRI 之间的共享密钥，或一个 JWT 签名密钥。

### 配置（网关声明）

```
AIRI_PRODUCTION_IDENTITY_PROVIDER=trusted_header
AIRI_PRODUCTION_IDENTITY_HEADER=x-airi-actor
AIRI_PRODUCTION_IDENTITY_TRUST_BOUNDARY=trusted_gateway
AIRI_PRODUCTION_IDENTITY_GATEWAY_HEADER=x-airi-gateway-attestation
AIRI_PRODUCTION_IDENTITY_GATEWAY_SECRET=<shared-secret>
```

### 配置（签名 token）

```
AIRI_PRODUCTION_IDENTITY_PROVIDER=trusted_header
AIRI_PRODUCTION_IDENTITY_TRUST_BOUNDARY=signed_jwt
AIRI_PRODUCTION_IDENTITY_JWT_SECRET=<hs256-key>
AIRI_PRODUCTION_IDENTITY_ISSUER=<expected-iss>
```

### 验证

- [ ] 网关就位后，**带** attestation 头的请求被接受，且评审记录
      `reviewer_auth_source`。
- [ ] **绕过**网关（直连 AIRI 端口）并手工带上 `x-airi-actor` 头的请求被拒绝：
      `identity_not_trusted`。*这才是对边界的真正测试。* 如果它成功了，说明边界
      并未部署 —— 要在边缘把该头去掉。
- [ ] 边界已配置且有一次部署在运行时，本地/mock 身份会被拒绝，而不是被静默接受。

**不等式 3（已认证 ≠ 已授权）：** 可信身份仍然必须针对具体动作被授权。确认“可信
但角色不对”的操作者是被正常授权路径拒绝的，而不是被身份路径放行。

```
AIRI_ENVIRONMENT=production uv run --frozen pytest -m identity_integration -q
```

---

## 4. 部署 + 提供方确认（Phase 8C —— 不等式 4）

### 配置

部署控制需要这个可选的控制通道：

```
AIRI_SPARK_PRODUCTION_ACTIVATION_LEDGER=<ledger-table-or-endpoint>
```

没有它，连通性和 shadow 可以验证，而部署控制仍保持未验证（NOT VERIFIED）——
这是诚实的状态，不是失败。

### 验证

- [ ] 只有在 ledger 已配置时，预检运行才会把 `activation_control_channel` 列为
      `passed`。
- [ ] 触发一次部署。响应返回成功**不是**证据。确认存在一行
      `ProductionDeploymentEvidence`，且它来自**第二次独立的状态读取**
      （`provider_confirmed`）。
- [ ] 有意测试反面：请求一次提供方会拒绝的部署，确认没有任何证据行声称成功。
- [ ] 确认证据携带的是提供方自己的 job/deployment id，而不是 AIRI 编出来的标识符。

**不等式 4（部署返回成功 ≠ 运行时已验证）：** 如果第二次状态读取不可用，部署必须
保持未验证，而不是继承第一次响应的乐观结论。

---

## 5. 可信遥测（Phase 8C —— 不等式 5）

### 前置条件

- [ ] 一个拥有自己凭据的监控生产者。
- [ ] 就 `source_event_id` 达成一致 —— 它必须对每次观测唯一，且在重试之间保持
      稳定。

### 注册（这不是验证）

```
POST /api/v1/telemetry-sources
{
  "telemetry_source_id": "prod_monitor",
  "source_system": "prod_monitor",
  "environment_id": "prod_real",
  "auth_identity": "monitor@example.invalid",
  "registered_by": "<operator>"
}
```

- [ ] 创建出来的 source 报告 `verified: false`。仅注册永远不会把生产者标记为可信。
- [ ] 带操作者身份的 `POST /api/v1/telemetry-sources/prod_monitor/verify` 会把
      `verified` 翻转为 `true`，并记录是谁做的。

### 摄取检查

- [ ] 摄取一份快照并确认 `telemetry_trust`：`trusted_source`（已注册 + 已验证 +
      `synthetic=false`）、`synthetic`（已注册但为合成），或 `unverified`。
- [ ] 把同一个 `source_event_id` 发送两次 → 只创建一行快照（幂等），第二次调用
      不会被重复计数。
- [ ] 确认 `observation_time` 与 `received_at` 是**两个不同的时钟**：
      `observation_lag_hours` = received − observed。时钟错误的生产者必须表现为
      `stale`/`late`，而不是静默地表现为 `fresh`。
- [ ] **不要**配置调度器。`MonitoringExpectation` 是一个*声明的期望*，可以报告
      `overdue`；它不轮询，也不告警。

**不等式 5（已收到监控数据 ≠ 遥测可信）：** 确认来自未注册来源的快照被拒绝
（404），而来自已注册但未验证来源的快照以 `telemetry_trust="unverified"` 存储，
且永远不被计为可信。

```
AIRI_ENVIRONMENT=production uv run --frozen pytest -m telemetry_integration -q
```

---

## 6. 验证报告（不等式 1，在部署层面再次出现）

- [ ] `POST /api/v1/production-deployments/{id}/verification` 产出一份报告，其中
      各节分别是 `verified` / `not_verified` —— 绝不是汇总成一个布尔值。
- [ ] 只有当每一个必需小节都已验证时，`real_environment_verified` 才为 `true`。
- [ ] 把底下的运行时换掉（让指纹指向另一个集群），确认报告会标记这次替换，而不是
      直接通过。

---

## 7. 可审计对账（Phase 8D —— 不等式 6）

- [ ] 制造一次真实的失配（运行时活跃版本 ≠ 注册表版本）。
- [ ] `POST /api/v1/reconciliation-plans` 会枚举**可用动作**
      （`registry_to_runtime`、`runtime_to_registry`、`manual_investigation`），并
      声明其中**没有任何一个**为真。核对计划文本不含关于哪一侧正确的断言。
- [ ] 创建一次评审，然后批准它。批准并不执行。
- [ ] 执行并确认结果记录了谁批准、跑了哪个动作。
- [ ] 确认所选动作是计划确实提供过的 —— 从未被提供过的动作会被拒绝，而不是被
      执行。
- [ ] 如果恢复无法执行，计划必须以失配仍然处于 **open** 状态收尾，而不是 closed。

**不等式 6（已检出失配 ≠ 已修复失配）：** 计划从不宣判真相；只有人的决策加上重新
观测才能。

---

## 8. 回滚 + 收敛（Phase 8E —— 不等式 7）

- [ ] 运行回滚预检，确认它明确点名任何缺失的证据（目标、上一版本、控制通道）。
- [ ] 执行回滚。它的返回值**不是**证据。
- [ ] 重新观测运行时，确认 `convergence_status` 是由一次全新读取
      （`ReconciliationResult` / 回滚验证）确认的。
- [ ] 确认只有当回滚后的读取一致时，
      `production_rollback_reviews.runtime_verified` 才为 `true`。
- [ ] 有意把回滚指向一个它无法到达的目标，确认它以 `not_verified` / `failed` 加
      一个失败分类收尾 —— 绝不是制造出来的成功。

**不等式 7（已执行恢复 ≠ 已验证收敛）：** 收敛唯一可接受的证据，是恢复之后取得的
一次新观测。

---

## 9. 策略

```
# Default. A rollback is NOT required for a deployment to count as verified.
AIRI_PRODUCTION_REQUIRE_ROLLBACK_VERIFIED=false
```

- [ ] 保持默认值，除非你的组织明确要求每次部署都必须有已验证的回滚。Phase 8 绝不能
      仅为满足某条策略而*制造*一次回滚 —— 如果策略要求它、而实际并没有发生，报告
      就保持 `not_verified`。

---

## 10. 最终签核

- [ ] 所有集成测试都在 `AIRI_ENVIRONMENT=production` 下运行，且**不跳过**。
- [ ] 上面每一条要么已勾选，要么带着原因被明确记录为未验证。
- [ ] `docs/history/phase8-report.md` 的 §real-environment 小节已按实际观察到的内容
      更新。
- [ ] 本清单中没有任何一条是靠 mock、夹具或假设满足的。

**在你的环境中无法完成的清单，也是一种有效结果。** 记录这个缺口；不要用仿真把它
关掉。
