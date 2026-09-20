---
name: Feature request
about: Suggest a scenario, capability, or improvement
title: "[feature] "
labels: enhancement
---

## What problem are you solving

<!-- Describe the need, not the implementation. -->

## Which layer does this belong to?

This is the most useful part of the request — AIRI deliberately separates
knowledge from mechanism, and picking the wrong layer is how the architecture
degrades. Please pick one and say why.

- [ ] **Scenario Skill** — new business semantics / a new domain
- [ ] **Capability Skill** — a new *reusable* execution mechanism
- [ ] **Tool** — a new deterministic realisation of an existing mechanism
- [ ] **Metric IR** — a new structural field (say whether it can be optional)
- [ ] **Evaluation / statistics** — a new computed measure
- [ ] **Web UI** — presentation only (no business logic in the browser)
- [ ] **Docs / tooling**
- [ ] **Not sure** — that's fine, describe it and we'll figure out the layer

## Does an existing capability already cover it?

<!-- e.g. metric_join, metric_count, metric_window, metric_sum, spark_sql_generator -->
<!-- If you want a new capability: would a second, unrelated domain need it? -->

## Proposed behaviour

## Alternatives considered

## Scope check

- [ ] This is not a request to bypass the approval / promotion / release gates
- [ ] This does not require real data to be bundled in the repository
- [ ] This is compatible with "the LLM proposes, Python produces artefacts"
