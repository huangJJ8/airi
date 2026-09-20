# AIRI — 5-Minute Live Interview Demo

A timed, rehearsal-ready script. Nine steps, 5 minutes, then the honest close.

---

## Before the interview

Rehearse this **three times end to end with a stopwatch**. The failure mode is
not the software — it is spending 2 minutes on the dashboard.

```powershell
.\scripts\start-demo.ps1     # migrate → seed synthetic data → both servers
```

Wait for both services to report healthy, then open <http://localhost:5173>.
Have the terminal running `start-demo.ps1` visible in a second window so the
health output can be shown if asked.

**Keep the `LOCAL DEMO` / `Synthetic Data` / `NOT PRODUCTION VERIFIED` markers
visible.** Do not crop or hide them — they are a deliberate part of the story and
you will point at them in step 9.

### Timing budget

| Step | Page | Time |
| --- | --- | --- |
| 1 | dashboard | 0:20 |
| 2 | development — requirement | 0:40 |
| 3 | development — IR and skills | 0:50 |
| 4 | development — generated SQL | 0:50 |
| 5 | testing | 0:35 |
| 6 | experiments | 0:45 |
| 7 | reflection | 0:30 |
| 8 | registry | 0:20 |
| 9 | enterprise relation (via development) | 0:50 |
| — | close | 0:30 |

If you are running long, **cut step 1 and step 8** — not steps 3, 4 or 9.

---

## Step 1 — Dashboard (`/dashboard`) · 20s

**Show:** open the page, do not click anything yet.

**我要讲什么:** "This is a risk metric R&D platform. The dashboard shows the
metric lifecycle — and the labels tell you which layer owns each step: the LLM
understands intent, Python produces every artifact, a human approves every gate."

**面试官可能问什么**

- *"Is this production?"* → "No. It's a portfolio/research implementation. All the
  data you're about to see is synthetic — see the markers on screen."
- *"Where's the data from?"* → "Generated in-repo. There is no connection to any
  real system in this demo."
- *"Why a web UI at all?"* → "So the workflow is demonstrable. It also forces the
  governance boundary to be explicit, because the UI is not allowed to compute
  business logic."

---

## Step 2 — Development: the requirement (`/development`) · 40s

**Do:** show the requirement textbox, then submit the invoice-risk requirement.

**要讲什么:** the input is **natural language** — a risk analyst's sentence, not
a form. Note what happens next: it is parsed into a **structured object**, not
into SQL directly.

**面试官可能问什么**

- *"What if the model returns garbage?"* → "The run fails loudly. The response
  must satisfy a strict schema; there is no repair step and no fallback to a
  canned answer. A successful run means the model genuinely produced a valid IR."
- *"Can I inject anything here?"* → "You can try — it will fail schema validation.
  Identifiers have to match a strict pattern, so free text has nowhere to go."
- *"Is this an agent?"* → "It's an agentic pipeline with a deterministic control
  path. One LLM step here: requirement understanding."

---

## Step 3 — Development: IR and skills · 50s

**Show:** the Metric IR panel, then the skills panel. Point at the scenario name
and the capability list.

**要讲什么 — this is the core of the demo:**

1. The IR is the **reviewable boundary**. Source, window, filters, aggregation,
   grouping, label definition, evaluation spec — as fields, each validated.
2. **Scenario Skill × Capability Skill:** `invoice_risk` is *what the business
   means*; `metric_sum` / `metric_window` / `metric_count` are *how a mechanism
   is realised*, and each pins a tool version.
3. "The model has now finished its job. Everything after this point is Python."

**面试官可能问什么**

- *"Why not just let it write the SQL?"* → "Because generated SQL has no spec to
  review against, isn't reproducible, and has no clean approval point."
- *"Why a skills layer rather than prompting harder?"* → "Because the mechanism
  should be declared data, not prompt text. You can audit a declaration; you
  can't audit a prompt."
- *"Where does business meaning enter the SQL?"* → "It doesn't. That's a hard
  rule — business semantics stay out of the SQL layer."

---

## Step 4 — Development: the generated SQL · 50s

**Show:** the generated SQL artifact, and the approval state.

**要讲什么:** this SQL was **not written by the model**. It was rendered by a
deterministic Python tool from a fixed Jinja template, filled with IR values that
survived validation — then it passed a **closed-grammar** validator before it
could be approved.

Then point at the **hash** and say: "The human approves a content hash, so the
query that runs is byte-identical to the query that was reviewed."

**面试官可能问什么**

- *"What stops SQL injection?"* → "Three layers. The model never emits SQL;
  identifiers are pattern-constrained in the IR; and the validator is a closed
  grammar with full matching — no `UNION`, nested queries, comments or
  multi-statements — plus DDL/DML verbs refused outright. Execution is read-only."
- *"What if someone edits the SQL after approval?"* → "The hash changes, so the
  approval no longer matches. Approval is over content, not over a filename."
- *"Is the template safe if the IR is valid?"* → "The validator doesn't trust the
  generator. That's deliberate — the architecture assumes its own code could be
  wrong."

---

## Step 5 — Testing (`/testing`) · 35s

**Show:** the test results — scenario-declared checks, pass/fail.

**要讲什么:** because the IR is structured, the platform knows **what to
assert**: schema conformance, null handling, duplicate keys, window boundaries,
distinct semantics, reconciliation. For join scenarios there is also a
**direct-join control**. The scenario declares the checks; Python runs them.

**面试官可能问什么**

- *"Who wrote these tests?"* → "The scenario declares the check types; the
  platform generates and runs them. These are not hand-written per metric."
- *"Are these real tests or a demo of tests?"* → "Real — they execute against the
  fixture data and can fail. If you want, I can show you the failing path."
