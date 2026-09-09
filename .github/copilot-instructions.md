# Algalon project instructions

## Sources of truth

- Use [README.md](../README.md) for architecture, development, tests, and release commands.
- Use [docs/local-deployment/README.md](../docs/local-deployment/README.md) for end-user installation and operations.
- Use [docs/roi-formula-evolution.md](../docs/roi-formula-evolution.md) for formula rationale, evidence classes, and claim limits.

## Architecture boundaries

- The TypeScript worker in `src/` owns central OTel session discovery, direct-turn AI usage reconciliation, optional matched source delta, phase allocation, scenario calculation, and `copilot_value_*` publication.
- FastAPI and SQLite in `backend/` form a local drill-down read model. Do not reimplement ROI calculation there.
- React in `web/` reads FastAPI only. It must not query VictoriaMetrics or parse OTel archives directly.
- The Collector is the OTLP and privacy boundary; VictoriaMetrics stores aggregate metrics. Prompt text remains local and must never become a metric label.

## Invariants

- Bind host services to `127.0.0.1`. App: `3000`; optional Grafana: `3001`; Collector: `4317/4318` and `13133`; VictoriaMetrics: `8428`.
- Use authoritative OTel `session.id`; publish only hashed session labels. No manual experiment boundaries and no GitHub token or REST dependency.
- Aggregate every local repository reaching the loopback Collector. Never pair a session with the installation repository's filesystem; unavailable retained source means zero coding benefit.
- Preserve negative savings, split overlapping phase time once, exclude cached input where required, and do not discount retained source twice with edit survival.
- Allocate gap-bounded engaged time, not raw elapsed wall clock. Publish elapsed duration and activity density as separate observed evidence, and produce no modeled value when a session has no eligible active spans.
- Keep `benchmarkFormulaVersion` and `CURRENT_FORMULA_VERSION` equal to the arithmetic generation. For any arithmetic change, bump both and increment the independent `SESSION_CALCULATION_VERSION` re-evaluation trigger in the same release. Increment `SESSION_CALCULATION_VERSION` on its own when evidence derivation or migration changes require retained sessions to be rebuilt. Never aggregate across formula versions, and re-derive phase evidence from retained spans when migrating older artifacts.
- Lead with net value, break-even manual time, and the bounded delivery-cost reduction. Return on AI credit spend is a labeled secondary ratio because its denominator excludes labor and seat cost.
- Aggregate each session's latest value once. Prompt ROI stays unavailable until prompt-level phase and retained-source attribution exist.
- Commit privacy-scrubbed OTLP spans transactionally to the local SQLite inbox before worker processing. Deduplicate by span identity and consume monotonic cursor pages; event time is evidence, never an ingestion filter.
- Import legacy trace archives into the inbox once during upgrade and persist a completion marker. Never restore file rotation, archive polling, or byte cursors as the normal collection path.
- Keep one FastAPI/SQLite writer for the live telemetry volume. Preview apps must use a separate volume.
- Keep pessimistic, base, and optimistic assumptions ordered and explicit.

## Working conventions

- Prefer small changes at the owning boundary and add focused regression coverage.
- Keep installation transactional and cross-platform. Preserve local config, credentials, telemetry, and named volumes during upgrades.
- Do not commit generated `data/`, `dist/`, dependency folders, `docker/.env`, `config/value-model.local.json`, or release output.
- Do not add Aspire, native-runtime, or legacy manual experiment paths back into the product.

## Anti-spiral budget

- Run narrow checks while code is changing. Freeze scope before running the full bundle, smoke, and deployment cycle.
- Produce one release candidate after narrow gates pass. Allow one repair and one retry after a full-cycle failure.
- Before a third full cycle, stop and report the blocker; continue only with explicit user approval.
- Do not expand implementation scope during release validation. Classify failures as direct regression, pre-existing issue, or environment/harness issue, and automatically fix only direct regressions.
- Do not rebuild artifacts after each small fix. When the user flags pace or scope, stop nonessential tooling immediately and provide a concise checkpoint.

## Validation

Run the narrowest relevant check first, then the affected suite. Before release work, run all commands documented under **Validate changes** in [README.md](../README.md), build the artifact, and smoke the exact artifact when Docker is available.