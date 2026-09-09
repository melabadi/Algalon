---
description: "Use when changing TypeScript OTel parsing, source delta, phase evidence, benchmark formulas, session processing, or metric publication."
applyTo: "src/**/*.ts,test/**/*.ts"
---
# Worker instructions

- Restrict evidence to spans carrying the selected authoritative `session.id`; never publish the raw ID.
- Discover sessions across every local repository reaching the Collector. Do not require a repository mount or ignore archived sessions for lack of a source baseline.
- Prefer exact direct-turn model/token/credit totals from the read-only VS Code log matched by the session's conversation ID; preserve OTel phase allocation and fall back to OTel usage when unavailable.
- Keep planning, research, coding, validation, and unclassified allocations non-overlapping. Preserve negative savings and observed cost.
- Manual time is phase-specific and mechanistic. Retained source already represents surviving output, so edit-survival signals are audit evidence rather than a second ROI multiplier.
- One AI credit is derived from observed nano-AIU evidence as documented; do not reintroduce allocated seat-cost ROI.
- Publish bounded aggregate labels only. Prompt text, paths, repository identity, commands, and tool payloads never belong in metrics.
- Keep source scans read-only and exclude dependencies, build output, lockfiles where configured, and `.copilot-value` itself.
- Never combine session telemetry with an unmatched repository snapshot. Publish source evidence unavailable and use zero retained characters when no same-session source producer exists.
- Update metric publication, backend/API expectations, UI methodology, and formula documentation together when contracts change.
- Validate with `npm test`; use fixture and privacy smoke commands when changing evidence extraction or Collector contracts.