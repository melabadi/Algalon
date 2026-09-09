---
name: release-validation
description: "Build and validate an Algalon release. Use for release preparation, CI parity, portable .pyz/.zip bundles, checksums, Docker smoke tests, Collector privacy checks, and exact-artifact deployment."
argument-hint: "Version or release artifact to validate"
---
# Release validation

## Procedure

1. Read the validation and release sections in [README.md](../../../README.md) and mirror [CI](../../workflows/ci.yml).
2. Install exact dependencies with `npm ci`, `npm ci --prefix web`, and the backend requirements.
3. Run TypeScript, Python CLI, SQLite, and React build checks. Fix only failures caused by the release changes.
4. Run `python scripts/copilot_value.py bundle` to create the `.pyz`, zip, and SHA-256 files.
5. Inspect the staged contract indirectly through bundle validation: no source tests, dev dependencies, local `.env`, local value config, telemetry, user paths, or obsolete Aspire configuration.
6. Run `python scripts/copilot_value.py smoke-bundle --skip-build` when Docker is available. Require OTel ingestion, worker publication, VictoriaMetrics queryability, SQLite session/prompt indexing, React delivery, and clean restoration. The smoke must replay one identical OTLP payload, then ingest a new span whose event time predates discovery. Require one stable session label, the expanded authoritative start, reconciled elapsed/public duration, cumulative prompts and usage, and available portfolio modeling.
7. Deploy the exact smoke-tested `.pyz` to the test repository, run installed `status`, and verify browser behavior at desktop and mobile sizes for UI changes.
8. Report artifact paths and checksums. Remove temporary containers, probes, screenshots, staging directories, and generated caches that are not release output.

## Retry budget

- Build one release candidate only after all narrow checks pass.
- Allow one repair and one retry after an exact-artifact smoke failure.
- A second failed exact-artifact smoke ends the workflow with a blocker report. Do not broaden scope or build a third candidate without explicit user approval.

Do not commit, tag, publish, or delete historical user volumes unless explicitly requested.