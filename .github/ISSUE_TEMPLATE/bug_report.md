---
name: Bug report
about: Something in AIRI does not work as documented
title: "[bug] "
labels: bug
---

<!--
Before filing: the local demo requires no Spark, no MySQL, and no LLM API key.
Most "it won't start" reports are a missing prerequisite or an occupied port —
see docs/guides/quickstart.md#troubleshooting first.
-->

## What happened

<!-- A clear description of the bug. -->

## What you expected

## Steps to reproduce

1.
2.
3.

## Scenario involved

<!-- If it is scenario-specific, say which. Otherwise delete this section. -->
- [ ] `invoice_risk`
- [ ] `enterprise_relation`
- [ ] Neither / not scenario-specific

## Environment

| | |
| --- | --- |
| OS | <!-- e.g. Windows 11 23H2, Ubuntu 24.04 --> |
| Python | <!-- python --version --> |
| Node | <!-- node --version --> |
| How started | <!-- scripts/start-demo.ps1, manual, Docker --> |
| `AIRI_LLM_MODE` | <!-- demo_mock (default) or a real provider --> |
| `AIRI_EXECUTION_MODE` | <!-- mock (default) / disabled / spark_test --> |

## Logs / evidence

<!--
Backend log: .demo/backend.log
Frontend log: .demo/frontend.log
API errors include an error_code — please include it verbatim.
-->

```text

```

## Checklist

- [ ] I searched existing issues
- [ ] I am **not** reporting on real financial data (this project uses synthetic data only)
- [ ] I removed any secrets, tokens, or internal hostnames from the log above
