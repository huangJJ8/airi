# Interview & Portfolio Assets

Everything in this folder is **job-application material** — written for a person
presenting AIRI, not for a user running it. The repo's user-facing docs live in
[`../guides/`](../guides/) and [`../architecture/`](../architecture/).

| File | Use it when |
| --- | --- |
| [highlights.md](highlights.md) | You need the 5 things worth remembering, and nothing else |
| [airi-pitch.md](airi-pitch.md) | You have 30 seconds, 3 minutes, or 10 minutes to introduce AIRI |
| [airi-qa.md](airi-qa.md) | You are being asked "why did you do it this way?" |
| [architecture-walkthrough.md](architecture-walkthrough.md) | You are sharing your screen and walking through the code |
| [demo-script.md](demo-script.md) | You are running the live 5-minute demo |
| [../resume-project.md](../resume-project.md) | You are writing the resume entry itself |

## Ground rules for all of it

Be accurate. AIRI's whole architectural argument is that a claim should be
traceable to evidence — a resume that overstates it contradicts the project.

Say this | Not this
--- | ---
AI-assisted | AI-powered
research and development platform | production-ready system
synthetic data, end-to-end demo | real fraud detection
2 differentiated scenarios, 91% backend coverage | "improved efficiency by 80%"
deterministic tool layer, human-approved gates | fully autonomous agent

The three claims that are **not** supported by this repository, and must never
appear in a resume, pitch, or interview answer:

- That AIRI runs on real enterprise data, or has any real predictive power.
- That Spark/Hive/MySQL/IAM integrations are verified (they are code-complete and
  deliberately fail-closed, but unverified).
- That any statistic in `examples/` describes real risk performance.

See the [Limitations](../../README.md#limitations) section of the README for the
canonical wording.
