---
description: "Use when changing README files, deployment instructions, architecture explanations, methodology, formulas, or operational troubleshooting."
applyTo: "**/*.md"
---
# Documentation instructions

- Keep root `README.md` focused on contributors and development, `docs/local-deployment/README.md` focused on end users, and `docs/roi-formula-evolution.md` authoritative for formula history and limits.
- Link across those documents instead of copying long formula or operational sections.
- Keep commands cross-platform where users need them and test every command shape against the current CLI.
- Use current architecture and ports: React/FastAPI `3000`, optional Grafana `3001`, Collector `4317/4318`, VictoriaMetrics `8428`.
- Describe ROI as scenario-sensitive modeled return over observed AI usage evidence, not causal productivity proof.
- Never include local usernames, absolute workstation paths, passwords, raw session IDs, prompt text, or other telemetry examples from real users.