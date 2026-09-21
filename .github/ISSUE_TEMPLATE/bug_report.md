---
name: Bug 报告
about: AIRI 中有东西与文档描述不符
title: "[bug] "
labels: bug
---

<!--
提交前须知：本地 demo 不需要 Spark、不需要 MySQL，也不需要 LLM API key。
大多数「无法启动」的报告都源于缺少前置条件或端口被占用 ——
请先看 docs/guides/quickstart.md#troubleshooting。
-->

## 发生了什么

<!-- 对缺陷的清晰描述。 -->

## 你期望的结果

## 复现步骤

1.
2.
3.

## 涉及场景

<!-- 如果是场景相关，请说明是哪个。否则删除本节。 -->
- [ ] `invoice_risk`
- [ ] `enterprise_relation`
- [ ] 都不是 / 与场景无关

## 环境

| | |
| --- | --- |
| OS | <!-- 例如 Windows 11 23H2、Ubuntu 24.04 --> |
| Python | <!-- python --version --> |
| Node | <!-- node --version --> |
| 启动方式 | <!-- scripts/start-demo.ps1、手动、Docker --> |
| `AIRI_LLM_MODE` | <!-- demo_mock（默认）或真实 provider --> |
| `AIRI_EXECUTION_MODE` | <!-- mock（默认）/ disabled / spark_test --> |

## 日志 / 证据

<!--
后端日志：.demo/backend.log
前端日志：.demo/frontend.log
API 错误会带一个 error_code —— 请原样附上。
-->

```text

```

## 检查清单

- [ ] 我已搜索过已有 issue
- [ ] 我**不是**在报告真实金融数据（本项目只使用合成数据）
- [ ] 我已从上面的日志中移除任何密钥、token 或内部主机名
