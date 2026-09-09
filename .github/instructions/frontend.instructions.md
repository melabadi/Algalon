---
description: "Use when changing the React UI, session search, routes, responsive layout, scenario controls, or Vite development behavior."
applyTo: "web/src/**,web/vite.config.ts,web/index.html"
---
# Frontend instructions

- Treat FastAPI as the only data source; use `web/src/api.ts` and shared types rather than direct OTel or VictoriaMetrics access.
- Scenario switches change modeled values only. Observed cost, duration, tokens, tools, prompts, and source evidence must remain fixed.
- Overall filters may narrow displayed sessions, but portfolio KPI totals continue to represent the selected date range unless the UI explicitly labels filtered totals.
- Preserve the zoom path: overall, session, prompts, prompt detail, methodology. Keep History API routes directly loadable through FastAPI's SPA fallback.
- Retain automatic recovery for transient API startup failures and a visible manual retry.
- Use lucide icons, established CSS variables, compact operational layouts, contained table scrolling, and responsive checks at desktop and `390x844` mobile sizes.
- Validate with `npm run build --prefix web`; use a browser interaction check for behavior changes.