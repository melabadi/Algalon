---
name: roi-model-change
description: "Change or review the Copilot ROI model, phase attribution, scenario assumptions, source evidence, AI-credit cost, value metrics, or methodology. Use for formula and evidence-contract work."
argument-hint: "Formula, evidence source, or scenario behavior to change"
---
# ROI model change

## Procedure

1. Read [ROI Formula Evolution](../../../docs/roi-formula-evolution.md) and identify whether each input is observed, deterministic, configured, or modeled.
2. Trace the owning TypeScript path through `experiment-evidence.ts`, `phase-evidence.ts`, `source-delta.ts`, `benchmark.ts`, `continuous.ts`, and `metrics.ts` as applicable.
3. State one falsifiable behavioral hypothesis and add or identify a focused test before broad edits.
4. Preserve these guardrails: session isolation, all-local-repository discovery, no unmatched source borrowing, zero coding benefit when source is unavailable, gap-bounded engaged phase time with no value from empty evidence, negative savings, observed AI-credit cost, retained-source non-duplication, ordered scenarios, and latest-session portfolio aggregation.
5. Keep `benchmarkFormulaVersion` and `CURRENT_FORMULA_VERSION` equal. For arithmetic changes, bump both and increment the independent `SESSION_CALCULATION_VERSION` trigger in the same release. Increment only `SESSION_CALCULATION_VERSION` when evidence derivation or migration changes without changing arithmetic. Confirm migration re-derives phase evidence from spans rather than replaying stale artifacts.
6. Update downstream contracts only where necessary: published metrics, SQLite/API shape, React methodology, configuration example, Grafana dashboard, the Pages explainer, and formula documentation.
7. Keep prompt ROI unavailable unless the change supplies prompt-level phase and retained-source attribution. Never allocate session ROI by prompt cost share.
8. Run `npm test`, the relevant fixture/privacy smoke, backend tests if contracts changed, and the React build if methodology or displays changed.
9. Rebuild the bundle and redeploy the local Docker stack, then verify the change through the API and UI before calling it done.

Treat resulting ROI as a sensitivity model, not causal proof, unless the evidence design itself establishes a valid counterfactual.