- *"How does a metric get promoted if a test fails?"* → "It doesn't. Tests are
  part of the evidence, and the gate is separate."

---

## Step 6 — Experiments (`/experiments`) · 45s

**Show:** coverage, bad rate, decile bins, KS (with direction), IV, lift,
threshold candidates.

**要讲什么:** every number here is computed in Python and stored as a **fact**.
Two distinctions worth stating out loud:

- **KS has a direction** — a strongly separating metric pointing the wrong way is
  a finding, not a success.
- The platform **refuses to compare incomparable runs**. Different dataset, label
  definition, evaluation window or snapshot → no comparison. A misleading number
  is worse than no number.

**面试官可能问什么**

- *"Are these numbers meaningful?"* → "About the pipeline, yes. About real risk
  performance, no — this is synthetic data, and the README says so explicitly."
- *"Which statistic would you actually trust?"* → "None in isolation. I'd want
  coverage and bad rate first — a high-IV metric on 2% of records is a headline,
  not a signal."
- *"Why compute in Python and not ask the model?"* → "Because these have to be
  reproducible and auditable. The model never computes a statistic."

---

## Step 7 — Reflection (`/reflection`) · 30s

**Show:** the diagnostics, then the hypotheses, then the proposals section.

**要讲什么:** the model is given the facts and asked for **hypotheses** — stored
separately, in their own type and their own table. Then the key sentence:

> "A reflection never changes anything by itself. It can only produce a bounded
> proposal, and a human decides."

**面试官可能问什么**

- *"So the AI's output is useless?"* → "No — it's advisory, and it's useful
  precisely because it can't act. The dangerous version is one where the model's
  opinion silently becomes the new metric definition."
- *"How do you keep facts and opinions apart?"* → "Different models, different
  tables. It's enforced by storage, not by a convention in the docs."

---

## Step 8 — Registry (`/registry`) · 20s

**Show:** the version list, the active version, the audit events.

**要讲什么:** metrics are **immutable, versioned, auditable assets**. You never
edit a version — you create one. There's an active pointer, an audit-event
stream, and rollback.

**面试官可能问什么**

- *"Why not just keep SQL files in git?"* → "Git gives you history. The registry
  gives you history *plus* which version is active, who approved it, what evidence
  supported it, and a rollback path."
- *"How do you roll back safely?"* → "Point the active pointer at the previous
  immutable version; the previous one is still intact because nothing was ever
  mutated."

---

## Step 9 — Enterprise Relation (`/development`) · 50s · **the closer**

**Do:** switch to the `enterprise_relation` scenario and run it.

**要讲什么:** this is the same workflow — not a fork. The difference is the
scenario: a **two-hop relationship** (enterprise → person → enterprise) with
`COUNT DISTINCT` and a self-loop exclusion.

Then the single most important sentence of the demo:

> "To support this I did **not** write an `enterprise_relation` join tool. I
> promoted the mechanism to a general `metric_join` capability. That's why adding
> this scenario added no new orchestration — only a new scenario file. New
> Scenario, not new Workflow."

**Show the join SQL** so the audience sees the two-hop join and the
`related_enterprise_id != enterprise_id` predicate.

**面试官可能问什么**

- *"How do I know this isn't hardcoded for this demo?"* → "Because it goes
  through the identical pipeline, and the join is a general capability pinned to a
  tool version. The only files that differ between the two scenarios are the two
  scenario declarations."
- *"Why did you pick this as the second scenario?"* → "Because it's structurally
  different — a join with distinct semantics — which is the one that would expose
  an IR that was really only fitted to scenario one."
- *"What would a third scenario cost?"* → "If it needs the same mechanisms, one
  scenario file plus a fixture. If it needs a new mechanism, a new capability plus
  a tool and template — but no change to the parser, workflow, experiments,
  registry or UI."

---

## The close · 30s

Stop clicking. Say this while the enterprise-relation SQL is still on screen:

> "Two things I want to be explicit about. One: this is a portfolio and research
> implementation — all the data is synthetic, and none of these statistics say
> anything about real predictive power. Two: the Spark/Hive and production
> adapters exist and are boundary-tested, but they are **not verified** against a
> real cluster, because there was none available — so they default to inert and
> fail closed.
>
> That's why the slogan is what it is: **LLMs reason. Python verifies. Humans
> govern.** The SQL that ships was never written by the model."

---

## What NOT to demo in 5 minutes

Do **not** open the production/runtime-governance surface (verification,
convergence, monitoring, notifications, packaging, release review). It is a large
part of the backend and it will eat your entire time budget.

If the interviewer asks, one sentence is enough:

> "The backend also carries the metric registry's release path and the runtime
> governance layer — verification, convergence and monitoring. Those are outside
> what I can show in five minutes; today's demo is the metric R&D main chain."

Then offer [architecture-walkthrough.md](architecture-walkthrough.md) as the
follow-up.

---

## Recovery lines

**If the demo fails to start:**

> "The demo runs on synthetic data with a deterministic LLM substitute, so if the
> environment is off we don't lose the point — let me show you the pipeline
> directly." → run `uv run --frozen python examples/quick_demo.py` in the
> terminal. It prints real artifacts from the same chain. This is why you ran it
> before sharing your screen.

**If a page is slow or empty:**

> "The UI is a thin client over the API — every number you see comes from the
> backend. Let me show you the same call in the API docs instead." → open
> <http://localhost:8000/docs>.

**If you are asked something you did not verify:**

> "I didn't verify that — it would need a real cluster, and I don't want to guess
> at the answer." This is always the right answer. It is consistent with the
> entire project.
