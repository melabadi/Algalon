# Evolution of the OTel-Based GitHub Copilot ROI Formula

**Status:** 31 August 2026<br>
**Scope:** Central aggregation of local GitHub Copilot sessions measured through OpenTelemetry  
**Current claim:** Modeled return on AI usage cost, not causal business ROI  
**Formula version:** 2

For the present-tense Markdown counterpart to the in-app Methodology page, see [Algalon Methodology and Formula](methodology.md). This document records why the formula changed and which earlier approaches were superseded.

## Executive summary

The project began with a simple question: can local OpenTelemetry show not only how GitHub Copilot is used, but whether the AI usage was economically worthwhile across coding, research, planning, and validation?

The answer evolved through several formulas. Each iteration removed a source of unsupported value:

1. Fixed minutes per observed activity.
2. Task-specific experiment windows.
3. Actual AI-credit usage value from OTel.
4. A single published speedup prior.
5. Separate phase-specific speedup priors.
6. A mechanistic token, artifact, and tool-runtime model.
7. Start-to-finish retained source delta exported through OTel.
8. Removal of the duplicate edit-survival discount and strict experiment-window boundaries.
9. Central all-repository session aggregation with retained source unavailable by default.
10. Gap-bounded engaged time, reasoning tokens removed from the review term, corrected tool classification, a bounded delivery-cost ratio, an explicit capacity band, and a formula version stamp.

Later implementation changes moved live trace collection to a transactional SQLite inbox, strengthened retained-session migration and integrity checks, added named browser-local calibration sets, made the in-app Methodology dependency flow explicit, and added descriptive model-cohort comparisons. None changed the arithmetic, so the current formula remains version 2 rather than inventing an iteration 11 for transport or presentation work.

The latest formula asks one narrow question: **under an explicit scenario, how does the modeled realized value of saved labor compare with the observed cost of AI usage?** The worker persists three scenario branches: pessimistic, base, and optimistic. The UI's fourth **Custom** selector is not a fourth formula or published metric series. It resolves to one branch from a named browser-local calibration set and evaluates the same formula over retained session evidence.

The in-app Methodology view follows the same dependency order: **Measure the work** from observed local evidence, **Sort the work** into overlap-safe phase evidence, **Estimate manual time** with configured assumptions, then **Compare value with cost** through the modeled economic outputs. Calibration and reference material follow those four steps so configured and modeled values are not presented as observations.

For one session, the calculation is:

$$
\begin{aligned}
T_{AI} &= \frac{W_{engaged}}{60}=\sum_p T_{AI,p} \\
T_{manual,s} &= \sum_p T_{manual,p,s} \\
T_{saved,s} &= T_{manual,s}-T_{AI} \\
B_s &= \frac{T_{saved,s}}{60}H\rho \\
V_{net,s} &= B_s-C_{AI} \\
ROI_s &= \frac{V_{net,s}}{C_{AI}}, \qquad C_{AI}>0
\end{aligned}
$$

**Code correspondence:** phase totals, manual totals, savings, benefit, net value, and ROI are computed in [shared/benchmark.ts, lines 260-345](../shared/benchmark.ts#L260-L345); gap-bounded, overlap-safe phase allocation is computed in [src/phase-evidence.ts, lines 47-180](../src/phase-evidence.ts#L47-L180).

The detailed executable correspondence is documented in [Current formula](#current-formula). Historical iterations below link to retained legacy code only when it still computes the displayed equation; removed models are marked explicitly.

In plain language:

1. Measure the session's engaged time: the observed span activity plus any think-time gaps shorter than the configured idle threshold. Allocate it once across planning, research, coding, validation, and unclassified work.
2. Estimate equivalent manual time for each phase from that phase's evidence and the selected pessimistic, base, or optimistic assumptions, or from the saved branch referenced by the browser-only Custom selector.
3. Subtract measured AI-assisted time from modeled manual time. Keep negative results.
4. Convert the modeled saved minutes into realized labor value using the loaded labor rate and capacity-realization factor.
5. Subtract observed AI usage cost to obtain modeled net value.
6. Report net value, break-even manual time, and a bounded delivery-cost reduction first. Divide modeled net value by observed AI usage cost only as a secondary, clearly labeled ratio.

The formula does **not** claim that the modeled manual time actually occurred. It is a sensitivity calculation over observed session evidence. The detailed phase formulas, break-even test, central source-evidence rule, and portfolio aggregation are in [Current formula](#current-formula).

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $ROI_s$ | ratio | Modeled return on AI usage cost under scenario $s$; multiply by 100 for percent |
| $B_s$ | dollars | Modeled realized labor benefit under scenario $s$ |
| $C_{AI}$ | dollars | Locally observed direct-turn usage with OTel fallback: **$\sum nano\_aiu/10^{11}$**, equivalent to **$0.01 per AI credit** |
| $T_{saved,s}$ | minutes | Total modeled time saved under scenario $s$ |
| $T_{manual,p,s}$ | minutes | Modeled equivalent manual time for phase $p$ under scenario $s$ |
| $T_{AI,p}$ | minutes | Gap-bounded engaged time allocated to phase $p$ |
| $T_{manual,s}$ | minutes | Sum of modeled manual time across all phases under scenario $s$ |
| $T_{AI}$ | minutes | Total gap-bounded engaged time across all phases |
| $V_{net,s}$ | dollars | Modeled realized labor benefit minus observed AI usage cost |
| $p$ | phase | Planning, research, coding, validation, or unclassified |
| $s$ | scenario branch | Pessimistic, base, or optimistic assumptions. The UI's Custom selector resolves to one such branch from a saved set. |
| $H$ | dollars/hour | Loaded labor proxy; documented example default: **$92/hour** |
| $\rho$ | ratio | Fraction of saved labor capacity assumed to become economic value; documented default: **0.50** |
| $60$ | minutes/hour | Converts modeled minutes saved to hours |

The strongest inputs are observed task duration, AI-credit usage value, and phase evidence. Retained source is strong only when a source-aware producer can match it to the same session; standard Copilot OTel does not currently provide that match. The weakest input remains the modeled manual counterfactual, $T_{manual,p,s}$. Consequently, the result should be presented as **Modeled AI Usage ROI**, with pessimistic, base, and optimistic presets plus an optional named Custom sensitivity view, and with net value and break-even manual time shown ahead of the ratio.

## Evidence boundary

| Type | Inputs |
|---|---|
| Observed locally | Direct-turn AI credits and model/token totals when available; OTel session timestamps, phase/tool spans, span-activity union, runtime, and edit-survival signals. OTel usage is the fallback when no matching direct turn log exists. |
| Derived from observation | Gap-bounded engaged time, activity density, uncached input tokens, and per-phase allocated time |
| Deterministic local observation exported through OTel | Added/modified/removed source characters and lines only when an explicit source-aware producer matches the same session; unavailable in central Copilot OTel collection |
| Configured assumptions | Loaded labor rate, capacity realization and its sensitivity band, idle-gap threshold, relevant-token fraction, planning/validation reasoning-token weight, review speed, interaction overhead, code-entry rate, manual-entry fraction |
| Modeled | Manual phase time, minutes saved, labor benefit, net value, delivery-cost reduction, ROI |

Central collection mounts no repository source tree. It publishes source completeness `0` with `evidence="otel_only"`, sets retained characters $C=0$, and therefore creates no positive coding benefit. The source-aware evaluator remains fail-closed: if a separate producer is used, only aggregate source-delta measurements enter OTel and the benchmark rejects missing, incomplete, stale, or mismatched evidence.

A session with no eligible active spans produces no allocated phase time and no modeled ROI. Value is never generated from an empty evidence set.

### Evidence acquisition and replay

Live evidence reaches the formula through a transactional local path. The loopback Collector removes disallowed content and sends privacy-scrubbed OTLP trace batches to FastAPI over the internal Docker network. FastAPI decomposes each batch into spans, commits them to the SQLite `otel_records` inbox, and deduplicates replay by authoritative span identity before the worker can consume them. The worker reads monotonically increasing cursor pages and checkpoints the cursor with accumulated session evidence. Event time remains evidence for session bounds; it is never used as an ingestion cutoff, so a late span can still update its original session.

Matching VS Code direct-turn logs remain read-only local evidence used to refine complete credit, model, and token totals. If every matching log is not available, the whole session falls back to its complete OTel aggregate rather than mixing partial direct and OTel usage. Legacy scrubbed trace archives are streamed into the inbox once during upgrade, deduplicated against live spans, and guarded by a durable completion marker. Archive polling, rotation, and byte cursors are not part of normal collection.

This transport changes durability, replay behavior, and provenance checks; it does not promote any evidence class or change the benchmark arithmetic.

### Formula and recalculation versions

Two independent version mechanisms prevent stale modeled results from silently mixing:

- `benchmarkFormulaVersion` is stamped on every benchmark result, and FastAPI's `CURRENT_FORMULA_VERSION` must identify the same arithmetic generation. Only matching results enter modeled portfolio totals.
- `SESSION_CALCULATION_VERSION` is a separate monotonic worker re-evaluation trigger. It does not identify the formula and does not need the same numeric value. Increment it when evidence derivation, migration, or other calculation inputs require retained sessions to be processed again.

An arithmetic change increments both formula identifiers and the session calculation trigger in the same release. A transport, evidence-derivation, or migration-only change may increment only `SESSION_CALCULATION_VERSION`. Migration must reconstruct phase evidence from retained inbox spans and exact session bounds rather than treating stale artifact phase totals as current evidence.

## Notation and units

All time values are in **minutes** unless explicitly marked as seconds. Currency is in **US dollars**.

### Indices and economic outputs

| Symbol | Unit | Meaning |
|---|---:|---|
| $p$ | category | Work phase: planning, research, coding, validation, or unclassified |
| $s$ | scenario branch | Pessimistic, base, or optimistic assumptions; Custom selects one branch from a saved calibration set |
| $T_{AI,p}$ | minutes | Gap-bounded engaged time allocated to phase $p$ from the session evidence |
| $T_{manual,p,s}$ | minutes | Modeled time for a human to perform equivalent phase work under scenario $s$ |
| $T_{saved,p,s}$ | minutes | Modeled phase savings: $T_{manual,p,s}-T_{AI,p}$ |
| $T_{saved,s}$ | minutes | Sum of modeled savings across all phases |
| $H$ | dollars/hour | Loaded labor proxy; currently $92/hour in the example configuration |
| $\rho$ | ratio | Capacity realization for the current per-session benchmark: **0.50 in every scenario**, reported alongside a **0.25 / 0.50 / 0.75** sensitivity band |
| $B_s$ | dollars | Modeled realized labor benefit under scenario $s$ |
| $C_{AI}$ | dollars | OTel-observed AI-credit usage value for the experiment |
| $R_s$ | ratio | Modeled delivery-cost reduction: $\Delta C_{gross,s}/C_{manual,s}$, bounded above by 1 |
| $ROI_s$ | ratio | Net modeled return divided by AI usage cost; multiply by 100 for percent |
| $q$ | ratio | Lower available edit-survival signal; now audit-only and not applied to ROI |

### Session time inputs

| Symbol | Unit | Meaning |
|---|---:|---|
| $W$ | seconds | Elapsed session window, $t_1-t_0$; retained as observed evidence only |
| $G$ | seconds | Configured maximum idle gap treated as think time; documented default **300** |
| $W_{engaged}$ | seconds | Union of clipped span activity with gaps of at most $G$ bridged; the allocation base |
| $A_{active}$ | seconds | Union of span activity with no gap bridging |
| $\delta$ | ratio | Activity density, $A_{active}/W$; an observed-coverage diagnostic |

### Mechanistic manual-time inputs

| Symbol | Unit | Meaning |
|---|---:|---|
| $U_p$ | tokens | Uncached input tokens attributed to phase $p$ |
| $O_p$ | tokens | Output tokens attributed to phase $p$ |
| $Q_p$ | tokens | Reasoning tokens attributed to phase $p$ |
| $\omega_{P/V,s}$ | ratio | Planning or validation reasoning-token weight; current pessimistic/base/optimistic values: **0 / 0 / 0.25**. Research uses uncached input and has no reasoning-token term. |
| $N_p$ | executions | Number of tools attributed to phase $p$ |
| $D_V$ | minutes | Non-overlapping validation-tool runtime: **raw `toolActiveSeconds` / 60** |
| $C$ | characters | Exact source characters added or modified and retained at completion |
| $\alpha_{p,s}$ | ratio | Current pessimistic/base/optimistic values for planning, research, and validation: **0.10 / 0.25 / 0.50** |
| $v_{p,s}$ | tokens/minute | Current pessimistic/base/optimistic values: **600 / 400 / 250** |
| $\tau_{p,s}$ | minutes/execution | Current pessimistic/base/optimistic values: **0.12 / 0.25 / 0.50** |
| $f_s$ | ratio | Current pessimistic/base/optimistic values: **0.25 / 0.50 / 1.00** |
| $c$ | characters/word | Configured character-to-word conversion; documented default: 5 |
| $w_s$ | words/minute | Current pessimistic/base/optimistic values: **60 / 40 / 25** |
| $m_s$ | multiplier | Current pessimistic/base/optimistic values: **1.00 / 1.25 / 1.50** |

### Legacy activity-model inputs

| Symbol | Unit | Meaning |
|---|---:|---|
| $E$ | decisions | Accepted edit decisions |
| $L$ | lines | Agent edit lines of code |
| $K$ | executions | Coding-tool executions |
| $R$ | executions | Research-tool executions |
| $P$ | executions | Planning-tool executions |
| $m_E$ | minutes/decision | Legacy pessimistic/base/optimistic values: **1 / 3 / 6** |
| $m_L$ | minutes/line | Legacy pessimistic/base/optimistic values: **0.10 / 0.25 / 0.50** |
| $m_K$ | minutes/execution | Legacy pessimistic/base/optimistic values: **1 / 3 / 6** |
| $m_R$ | minutes/execution | Legacy pessimistic/base/optimistic values: **1 / 3 / 6** |
| $m_P$ | minutes/execution | Legacy pessimistic/base/optimistic values: **2 / 5 / 10** |
| $A$ | minutes | Measured AI-assisted task duration in the global-speedup model |
| $M$ | minutes | Estimated equivalent manual task duration in the global-speedup model |
| $s$ | ratio | Legacy pessimistic/base/optimistic global reductions: **0.21 / 0.558 / 0.89** |
| $s_p$ | ratio | Legacy phase reductions: planning **0.10/0.30/0.50**, research **0.15/0.40/0.65**, coding **0.21/0.558/0.89**, validation **0.05/0.20/0.40**, unclassified **0.10/0.30/0.50** |

The notation uses $H$ for labor rate and $\tau$ for interaction overhead. This avoids using the same letter for two different quantities.

---

## Iteration 0: Mixed OTel and GitHub REST proposal

### Rationale

The initial design combined local OTel activity with GitHub usage and billing APIs. OTel would supply operational behavior, while REST billing data would supply cost.

The proposed value structure was already recognizable:

$$
\begin{aligned}
Benefit &= Hours_{saved}\times LaborRate\times CapacityRealization \\
ROI &= \frac{Benefit-Cost}{Cost}
\end{aligned}
$$

**Historical code correspondence:** the outer benefit, net-value, and ROI arithmetic survives in the legacy daily path at [src/model.ts, lines 48-60](../src/model.ts#L48-L60), with allocated daily cost at [lines 89-94](../src/model.ts#L89-L94). The proposed GitHub REST cost source was never retained.

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $Hours_{saved}$ | hours | Estimated manual labor avoided through AI assistance |
| $LaborRate$ | dollars/hour | Local configured value: **$120/hour** |
| $CapacityRealization$ | ratio | Legacy pessimistic/base/optimistic values: **0.25 / 0.50 / 0.75** |
| $Benefit$ | dollars | Realized labor value attributed to the estimated saved hours |
| $Cost$ | dollars | Legacy daily allocation from **$39/month seat + $0 variable + $0 enablement**, divided by days in the month |
| $ROI$ | ratio | Net benefit divided by cost; multiply by 100 for percent |

### What it did well

- Separated telemetry storage from ROI calculation.
- Included coding, research, and planning rather than LoC alone.
- Treated assumptions as configurable rather than observed facts.
- Established the local Collector, VictoriaMetrics, Grafana, and Aspire architecture.

### Shortcomings

- Required Enterprise API permissions and a token.
- Mixed local OTel evidence with externally retrieved billing data.
- Did not yet define a credible manual-time counterfactual.
- Risked conflating subscription allocation with the marginal cost of an AI-assisted task.

### Outcome

The design moved to OTel-only evidence and explicit local configuration. Named experiments now use AI-credit usage value rather than seat allocation.

## Iteration 1: Daily OTel activity weights

### Rationale

The first implemented OTel-only formula converted available activity counters into estimated minutes saved. This made the dashboard functional even when accepted-edit and LoC instruments were absent.

A simplified representation is:

$$
\begin{aligned}
T_{coding} &= \max\left(q\max(E m_E,L m_L),K m_K\right) \\
T_{saved} &= T_{coding}+R m_R+P m_P
\end{aligned}
$$

**Historical code correspondence:** [src/model.ts, lines 43-50](../src/model.ts#L43-L50), exercised by [test/model.test.ts, lines 26-38](../test/model.test.ts#L26-L38).

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $T_{coding}$ | minutes | Legacy modeled coding minutes saved |
| $T_{saved}$ | minutes | Legacy total modeled minutes saved |
| $\max(a,b)$ | operation | Selects the larger value, avoiding addition of overlapping coding proxies |
| $q$ | ratio | Observed edit-survival ratio used by this historical formula |
| $E$ | decisions | Accepted edit decisions |
| $m_E$ | minutes/decision | Historical pessimistic/base/optimistic values: **1 / 3 / 6** |
| $L$ | lines | Agent edit lines of code |
| $m_L$ | minutes/line | Historical pessimistic/base/optimistic values: **0.10 / 0.25 / 0.50** |
| $K$ | executions | Coding-tool executions |
| $m_K$ | minutes/execution | Historical pessimistic/base/optimistic values: **1 / 3 / 6** |
| $R$ | executions | Research-tool executions |
| $m_R$ | minutes/execution | Historical pessimistic/base/optimistic values: **1 / 3 / 6** |
| $P$ | executions | Planning-tool executions |
| $m_P$ | minutes/execution | Historical pessimistic/base/optimistic values: **2 / 5 / 10** |

None of these activity weights are used by the current named-experiment formula.

### What it did well

- Used only local OTel activity and explicit configuration.
- Avoided adding accepted edits, LoC, and coding-tool proxies together; the maximum reduced obvious overlap.
- Exposed pessimistic, base, and optimistic assumptions.
- Preserved research and planning as first-class value categories.

### Shortcomings

- A tool execution was assigned a fixed number of saved minutes without evidence that it produced useful work.
- Activity volume was treated as an outcome.
- Daily aggregation mixed unrelated tasks.
- Missing instruments could make a completed application look valueless.
- The denominator was an allocated daily cost, not the observed cost of AI used for the task.

### Example: first cupcake storefront

The first React/Vite cupcake app built and validated successfully, but the strict OTel result had no accepted-edit or LoC evidence. A manual final-LoC fallback was briefly shown, then rejected because it violated the OTel-only objective. Investigation also found that the emitters had been pointed at Aspire instead of the Collector, so the task could not be reconstructed honestly.

**Lesson:** telemetry coverage and task isolation must be solved before discussing ROI.

## Iteration 2: Named experiment windows with fixed tool weights

### Rationale

Start and completion markers isolated one task from the rest of the day. The same activity-weight formula could now be applied to one build.

For the Sugarline rebuild, the base assumptions were:

$$
T_{saved}=(5\times3)+(7\times3)+(8\times5)=76\text{ minutes}
$$

**Historical code correspondence:** the generic fixed-weight arithmetic survives in [src/model.ts, lines 46-50](../src/model.ts#L46-L50). The Sugarline `5/7/8` named-window fixture is not retained as executable code.

**Where:**

| Term | Unit | Definition |
|---|---:|---|
| $T_{saved}$ | minutes | Total minutes saved estimated by the historical activity-weight model |
| $5\times3$ | executions × minutes/execution | Five coding executions valued at three minutes each |
| $7\times3$ | executions × minutes/execution | Seven research executions valued at three minutes each |
| $8\times5$ | executions × minutes/execution | Eight planning executions valued at five minutes each |
| $76$ | minutes | Sum of the three modeled contributions |

### What it did well

- Measured a real task window rather than a daily mixture.
- Made experiment evidence selectable in Grafana.
- Excluded 46 unmapped tool calls rather than assigning them value.
- Exposed the exact activity counts behind the estimate.

### Shortcomings

- The `3/3/5` minute weights were arbitrary.
- The reported cost still used a small allocated daily amount instead of the task's AI credits.
- The resulting **5,941% ROI** was therefore materially overstated.

### Example: Sugarline cupcake rebuild

| Observation | Value |
|---|---:|
| AI-assisted elapsed time | 21m 29s |
| Coding executions | 5 |
| Research executions | 7 |
| Planning executions | 8 |
| Estimated minutes saved | 76.0 |
| Initially reported ROI | 5,941% |

**Lesson:** task isolation improved the evidence, but arbitrary benefit weights and the wrong cost denominator still dominated the result.

## Iteration 3: Actual AI-credit usage value

### Rationale

The scrubbed Copilot chat spans contained `copilot_chat.copilot_usage_nano_aiu`. This provided an authoritative experiment-specific economic usage value without REST billing access.

$$
\begin{aligned}
AI\ credits &= \frac{\sum nano\_aiu}{10^9} \\
C_{AI} &= \frac{\sum nano\_aiu}{10^{11}}
\end{aligned}
$$

**Code correspondence:** OTel fallback conversion is implemented at [src/experiment-evidence.ts, lines 218-231](../src/experiment-evidence.ts#L218-L231), and direct-turn conversion at [lines 384-398](../src/experiment-evidence.ts#L384-L398).

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $nano\_aiu$ | nano-AIU | Atomic GitHub Copilot AI usage value recorded on one OTel chat span |
| $\sum nano\_aiu$ | nano-AIU | Sum across deduplicated experiment chat spans |
| $10^9$ | nano-AIU/credit | Converts nano-AIU to AI credits |
| $AI\ credits$ | credits | Total AI credits consumed by the experiment |
| $C_{AI}$ | dollars | Economic AI usage value for the experiment |
| $10^{11}$ | nano-AIU/dollar | Combined conversion: $10^9$ nano-AIU per credit and 100 credits per dollar |

One AI credit therefore corresponds to $0.01 of usage value.

### What it did well

- Replaced allocated cost with measured task-specific AI usage.
- Deduplicated model usage by span identity.
- Preserved model and token details for audit.
- Corrected the Sugarline cost to **$11.5802**.

Using the old 76-minute numerator with the corrected denominator produced:

$$
ROI=\frac{76-11.58}{11.58}=556.3\%
$$

**Historical code correspondence:** the generic benefit-minus-cost ROI operation survives at [src/model.ts, lines 51-60](../src/model.ts#L51-L60). The hard-coded Sugarline `76/11.58` example is not retained as a fixture.

**Where:**

| Term | Unit | Definition |
|---|---:|---|
| $ROI$ | percent | Historical return using corrected AI cost but the old benefit model |
| $76$ | dollars | Modeled labor benefit; 76 saved minutes equaled $76 because $H=\$120/hour$ and $\rho=0.50$ |
| $11.58$ | dollars | OTel-observed Sugarline AI usage value, rounded from $11.5802$ |
| $556.3\%$ | percent | Net modeled return divided by AI usage value |

This was much lower than 5,941%, but still depended on arbitrary activity weights.

### Shortcomings

- Correcting cost did not repair the value numerator.
- Raw input tokens were mostly repeated cached context.
- Raw output tokens represented discussion, reasoning, and generated text, not retained source alone.
- Token volume multiplied by typing speed would have substantially overcredited output.

### Outcome

Tool calls stopped directly creating benefit. The next model used elapsed time and an external speedup prior.

## Iteration 4: One global speedup prior

### Rationale

A published 55.8% Copilot task-time reduction was used as the base manual counterfactual, with 21% and 89% sensitivity bounds.

For measured AI-assisted time $A$ and assumed speedup $s$:

$$
\begin{aligned}
M &= \frac{A}{1-s} \\
T_{saved} &= M-A
\end{aligned}
$$

**Historical code status:** no executable implementation of the global-speedup prior remains; it was replaced by the phase-evidence benchmark in [shared/benchmark.ts, lines 220-267](../shared/benchmark.ts#L220-L267).

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $A$ | minutes | Measured AI-assisted task duration |
| $s$ | ratio | Historical pessimistic/base/optimistic values: **0.21 / 0.558 / 0.89** |
| $1-s$ | ratio | Remaining fraction of manual time represented by the AI-assisted duration |
| $M$ | minutes | Inferred time for an equivalent manual task |
| $T_{saved}$ | minutes | Difference between inferred manual time and measured AI-assisted time |

For example, $s=0.558$ means the observed AI-assisted time $A$ is assumed to be 44.2% of manual time, so $M=A/0.442$.

The model used measured elapsed time and actual AI-credit usage. Source typing-equivalent was displayed only as a secondary check.

### What it did well

- Removed fixed value per tool call.
- Used exact experiment duration and exact AI usage cost.
- Made the counterfactual assumption mathematically explicit.
- Added break-even manual duration and sensitivity scenarios.

### Shortcomings

- The 55.8% prior was the sole mechanism creating manual time.
- Planning, research, coding, and validation were implicit inside one percentage.
- The prior came from a different task population, not these local tasks.
- A global speedup could not explain why a small visual retheme and a greenfield application should behave differently.

### Examples

| Task | AI time | AI cost | Base manual estimate | Base ROI |
|---|---:|---:|---:|---:|
| Sugarline build | 21.48m | $11.58 | 48.59m | 117.5% |
| Northstar Cinema rewrite | 11.66m | $17.19 | 26.38m | -20.5% |
| Northstar retheme | 2.99m | $3.96 | 6.77m | -4.5% |

For the retheme, the break-even manual time was about 6m 57s. The result was useful as a sensitivity statement, but it did not establish that 6.77 minutes was a credible manual baseline.

## Iteration 5: Phase-specific speedup priors

### Rationale

The task was split into planning, research, coding, validation, and unclassified phases. OTel tool names classified phase evidence, and overlapping intervals were split so phase totals reconciled to wall-clock duration.

$$
\begin{aligned}
T_{AI} &= \sum_pT_{AI,p} \\
T_{manual,p} &= \frac{T_{AI,p}}{1-s_p}
\end{aligned}
$$

**Historical code correspondence:** the overlap-safe $T_{AI}=\sum_pT_{AI,p}$ identity survives at [src/phase-evidence.ts, lines 87-134](../src/phase-evidence.ts#L87-L134). No executable implementation of $T_{manual,p}=T_{AI,p}/(1-s_p)$ remains.

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $p$ | phase | Planning, research, coding, validation, or unclassified |
| $T_{AI}$ | minutes | Total measured AI-assisted experiment duration |
| $T_{AI,p}$ | minutes | Measured AI-assisted duration allocated to phase $p$ |
| $\sum_p$ | operation | Sum across all work phases |
| $s_p$ | ratio | Historical phase-specific time reduction; exact values are listed immediately below |
| $1-s_p$ | ratio | Remaining fraction of manual phase time represented by AI-assisted phase time |
| $T_{manual,p}$ | minutes | Inferred equivalent manual duration for phase $p$ |

$s_p$ was not measured from these applications; changing it directly changed the inferred manual duration for that phase.

The hard-coded phase reductions were:

| Phase $p$ | Pessimistic $s_p$ | Base $s_p$ | Optimistic $s_p$ |
|---|---:|---:|---:|
| Planning | **0.10** | **0.30** | **0.50** |
| Research | **0.15** | **0.40** | **0.65** |
| Coding | **0.21** | **0.558** | **0.89** |
| Validation | **0.05** | **0.20** | **0.40** |
| Unclassified | **0.10** | **0.30** | **0.50** |

**Historical code status:** these $s_p$ constants and the phase-speedup evaluator are no longer retained. Current scenario constants are defined in [config/value-model.example.json, lines 128-161](../config/value-model.example.json#L128-L161) and drive the mechanistic equations instead.

At this stage, edit survival $q$ discounted coding savings.

### What it did well

- Made planning and research direct contributors.
- Prevented overlapping spans from double-counting elapsed time.
- Showed phase allocations and phase savings in Grafana.
- Replaced a single prior with assumptions that could eventually be calibrated per phase.

### Shortcomings

- The phase speedups were still unvalidated assumptions.
- A chat span inherited the phase of the next tool, which is a heuristic.
- Internal planning with no explicit planning tool remained difficult to classify.
- The retheme manual estimate fell to only 4.90 minutes, conflicting with its 25.3-minute source typing-equivalent.

### Results that exposed the problem

| Experiment | Base ROI |
|---|---:|
| Sugarline build | +0.7% |
| Northstar Cinema rewrite | -62.4% |
| Northstar retheme | -56.3% |

For the retheme, measured phase time was approximately 0.16m research, 1.08m coding, and 1.76m validation. The arithmetic was internally correct, but the inferred manual baseline was not persuasive.

**Lesson:** finer-grained priors are not necessarily stronger evidence.

## Iteration 6: Mechanistic token, artifact, and runtime model

### Rationale

Instead of asking how much faster each phase was, the model estimated the human work represented by phase-specific evidence.

Let $U$ be uncached input tokens, $O$ output tokens, $Q$ reasoning tokens, $N$ tool executions, $D$ non-overlapping tool runtime, $C$ source characters, and $c$ characters per word:

$$
\begin{aligned}
M_{planning,s} &= \frac{\alpha_{P,s}(O_P+Q_P)}{v_{P,s}}+N_P\tau_{P,s} \\
M_{research,s} &= \frac{\alpha_{R,s}U_R}{v_{R,s}}+N_R\tau_{R,s} \\
M_{coding,s} &= \frac{f_sC}{cw_s} \\
M_{validation,s} &= D_V+\frac{\alpha_{V,s}(O_V+Q_V)}{v_{V,s}}+N_V\tau_{V,s} \\
M_{unclassified,s} &= m_sT_{AI,unclassified}
\end{aligned}
$$

**Historical code correspondence:** the five phase equations survive algebraically at [shared/benchmark.ts, lines 222-242](../shared/benchmark.ts#L222-L242). This iteration's final-source-size producer for $C$ is not retained; current code supplies matched retained delta or zero instead.

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $M_{planning,s}$ | minutes | Modeled equivalent manual planning time under scenario $s$ |
| $M_{research,s}$ | minutes | Modeled equivalent manual research time under scenario $s$ |
| $M_{coding,s}$ | minutes | Modeled equivalent manual coding time under scenario $s$ |
| $M_{validation,s}$ | minutes | Modeled equivalent manual validation time under scenario $s$ |
| $M_{unclassified,s}$ | minutes | Modeled equivalent manual time for unclassified activity under scenario $s$ |
| $s$ | scenario branch | Pessimistic, base, or optimistic assumptions; Custom resolves to one branch from a saved set |
| $P,R,V$ | phase subscripts | Planning, research, and validation respectively |
| $\alpha_{P,s},\alpha_{R,s},\alpha_{V,s}$ | ratio | Pessimistic/base/optimistic values for every listed phase: **0.10 / 0.25 / 0.50** |
| $O_P,O_V$ | tokens | Output tokens attributed to planning or validation |
| $Q_P,Q_V$ | tokens | Reasoning tokens attributed to planning or validation |
| $U_R$ | tokens | Uncached input tokens attributed to research |
| $v_{P,s},v_{R,s},v_{V,s}$ | tokens/minute | Pessimistic/base/optimistic values for every listed phase: **600 / 400 / 250** |
| $N_P,N_R,N_V$ | executions | Tool executions attributed to planning, research, or validation |
| $\tau_{P,s},\tau_{R,s},\tau_{V,s}$ | minutes/execution | Pessimistic/base/optimistic values for every listed phase: **0.10 / 0.25 / 0.50** |
| $f_s$ | ratio | Pessimistic/base/optimistic manual-entry fractions: **0.25 / 0.50 / 1.00** |
| $C$ | characters | Historical final authored source characters used by this iteration |
| $c$ | characters/word | Configured conversion used by this iteration: **5 characters/word** in all scenarios |
| $w_s$ | words/minute | Pessimistic/base/optimistic code-entry rates: **60 / 40 / 25** |
| $D_V$ | minutes | Non-overlapping observed validation-tool runtime: **raw `toolActiveSeconds` / 60** |
| $m_s$ | multiplier | Pessimistic/base/optimistic values: **1.00 / 1.25 / 1.50** |
| $T_{AI,unclassified}$ | minutes | AI-assisted elapsed time not assigned to another phase |

Each $M_{phase,s}$ is the same concept later written as $T_{manual,phase,s}$.

Base calibration used 25% relevant tokens, 400 tokens/minute, 0.25 minutes per interaction, 50% manual source entry, 40 WPM, five characters per word, and 50% capacity realization.

### What it did well

- Removed fixed speedup percentages.
- Used uncached rather than total research input, avoiding repeated-context inflation.
- Excluded validation input context, which was especially repetitive.
- Used output/reasoning tokens differently by phase.
- Used tool runtime for validation rather than assigning a fixed benefit per test command.
- Made every human-rate assumption visible and configurable.

### Shortcomings

- Coding used **final source size**, crediting unchanged pre-existing code.
- Source output was observed from the filesystem at completion, not retrieved from OTel.
- Retained source could still include human-authored changes.
- The relevant-token fraction remained a calibration assumption.
- The 83-90% implied speedups across very different tasks were suspicious.
- Scenario ranges were extremely wide.

### Examples

| Task | AI time | Manual estimate | AI cost | Base ROI | Scenario range |
|---|---:|---:|---:|---:|---:|
| Sugarline build | 21.48m | 123.80m | $11.58 | 734.2% | 110.1%-2,644.1% |
| Northstar Cinema rewrite | 11.66m | 99.80m | $17.19 | 380.4% | 22.6%-1,497.9% |
| Northstar retheme | 2.99m | 19.81m | $4.36 | 285.4% | -8.2%-1,160.2% |
| Neon Snake | 11.53m | 110.06m | $18.69 | 427.2% | 36.2%-1,630.7% |

These are **historical model outputs**, not current decision-grade results. Their source input was final source size, so they should not be presented as results from the current evidence contract.

## Iteration 7: OTel-backed retained source delta

### Rationale

The coding estimate needed to reward only source created or meaningfully changed during the experiment.

A source baseline is captured before work. Completion computes deterministic differences and emits aggregate measurements through OTLP:

- exact added and removed characters;
- added and removed lines;
- files added, modified, deleted, renamed, and unchanged;
- before/after file and character totals.

Exact renames and at least 50%-similar renamed files are paired so moving code does not look like deleting and recreating the whole file.

The coding formula now uses exact retained changed characters:

$$
M_{coding,s}=\frac{f_sC_{retained\ delta}}{cw_s}
$$

**Code correspondence:** exact retained changes are calculated at [src/source-delta.ts, lines 156-212](../src/source-delta.ts#L156-L212), supplied to the benchmark at [src/continuous.ts, lines 181-202](../src/continuous.ts#L181-L202), and consumed at [shared/benchmark.ts, lines 233-234](../shared/benchmark.ts#L233-L234).

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $M_{coding,s}$ | minutes | Modeled equivalent manual coding time under scenario $s$ |
| $s$ | scenario | Pessimistic, base, or optimistic calibration |
| $f_s$ | ratio | Pessimistic/base/optimistic values: **0.25 / 0.50 / 1.00** |
| $C_{retained\ delta}$ | characters | Exact added/modified characters still present at experiment completion |
| $c$ | characters/word | Configured conversion used by this iteration: **5 characters/word** |
| $w_s$ | words/minute | Pessimistic/base/optimistic values: **60 / 40 / 25** |

### What it did well

- Excluded unchanged code from the coding baseline.
- Kept source content and paths out of telemetry.
- Sent only aggregate evidence through the Collector.
- Waited for VictoriaMetrics persistence, then queried the evidence back.
- Failed closed on missing, incomplete, or stale source series.
- Added deterministic tests for additions, removals, renames, modified renames, and privacy.

### Shortcomings

- Source delta is not a native Copilot OTel instrument; it is a deterministic local observation exported as OTLP.
- Historical experiments had no start baseline and cannot be reconstructed credibly.
- The delta measures task output, not exclusive AI authorship.
- A very small experiment still produces a volatile ROI denominator.

### Example: source-delta smoke experiment

A tiny TypeScript fixture changed one existing character and added one retained line.

| Observation | Value |
|---|---:|
| Before | 28 characters |
| After | 59 characters |
| Added/modified | 32 characters |
| Removed | 1 character |
| Added/removed lines | 2 / 1 |
| AI-assisted elapsed time | 0.3530m |
| AI usage value | $0.157581 |
| Base manual estimate | 0.5816m |
| Base modeled savings | 0.2286m |
| Base ROI | 45.09% |
| Scenario range | -128.18%-431.21% |

The raw OTel source series and the derived benchmark input both reported 32 retained characters.

## Iteration 8: Remove duplicate quality discount and enforce strict windows

### Rationale

The source delta already measures code retained at completion. Multiplying coding savings by edit survival discounted retention twice. It also had an incorrect sign effect: when coding savings were negative, a lower quality factor made the loss less negative and improved ROI.

The final savings formula is therefore:

$$
\begin{aligned}
T_{saved,p,s} &= T_{manual,p,s}-T_{AI,p} \\
T_{saved,s} &= \sum_pT_{saved,p,s}
\end{aligned}
$$

**Code correspondence:** phase savings are computed without a quality multiplier at [shared/benchmark.ts, lines 243-255](../shared/benchmark.ts#L243-L255), and total savings at [lines 257-267](../shared/benchmark.ts#L257-L267). Strict experiment-window filtering is at [src/experiment-evidence.ts, lines 156-162](../src/experiment-evidence.ts#L156-L162).

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $p$ | phase | Planning, research, coding, validation, or unclassified |
| $s$ | scenario | Pessimistic, base, or optimistic calibration |
| $T_{manual,p,s}$ | minutes | Modeled equivalent manual time for phase $p$ under scenario $s$ |
| $T_{AI,p}$ | minutes | Measured AI-assisted elapsed time allocated to phase $p$ |
| $T_{saved,p,s}$ | minutes | Modeled time saved for one phase; negative values are retained |
| $\sum_p$ | operation | Sum across every work phase |
| $T_{saved,s}$ | minutes | Total modeled time saved under scenario $s$ |

Edit survival remains published as audit evidence but no longer changes ROI.

The experiment reader also now counts only spans that both start and finish inside the experiment markers. This excludes the completion command itself and prevents a tool span from contributing more runtime than the experiment duration.

### What it did well

- Removed the duplicate retention discount.
- Fixed the negative-savings sign error.
- Kept quality evidence visible without overstating what it proves.
- Made immediate and later recalculation stable when a completion tool span finishes after the end marker.
- Preserved negative savings instead of clamping them away.

### Shortcomings

- Manual phase time remains modeled rather than observed.
- Idle gaps inside the markers are allocated proportionally, so operators must bracket tasks tightly.
- Tiny AI costs make percentage ROI highly sensitive to seconds of modeled benefit.

### Controlled quality test

A test fixture with a 0.9289 edit-survival ratio moved from 479.99% under the duplicate discount to 526.84% after the correction, a 46.85 percentage-point difference. This was a controlled formula test, not a new real-world task result.

The source-delta smoke experiment remained at 45.09% because its edit-survival ratio was exactly 1.0.

---

## Iteration 9: Central all-repository OTel aggregation

### Rationale

The local Collector already receives Copilot OTel from every repository using the configured VS Code instance. Mounting only the repository that contains the installer created a false attribution risk: a session from repository B could be paired with filesystem changes from repository A.

The central worker now discovers every retained OTel `session.id`, keeps phase allocation and usage scoped to that ID, and reconciles exact direct-turn credits/model/tokens across every local conversation ID carried by the resource session. It uses direct usage only when the complete matching log set is available; otherwise it retains the complete OTel fallback. It does not mount or scan a repository. For each central session:

$$
C=0 \qquad source\_evidence\_complete=0
$$

**Code correspondence:** the central source record is `otel_only` with zero characters at [src/continuous.ts, lines 214-230](../src/continuous.ts#L214-L230), zero retained characters enter the benchmark at [lines 283-305](../src/continuous.ts#L283-L305), and completeness is emitted as `0` at [src/metrics.ts, lines 133-136](../src/metrics.ts#L133-L136).

The `evidence` label is `otel_only`. Planning, research, validation, and unclassified formulas still use their session-scoped evidence. Coding tool calls and tokens remain visible but cannot create coding benefit; measured coding time can still make coding savings negative.

### What it does well

- Aggregates all local repositories through one loopback Collector without registration.
- Prevents telemetry from one repository from borrowing source evidence from another.
- Preserves authoritative `session.id`, exact direct-turn usage, overlap-safe phase time, observed cost, and negative savings.
- Indexes zero-cost OTel sessions with `benchmark: null` rather than hiding them.

### Shortcomings

- Repository identity is intentionally removed, so the dashboard cannot group sessions by repository.
- Standard Copilot OTel does not currently establish retained source; central coding benefit is therefore zero.
- A future source-aware producer must prove a same-session repository match before it can set $C>0$.

---

## Iteration 10: Engaged time, evidence gates, and a bounded ratio

### Rationale

A structured review of iteration 9 found five defects that were each capable of moving the reported number without any change in developer behavior.

**1. Elapsed wall clock capitalized idle.** The allocation base was $t_1-t_0$ over an authoritative `session.id`. A VS Code chat session survives lunch, a meeting, or an overnight, so unattended hours were valued at the loaded rate. Iteration 10 allocates a gap-bounded engaged base instead:

$$
W_{engaged}=\mu\left(\bigcup_k B_k\right),\qquad
B_k=\text{maximal activity block whose internal gaps are}\le G
$$

**Code correspondence:** [gap-bridging block accumulation](../src/phase-evidence.ts#L47-L68) and [the engaged allocation base](../src/phase-evidence.ts#L152-L160).

Elapsed time $W$ and activity density $\delta=A_{active}/W$ are still published as observed evidence, so nothing is hidden; only the base used to distribute phase time changed. This raises modeled savings for idle-heavy sessions because token-derived manual time is unchanged while $T_{AI}$ falls.

**2. Reasoning tokens were charged at a human review rate.** Reasoning tokens are never displayed to the developer, so converting them into human review minutes was indefensible. Planning and validation reasoning tokens now enter their review terms only through explicit weights $\omega_{P,s}$ and $\omega_{V,s}$, which are **0** in the pessimistic and base branches and **0.25** in the optimistic branch as stated upper sensitivity bounds. Research uses uncached input and has no reasoning-token term.

**3. Tool classification missed real editing tools.** Normalized substring matching meant `replace_string_in_file`, `multi_replace_string_in_file`, `create_directory`, `edit_notebook_file`, and `vscode_renameSymbol` matched no coding pattern and fell to unclassified, where they earned $m_s$ times their allocated time instead of being valued by coding evidence. Because central collection sets $C=0$, correcting the patterns *reduces* modeled benefit. This is the intended result: it exposes the retained-source gap instead of hiding it behind the unclassified multiplier.

**4. ROI on AI credit spend is a leverage ratio.** As $C_{AI}\to0$ the ratio diverges, so choosing a cheaper model raises ROI for identical delivered work. Iteration 10 adds a bounded companion and demotes the ratio in every surface:

$$
R_s=\frac{\Delta C_{gross,s}}{C_{manual,s}},\qquad R_s\le 1
$$

**Code correspondence:** [bounded delivery-cost reduction](../shared/benchmark.ts#L325-L345) and [the portfolio aggregate](../backend/app/store.py).

**5. The scenario band excluded the most leveraged parameter.** $H$ and $\rho$ were identical in all three branches, so the pessimistic-to-optimistic range looked like an uncertainty interval while omitting the flat $2\times$ multiplier with the weakest evidence. Each scenario now also reports an explicit capacity band at $\rho\in\{0.25,0.50,0.75\}$.

Two safeguards were added alongside these changes. A session with no eligible active spans now allocates zero time instead of assigning 100% of the window to unclassified, so modeled manual work can no longer be created from an empty evidence set. And every benchmark result carries a `formulaVersion`; the read model refuses to aggregate results from any other version.

### What it does well

- Removes idle capitalization while keeping elapsed time and activity density as published observed evidence.
- Stops charging human review time against tokens no human ever sees.
- Values real editing tools with coding evidence rather than an unclassified multiplier.
- Adds a bounded, model-price-independent economic ratio beside the leverage ratio.
- Makes the capacity assumption's leverage visible instead of hiding it in a constant.
- Prevents silent mixing of two arithmetic generations in one portfolio window.

### Shortcomings

- The idle threshold $G$ is an operating policy, not a measured constant. A 300-second default is a judgment call.
- Engaged time is now `derived` rather than `observed`; the evidence taxonomy moved with it.
- Correcting classification lowers reported value while $C=0$, so the central deployment still cannot value its strongest phase.
- The token-to-minutes conversion remains jointly unidentified in $\alpha$ and $v$, and rework, defect escape, and downstream review remain excluded and one-signed.

---

## Current formula

### The calculation, step by step

The worker benchmark runs independently for the pessimistic, base, and optimistic scenarios. Only the manual-time assumptions change between scenarios; the observed session time, phase evidence, and AI usage cost do not. The browser-only Custom view selects one branch from a saved calibration set and feeds the same observed session evidence through this benchmark. It does not add a fourth worker scenario or metric label.

For each scenario $s$ and phase $p$:

$$
\begin{aligned}
T_{saved,p,s} &= T_{manual,p,s}-T_{AI,p} \\
T_{AI} &= \sum_pT_{AI,p} \\
T_{manual,s} &= \sum_pT_{manual,p,s} \\
T_{saved,s} &= \sum_pT_{saved,p,s}=T_{manual,s}-T_{AI} \\
C_{manual,s} &= \frac{T_{manual,s}}{60}H \\
C_{assisted} &= \frac{T_{AI}}{60}H+C_{AI} \\
\Delta C_{gross,s} &= C_{manual,s}-C_{assisted} \\
R_s &= \frac{\Delta C_{gross,s}}{C_{manual,s}} \\
B_s &= \frac{T_{saved,s}}{60}H\rho \\
V_{net,s} &= B_s-C_{AI} \\
ROI_s &= \frac{V_{net,s}}{C_{AI}}, \qquad C_{AI}>0
\end{aligned}
$$

**Code correspondence:** each equation is mapped to its executing statement in the table below.

**Implementation cross-check (current tree):**

| Documented expression or rule | Owning implementation |
|---|---|
| $W_{engaged}$ gap-bounded activity base | [src/phase-evidence.ts, lines 47-68](../src/phase-evidence.ts#L47-L68) |
| $T_{AI}=\sum_pT_{AI,p}$ and overlap-safe $T_{AI,p}$ allocation | [src/phase-evidence.ts, lines 140-180](../src/phase-evidence.ts#L140-L180) |
| The five $T_{manual,p,s}$ equations | [shared/benchmark.ts, lines 124-135](../shared/benchmark.ts#L124-L135) and [lines 265-287](../shared/benchmark.ts#L265-L287) |
| Phase savings $T_{manual,p,s}-T_{AI,p}$ | [shared/benchmark.ts, lines 288-301](../shared/benchmark.ts#L288-L301) |
| Total manual time, savings, labor costs, benefit, and net value | [shared/benchmark.ts, lines 302-312](../shared/benchmark.ts#L302-L312) |
| Capacity sensitivity band at $\rho\in\{0.25,0.50,0.75\}$ | [shared/benchmark.ts, lines 313-324](../shared/benchmark.ts#L313-L324) |
| $R_s$, $ROI_s=V_{net,s}/C_{AI}$, and the $C_{AI}=0$ guard | [shared/benchmark.ts, lines 325-345](../shared/benchmark.ts#L325-L345) |
| `formulaVersion` stamp | [shared/benchmark.ts, lines 346-357](../shared/benchmark.ts#L346-L357) |
| $C_{AI}=\sum nano\_aiu/10^{11}$ from OTel fallback | [src/experiment-evidence.ts, lines 195-215](../src/experiment-evidence.ts#L195-L215) |
| Evidence-sufficiency gate before any benchmark runs | [src/continuous.ts, lines 107-112](../src/continuous.ts#L107-L112) |
| Central collection's $C=0$ input | [src/continuous.ts, lines 310-320](../src/continuous.ts#L310-L320) |
| Matched source-aware $C=charactersAdded$ input | [src/continuous.ts, lines 219-228](../src/continuous.ts#L219-L228) |

These links identify the statements that execute the equations, rather than nearby type declarations or metric publication code.

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $p$ | phase | Planning, research, coding, validation, or unclassified |
| $s$ | scenario branch | Pessimistic, base, or optimistic assumptions; Custom resolves to one saved-set branch |
| $T_{AI,p}$ | minutes | Measured AI-assisted engaged time allocated to phase $p$ |
| $T_{manual,p,s}$ | minutes | Modeled equivalent manual time for phase $p$ under scenario $s$ |
| $T_{saved,p,s}$ | minutes | Modeled phase savings: $T_{manual,p,s}-T_{AI,p}$ |
| $T_{saved,s}$ | minutes | Total modeled savings across all phases |
| $T_{manual,s}$ | minutes | Total modeled manual-only duration under scenario $s$ |
| $T_{AI}$ | minutes | Total measured AI-assisted engaged time, $W_{engaged}/60$ |
| $\sum_p$ | operation | Sum across every work phase |
| $60$ | minutes/hour | Converts modeled minutes saved to hours |
| $H$ | dollars/hour | Loaded labor proxy; documented example default: **$92/hour** |
| $\rho$ | ratio | Capacity realization; documented default: **0.50**, banded at **0.25 / 0.50 / 0.75** |
| $C_{manual,s}$ | dollars | Modeled manual-only labor cost at the loaded rate |
| $C_{assisted}$ | dollars | Measured AI-assisted time valued at the same loaded rate, plus observed AI usage |
| $\Delta C_{gross,s}$ | dollars | Gross delivery-cost difference before capacity realization |
| $R_s$ | ratio | Modeled delivery-cost reduction; bounded above by 1 and independent of model price |
| $B_s$ | dollars | Modeled realized labor benefit under scenario $s$ |
| $C_{AI}$ | dollars | Locally observed usage from exact VS Code direct turns, with OTel trace fallback: **$\sum nano\_aiu/10^9=$ credits**, then **credits$/100=$ dollars**; equivalently **$\sum nano\_aiu/10^{11}$** |
| $V_{net,s}$ | dollars | Modeled realized net value: $B_s-C_{AI}$ |
| $ROI_s$ | ratio | Net modeled return divided by AI usage value; multiply by 100 for percent |

When $C_{AI}=0$, ROI is reported as unavailable rather than dividing by zero. When $C_{manual,s}=0$, $R_s$ is reported as unavailable for the same reason.

$\Delta C_{gross,s}$, $R_s$, and $V_{net,s}$ answer different questions:

- **Gross delivery savings** compares modeled manual labor cost with measured AI-assisted labor cost plus AI usage. It values every saved labor minute at the full loaded rate.
- **Delivery-cost reduction** expresses that same comparison as a share of the modeled manual baseline. It is bounded above by 1, cannot diverge when AI usage is small, and does not improve merely because a cheaper model was selected.
- **Realized net value** first applies $\rho$, the configured capacity-realization factor, to saved labor and then subtracts AI usage. This is the ROI numerator.

Do not substitute gross delivery savings into the ROI formula. With $\rho<1$, doing so would overstate the value the model assumes can actually be realized.

### How to read the result

Report net value, break-even manual time, and $R_s$ first. $ROI_s$ divides by observed AI credit spend alone, which excludes labor and seat cost, so it rises when the same work is done with a cheaper model. It remains useful for comparing sessions at similar spend and is retained as a clearly labeled secondary figure.

| Displayed ROI | Meaning under the selected scenario |
|---:|---|
| Greater than 0% | Modeled realized labor benefit exceeds observed AI usage cost |
| 0% | Modeled realized labor benefit exactly equals observed AI usage cost |
| Between -100% and 0% | Some positive benefit is modeled, but it does not cover AI usage cost |
| -100% | Modeled realized labor benefit is zero |
| Less than -100% | Modeled time saved is negative, so the session is modeled as taking longer than its manual counterfactual |

For example, 100% ROI means modeled net value equals the AI usage cost; equivalently, modeled realized labor benefit is twice the AI usage cost. It does **not** mean the session was proven to be twice as productive.

### Break-even manual time

The benchmark also reports the modeled manual duration needed for zero ROI:

$$
\begin{aligned}
T_{saved,break\ even} &= \frac{C_{AI}}{(H/60)\rho} \\
T_{manual,break\ even} &= T_{AI}+\frac{C_{AI}}{(H/60)\rho}
\end{aligned}
$$

**Code correspondence:** [per-minute realized value and break-even adjustment](../shared/benchmark.ts#L258-L262), then [manual break-even duration](../shared/benchmark.ts#L338-L342).

At the documented example configuration of $H=92$ dollars/hour and $\rho=0.50$, one saved minute creates about **$0.7667** of modeled realized benefit. A 12-minute session costing **$3** therefore needs a modeled manual baseline of about **15.9 minutes** to break even: 12 measured minutes plus 3.9 additional saved minutes.

This break-even duration is often easier to evaluate than a percentage: ask whether the same work would credibly have taken at least that long without AI. Because it is the most directly checkable output of the model, the dashboard reports it alongside net value rather than behind the ratio.

The same break-even duration is also reported at each capacity-band value, so a reader who rejects $\rho=0.50$ can read the threshold implied by their own assumption.

### Central collection and coding benefit

The latest central worker has no repository source tree and standard Copilot OTel has no retained-source delta. It therefore uses:

$$
C=0 \quad\Longrightarrow\quad T_{manual,coding,s}=0
$$

**Code correspondence:** [the central worker supplies zero retained source](../src/continuous.ts#L310-L320), and [the coding equation consumes that value directly](../shared/benchmark.ts#L276-L278).

If the session contains measured coding time, its coding-phase saving is consequently $-T_{AI,coding}$. Coding tool calls and coding tokens remain visible as evidence but do not create coding benefit. This conservative result is deliberate: one repository's source must never be borrowed for another repository's session.

Because iteration 10 corrected the coding tool patterns, more measured activity now lands in the phase that cannot be valued centrally. Do not read the resulting phase mix as a finding about which kinds of work pay off; it is an artifact of the retained-source gap.

A separate source-aware producer may set $C>0$ only after matching complete retained-source evidence to the same session. The coding formula itself does not change.

### Portfolio aggregation

The local stack is a single-developer workstation boundary. The overall dashboard uses each session's latest value once, but it does not add overlapping session durations. For benchmarked session $i$, let $I_i=[t_{start,i},t_{end,i}]$, let $\mu$ measure elapsed minutes, and let $W_{engaged,i}$ be that session's gap-bounded engaged duration. Portfolio AI-assisted time is the summed engaged time, capped by the union of session windows:

$$
\begin{aligned}
T_{AI,portfolio} &= \min\left(\sum_i W_{engaged,i},\ \mu\left(\bigcup_i I_i\right)\right) \\
T_{manual,portfolio,s} &= \sum_i T_{manual,i,s} \\
T_{saved,portfolio,s} &= T_{manual,portfolio,s}-T_{AI,portfolio} \\
C_{assisted,portfolio} &= \frac{T_{AI,portfolio}}{60}H+\sum_i C_{AI,i} \\
B_{portfolio,s} &= \frac{T_{saved,portfolio,s}}{60}H\rho \\
V_{net,portfolio,s} &= B_{portfolio,s}-\sum_i C_{AI,i} \\
R_{portfolio,s} &= \frac{C_{manual,portfolio,s}-C_{assisted,portfolio}}{C_{manual,portfolio,s}} \\
ROI_{portfolio,s} &= \frac{V_{net,portfolio,s}}{\sum_i C_{AI,i}}
\end{aligned}
$$

**Code correspondence:** FastAPI applies the capped engaged rule and persisted-scenario portfolio totals in [backend/app/store.py](../backend/app/store.py); React performs the same operation for browser-local Custom scenarios in [web/src/features/calibration/model.ts](../web/src/features/calibration/model.ts).

Here $i$ identifies a session. The cap keeps the portfolio concurrency-safe: two sessions spanning 10:00-10:30 and 10:10-10:40 can contribute at most 40 portfolio minutes, not 60. When sessions do not overlap, the total equals the sum of their engaged times, so portfolio arithmetic matches session arithmetic exactly. Every session's AI usage cost is still summed.

Only sessions whose persisted `formulaVersion` matches the read model's current version contribute to modeled portfolio totals. Superseded results are still listed as observed sessions and counted in `supersededFormulaSessions`, but their modeled values are excluded so two arithmetic generations are never summed together.

This correction does not establish active human attention: authoritative session wall clock can still include waiting, background execution, or idle gaps shorter than the configured threshold. It is therefore a concurrency-safe session-time proxy, not observed labor effort. If one collector were shared by multiple developers, interval union would undercount their combined labor and a per-developer identity would be required.

Portfolio ROI is **not** the arithmetic mean of session ROI percentages; averaging percentages would give a one-cent session the same weight as a much more expensive session. Custom portfolio aggregation uses the same union equation and each session's latest API evidence once. Prompt-level ROI remains unavailable because prompt-level phase time and retained-source attribution do not yet exist.

### Model-cohort comparison

The Overall view provides a descriptive slice over the currently displayed sessions. It groups each whole session by the exact set of observed model names that carried at least one request. A one-model session belongs to that model's cohort, a multi-model session belongs to one explicit **Mixed** cohort for that exact model set, and a session with no request-bearing model belongs to **Unknown model**. Sessions are never split and no share of a mixed session's cost or benefit is assigned to an individual model.

Within each cohort, session count, request count, and observed AI cost are summed. Time gained, net value, and return are medians over the cohort's sessions that have a valid result for the selected scenario. The session-search filter also narrows this table. A cohort median is not portfolio ROI, and differences between cohorts are not causal model comparisons: task mix, user behavior, session composition, calibration, and multi-model routing remain confounded.

### Literature triangulation for configured assumptions

The source register distinguishes four evidence outcomes for each exact input:

- **Direct support** measures the same construct or defines the exact convention used by the model.
- **Proxy benchmark** measures an analogous construct in another task or population. It can challenge plausibility but cannot establish the configured value.
- **Context only** bounds interpretation or demonstrates heterogeneity without calibrating the input.
- **Local evidence needed** means no curated source supports that exact input.

A publication is never allowed to lend credibility to an unrelated constant. `benchmark.calibrationSources[].supportLevels` records the mapping separately for every value named in `appliesTo`.

| Configured assumption | Current preset | Curated evidence | Defensible claim |
|---|---:|---|---|
| Overall productivity envelope | No external speedup enters the current arithmetic | Peng et al. reported **55.8% faster** completion for one bounded JavaScript HTTP-server task. Becker et al. reported **19% longer** completion in an RCT with 16 experienced maintainers and 246 mature-project tasks. Cui et al. pooled three workplace RCTs with 4,867 developers and estimated **26.08% more completed tasks** (SE **10.3%**), with variation across experiments. | Context only. Effects can change sign across tasks, populations, repositories, and tools; elapsed time and completed-task throughput are also different outcomes. None sets a current phase constant. |
| Human review rate $v_{p,s}$ | **600 / 400 / 250 tokens/minute** | Brysbaert's meta-analysis of 190 studies and 18,573 participants estimated **238 words/minute** for adult English non-fiction, with most adults between 175 and 300. Wyrich et al. mapped **95** code-comprehension experiments and found diverse tasks, populations, measures, and designs rather than a transferable rate. | Proxy only. Prose words are not model tokens, and ordinary reading is not technical review. The code-comprehension literature does not supply a universal tokens/minute conversion. Timed local phase review is still required. |
| Manual source-entry rate $w_s$ | **60 / 40 / 25 words/minute**; audit rate **52** | Dhakal et al. measured 168,000 online transcription volunteers: mean **51.56 WPM**, SD **20.20**. | Proxy only. The rounded **52 WPM** mean now anchors the secondary typing-equivalent audit metric. Transcribing sentences is not source-code authoring, so the ROI-bearing coding rates remain unchanged. |
| Character-to-word conversion $c$ | **5 characters/word** | Dhakal et al. explicitly calculate WPM with one standardized word equal to **five characters**, following text-entry research convention. | Direct support for the conversion convention, not for coding speed. |
| Loaded labor rate $H$ | **$92/hour** | BLS reports a May 2024 U.S. software-developer median wage of **$133,080/year**. BLS ECEC reports private-industry benefits at **30.1%** of employer compensation in March 2026. The mechanical cross-source benchmark is **$91.53/hour**, rounded to **$92**: $133{,}080/(40\times52)/(1-0.301)$. | Proxy only. The sources use different periods and populations and omit organization-specific role mix, utilization, facilities, and overhead. The example now follows the published benchmark, but local finance remains authoritative. |
| Capacity realization $\rho$ | **0.50** | The 2024 Work Trend Index reports that 90% of surveyed AI users said AI helps them save time. | Context only. Self-reported time savings do not show what fraction becomes deliverable capacity or economic value. Local operating policy or follow-through data must set $\rho$. |
| Relevant-token fraction $\alpha_{p,s}$ | **0.10 / 0.25 / 0.50** | None | Local evidence needed: blind or paired phase review samples. |
| Planning/validation reasoning-token weight $\omega_{P/V,s}$ | **0 / 0 / 0.25** | None | Reasoning tokens are not rendered to the developer, so the pessimistic and base branches exclude them entirely. The optimistic 0.25 is an explicit upper sensitivity bound, not a measurement. Research has no reasoning-token term. |
| Maximum bridged idle gap $G$ | **300 seconds** | None | Local evidence needed. This is an operating policy that separates think time from absence; it is not derived from any published study. |
| Tool interaction overhead $\tau_{p,s}$ | **0.12 / 0.25 / 0.50 minutes/tool** (**7.2 / 15 / 30 seconds**) | Mozannar et al. found that including post-acceptance work raised mean total verification from **3.96 to 7.03 seconds** for initially verified suggestions and from **3.25 to 15.21 seconds** (**6.48 seconds** median) for deferred suggestions; explicit waiting added **2.5 seconds** when it occurred. | Proxy only. The **7.03-second** mean is rounded to the **0.12-minute** lower setting, while **15.21 seconds** aligns with the **0.25-minute** base. Suggestion verification is not one general tool execution, and the **0.50-minute** upper setting remains an explicit sensitivity bound. |
| Manual source-entry fraction $f_s$ | **0.25 / 0.50 / 1.00** | None | Local evidence needed: paired retained-source attribution. |
| Unclassified multiplier $m_s$ | **1.00 / 1.25 / 1.50** | None | Local evidence needed: samples that are independently classified and timed. |

**Research-anchored default substitutions (7 August 2026)**

| Input | Previous example | Revised example | Evidence treatment |
|---|---:|---:|---|
| Loaded labor rate $H$ | $120/hour | **$92/hour** | Rounded BLS wage-plus-benefits proxy; replace with local finance data when available |
| Audit typing rate | 40 WPM | **52 WPM** | Rounded observed transcription mean; audit-only and excluded from ROI |
| Pessimistic tool overhead $\tau_{p,pessimistic}$ | 0.10 min/tool | **0.12 min/tool** | Rounded CHI verification mean; proxy applied to planning, research, and validation |

No numeric substitution was made for capacity realization, token relevance, technical review speed, manual source-entry fraction, coding speed, or the unclassified multiplier because the reviewed literature did not measure the same construct in compatible units.

For planning, research, and validation, the token term is $tokens\times\alpha_{p,s}/v_{p,s}$. Outcome duration alone therefore identifies only the ratio $\alpha/v$, not relevance and review speed separately. A defensible local calibration must label relevance independently and time technical review independently, or report one explicitly combined effective-minutes-per-token parameter. Fitting both constants to the same completion-time observations would create false precision.

Primary and official sources:

1. Peng, Kalliamvakou, Cihon, and Demirer, [*The Impact of AI on Developer Productivity: Evidence from GitHub Copilot*](https://arxiv.org/abs/2302.06590), 2023.
2. Becker, Rush, Barnes, and Rein, [*Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity*](https://arxiv.org/abs/2507.09089), 2025.
3. Brysbaert, [*How Many Words Do We Read per Minute? A Review and Meta-Analysis of Reading Rate*](https://doi.org/10.1016/j.jml.2019.104047), 2019.
4. Dhakal, Feit, Kristensson, and Oulasvirta, [*Observations on Typing from 136 Million Keystrokes*](https://doi.org/10.1145/3173574.3174220), 2018.
5. U.S. Bureau of Labor Statistics, [*Software Developers, Quality Assurance Analysts, and Testers*](https://www.bls.gov/ooh/computer-and-information-technology/software-developers.htm), May 2024 wage data.
6. U.S. Bureau of Labor Statistics, [*Employer Costs for Employee Compensation - March 2026*](https://www.bls.gov/news.release/ecec.nr0.htm), 2026.
7. Microsoft and LinkedIn, [*2024 Work Trend Index Annual Report*](https://www.microsoft.com/en-us/worklab/work-trend-index/2024-ai-at-work-is-here-now-comes-the-hard-part), 2024.
8. Cui, Demirer, Jaffe, Musolff, Peng, and Salz, [*The Effects of Generative AI on High-Skilled Work: Evidence from Three Field Experiments with Software Developers*](https://doi.org/10.1287/mnsc.2025.00535), 2026.
9. Mozannar, Bansal, Fourney, and Horvitz, [*Reading Between the Lines: Modeling User Behavior and Costs in AI-Assisted Programming*](https://doi.org/10.1145/3613904.3641936), 2024.
10. Wyrich, Bogner, and Wagner, [*40 Years of Designing Code Comprehension Experiments: A Systematic Mapping Study*](https://doi.org/10.1145/3626522), 2023.

The configured numbers remain **literature-informed sensitivity presets, not validated constants**. Three example values now use rounded proxy anchors, while the literature directly supports only the standardized five-character word convention. Organization-specific values still require local measurement.

### Current formula enablement

| Configuration key | Installed or default value | Effect |
|---|---:|---|
| `benchmark.acknowledgedAssumptions` | Installer: **true**; schema and example default: **false** | Enables the current per-session mechanistic scenarios and ROI only after explicit acknowledgement |
| `benchmark.calibrationSources` | Ten scoped starter sources in the example | Records evidence class, per-input direct/proxy/context support, finding used, and limitation; it does not change arithmetic |
| `benchmark.capacityRealizationBand` | **[0.25, 0.5, 0.75]** | Adds an explicit capacity sensitivity band to every scenario result |
| `benchmark.maxIdleGapSeconds` | **300** | Idle longer than this is excluded from the engaged allocation base |
| `benchmark.scenarios.*.{planning,validation}.reasoningTokenWeight` | **0 / 0 / 0.25** | Share of planning or validation reasoning tokens entering the review term; the research value is retained for schema compatibility but does not enter the current formula |
| `benchmark.presetScenarios` | Optional preserved snapshot | Keeps the original pessimistic, base, and optimistic values beside exported custom active scenarios |
| Top-level `acknowledgedAssumptions` | **false** by default | Keeps the obsolete daily activity-weight ROI disabled |
| `loadedHourlyRateUsd` | Example: **92** | Rounded BLS wage-plus-benefits proxy; local finance should replace it |
| `benchmark.typingWordsPerMinute` | **52** by default | Rounded Dhakal et al. mean; produces the secondary typing-equivalent audit metric and does not enter current ROI |
| `measurementDelayDays` | **0** by default | Selects the current UTC day for the legacy daily path; per-session paths ignore it |
| `monthlySeatCostUsd` | Example: **39** | Legacy daily allocation only; excluded from per-session ROI |
| `monthlyVariableCostUsd` | Example: **0** | Legacy daily allocation only; excluded from per-session ROI |
| `monthlyEnablementCostUsd` | Example: **0** | Legacy daily allocation only; excluded from per-session ROI |

The per-session formula excludes the monthly seat, variable, and enablement allocation keys. Their example values are **$39**, **$0**, and **$0** respectively, and they remain relevant only to the separate legacy daily path.

## How current AI phase time is calculated

$T_{AI,p}$ is not the sum of raw span durations, and it is no longer the full elapsed window. It is the session's **engaged** time distributed across phases according to overlap-safe OTel activity.

### 1. Define the session window

The latest central path groups spans by authoritative OTel `session.id`. For one session, $t_0$ is the earliest valid span start and $t_1$ is the latest valid span end. As later spans arrive for that same session, the worker recalculates through the new $t_1$ and replaces the session's previous published value; the portfolio keeps only that latest value.

The separate source-aware experiment path instead records $t_0$ immediately before work and $t_1$ immediately after work. In that path, only spans that both start and finish inside the explicit markers are eligible. This excludes work outside the task and the completion command itself.

In both paths, evidence is isolated to one session and clipped to $[t_0,t_1]$. The remaining phase-allocation arithmetic is identical.

### 2. Classify eligible OTel spans

Tool spans are classified by configured name patterns:

| Phase | Current matching examples |
|---|---|
| Planning | `manage_todo_list`, `todo`, `ask_questions`, `questions` |
| Research | `search`, `read`, `fetch`, `grep`, `semantic`, `reference`, `documentation`, `list_dir`, `view_image`, `tool_search`, `usages` |
| Coding | `apply_patch`, `create_file`, `create_directory`, `replace_string_in_file`, `edit_notebook_file`, `rename` |
| Validation | `run_in_terminal`, `get_terminal_output`, `terminal`, `get_errors`, `test`, `playwright`, `screenshot`, `click`, `navigate`, `open_browser`, `run_task`, `task_output` |
| Unclassified | No configured pattern matched |

Tool names are normalized and matched by substring. Normalization lowercases and collapses non-alphanumeric runs to a single underscore, so a pattern must survive that transformation to match: `rename_symbol` does **not** match `vscode_renameSymbol`, whereas `rename` does. Iteration 10 corrected several patterns that silently failed this test. If one name matches multiple phases, the hard-coded precedence is **planning, research, coding, validation**.

Each cost-bearing chat/model span inherits the phase of the first nearby tool it appears to prepare. The tool must start from 0.25 seconds before to 3 seconds after the chat span ends. If no such tool exists, the chat span is unclassified. The chat span's tokens inherit the same phase.

The hard-coded attribution rules are:

| Rule | Hard-coded value |
|---|---|
| Cost-bearing chat-span threshold | `copilot_usage_nano_aiu > 0` |
| Earliest associated tool start | **0.25 seconds (250 ms) before** chat-span end |
| Latest associated tool start | **3.00 seconds (3000 ms) after** chat-span end |
| Multiple tools in the association window | First tool by start time |
| No associated or matching tool | Unclassified phase |
| Cross-phase overlap | Segment duration split equally among distinct active phases |
| Same-phase overlap | Phase counted once for that segment |
| Maximum bridged idle gap $G$ | **300 seconds**, configurable as `benchmark.maxIdleGapSeconds` |
| No eligible active spans | **Zero** allocated time in every phase; no benchmark is produced |

### 3. Split overlapping activity

All eligible chat and tool intervals are divided at every start/end boundary. For each resulting segment $j$, let $d_j$ be its duration and $n_j$ be the number of distinct active phases. A phase receives $d_j/n_j$ seconds from every segment in which it is active. Multiple overlapping spans from the same phase still count that phase only once.

$$
\begin{aligned}
a_p &= \sum_{j:\,p\text{ is active}}\frac{d_j}{n_j} \\
A_{\mathrm{active}} &= \sum_p a_p \\
W &= \frac{t_1-t_0}{1000} \\
W_{engaged} &= \mu\left(\bigcup_k B_k\right) \\
T_{AI,p} &= \frac{W_{engaged}}{60}\frac{a_p}{A_{\mathrm{active}}}
\end{aligned}
$$

**Code correspondence:** [segment boundaries, distinct active phases, and equal overlap splitting](../src/phase-evidence.ts#L128-L151), then [engaged base and allocation](../src/phase-evidence.ts#L152-L180).

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $j$ | segment | One non-overlapping interval between adjacent OTel span boundaries |
| $p$ | phase | Planning, research, coding, validation, or unclassified |
| $d_j$ | seconds | Wall-clock duration of segment $j$ |
| $n_j$ | count | Number of distinct phases active during segment $j$ |
| $a_p$ | seconds | Overlap-safe active time assigned to phase $p$ |
| $A_{\mathrm{active}}$ | seconds | Total overlap-safe active time across phases, $\sum_p a_p$ |
| $t_0,t_1$ | milliseconds since epoch | Session start and completion timestamps |
| $1000$ | milliseconds/second | Converts the timestamp difference to seconds |
| $W$ | seconds | Full session wall-clock duration; published as observed evidence |
| $B_k$ | interval | Maximal activity block whose internal gaps are at most $G$ |
| $\mu$ | measure | Total elapsed length of the union of those blocks |
| $W_{engaged}$ | seconds | Gap-bounded engaged duration used as the allocation base |
| $60$ | seconds/minute | Converts allocated seconds to minutes |
| $T_{AI,p}$ | minutes | Engaged time allocated to phase $p$ |

For example, if coding and validation overlap for a four-second segment, then $d_j=4$ and $n_j=2$. Coding receives two seconds and validation receives two seconds.

The factor $a_p/A_{\mathrm{active}}$ is phase $p$'s share of observed active activity. Multiplying that share by $W_{engaged}$ allocates think-time gaps proportionally while discarding idle longer than $G$. Therefore:

$$
\sum_p T_{AI,p}=\frac{W_{engaged}}{60}\le\frac{W}{60}
$$

**Code correspondence:** every allocated phase uses the same engaged numerator and normalized active share at [src/phase-evidence.ts, lines 152-180](../src/phase-evidence.ts#L152-L180); reconciliation is asserted at [test/experiment-evidence.test.ts, lines 195-230](../test/experiment-evidence.test.ts#L195-L230).

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $p$ | phase | One of the five work phases |
| $T_{AI,p}$ | minutes | Engaged time allocated to phase $p$ |
| $\sum_p$ | operation | Sum across all phases |
| $W_{engaged}$ | seconds | Gap-bounded engaged duration |
| $W$ | seconds | Full session wall-clock duration |
| $60$ | seconds/minute | Converts seconds to minutes |

If there are no eligible active spans ($A_{\mathrm{active}}=0$), every phase receives zero and no ROI is modeled for that session. Earlier iterations assigned the whole window to unclassified, which allowed modeled manual work to be created without any supporting evidence.

The activity density $\delta=A_{\mathrm{active}}/W$ is published so a reader can see how much of the session window was directly observed rather than inferred.

## How current manual phase time is calculated

$T_{manual,p,s}$ is not measured elapsed time. It is the modeled human counterfactual for phase $p$ under scenario $s$. The implementation calculates all five phases as follows:

$$
\begin{aligned}
T_{manual,planning,s} &= \frac{\alpha_{P,s}(O_P+\omega_{P,s}Q_P)}{v_{P,s}}+N_P\tau_{P,s} \\
T_{manual,research,s} &= \frac{\alpha_{R,s}U_R}{v_{R,s}}+N_R\tau_{R,s} \\
T_{manual,coding,s} &= \frac{f_sC}{cw_s} \\
T_{manual,validation,s} &= D_V+\frac{\alpha_{V,s}(O_V+\omega_{V,s}Q_V)}{v_{V,s}}+N_V\tau_{V,s} \\
T_{manual,unclassified,s} &= m_sT_{AI,unclassified}
\end{aligned}
$$

**Code correspondence:** [the shared token-term helper](../shared/benchmark.ts#L124-L135) and [all five phase expressions](../shared/benchmark.ts#L265-L287).

**Where:**

| Symbol | Unit | Definition |
|---|---:|---|
| $T_{manual,phase,s}$ | minutes | Modeled equivalent manual time for the named phase under scenario $s$ |
| $s$ | scenario | Pessimistic, base, or optimistic calibration |
| $P,R,V$ | phase subscripts | Planning, research, and validation respectively |
| $\alpha_{P,s},\alpha_{R,s},\alpha_{V,s}$ | ratio | Pessimistic/base/optimistic values for every listed phase: **0.10 / 0.25 / 0.50** |
| $O_P,O_V$ | tokens | Output tokens attributed to planning or validation chat spans |
| $Q_P,Q_V$ | tokens | Reasoning tokens attributed to planning or validation chat spans |
| $\omega_{P,s},\omega_{V,s}$ | ratio | Reasoning-token weight; pessimistic/base/optimistic values: **0 / 0 / 0.25** |
| $U_R$ | tokens | Uncached input tokens attributed to research chat spans |
| $v_{P,s},v_{R,s},v_{V,s}$ | tokens/minute | Pessimistic/base/optimistic values for every listed phase: **600 / 400 / 250** |
| $N_P,N_R,N_V$ | executions | Tool executions classified as planning, research, or validation |
| $\tau_{P,s},\tau_{R,s},\tau_{V,s}$ | minutes/execution | Pessimistic/base/optimistic values for every listed phase: **0.12 / 0.25 / 0.50** |
| $f_s$ | ratio | Pessimistic/base/optimistic values: **0.25 / 0.50 / 1.00** |
| $C$ | characters | Exact source characters added/modified and retained at completion |
| $c$ | characters/word | Configured conversion; documented default: **5 characters/word** |
| $w_s$ | words/minute | Pessimistic/base/optimistic values: **60 / 40 / 25** |
| $D_V$ | minutes | **Raw `toolActiveSeconds` / 60** after overlapping validation tools are merged into one continuous interval |
| $m_s$ | multiplier | Pessimistic/base/optimistic values: **1.00 / 1.25 / 1.50** |
| $T_{AI,unclassified}$ | minutes | Wall-clock time allocated to the unclassified phase |

The evidence used differs deliberately by phase:

- **Planning:** output tokens plus planning-tool interaction overhead. Reasoning tokens are excluded unless a scenario sets $\omega>0$, because they are never displayed to the developer.
- **Research:** uncached input tokens plus research-tool interaction overhead. Cached input is excluded as repeated context.
- **Coding:** retained source delta only. Coding tokens and coding-tool counts do not create manual-time credit.
- **Validation:** actual validation-tool runtime plus output review and interaction overhead, with the same reasoning-token rule. Validation input is excluded as repeated context.
- **Unclassified:** a configured multiple of measured unclassified AI time because no stronger phase evidence exists. Because that measured time is now gap-bounded, long idle can no longer inflate it.

The secondary typing-equivalent metric is audit-only and does not enter manual time, benefit, net value, or ROI:

$$
T_{typing}=\frac{C}{c w_{audit}}
$$

**Code correspondence:** [shared/benchmark.ts, line 354](../shared/benchmark.ts#L354), asserted at [test/benchmark.test.ts, line 79](../test/benchmark.test.ts#L79). Here $w_{audit}$ is `benchmark.typingWordsPerMinute`, whose documented default is 52 words/minute.

### Current scenario assumption defaults

| Assumption | Pessimistic | Base | Optimistic |
|---|---:|---:|---:|
| Relevant token fraction $\alpha$ | 0.10 | 0.25 | 0.50 |
| Planning/validation reasoning token weight $\omega$ | 0 | 0 | 0.25 |
| Human review rate $v$ | 600 tokens/min | 400 tokens/min | 250 tokens/min |
| Tool overhead $\tau$ | 0.12 min/tool | 0.25 min/tool | 0.50 min/tool |
| Manual source-entry fraction $f$ | 0.25 | 0.50 | 1.00 |
| Code-entry rate $w$ | 60 words/min | 40 words/min | 25 words/min |
| Unclassified multiplier $m$ | 1.00 | 1.25 | 1.50 |
| Loaded labor rate $H$ | $92/hour | $92/hour | $92/hour |
| Capacity realization $\rho$ | 0.50 | 0.50 | 0.50 |
| Characters per word $c$ | 5 | 5 | 5 |
| Maximum bridged idle gap $G$ | 300 s | 300 s | 300 s |

**Code correspondence:** example scenario values are defined in [config/value-model.example.json, lines 152-176](../config/value-model.example.json#L152-L176), schema defaults mirror them at [src/schema.ts, lines 85-121](../src/schema.ts#L85-L121), and parity is checked at [test/schema.test.ts, lines 7-38](../test/schema.test.ts#L7-L38).

$H$, $\rho$, and $G$ are identical across the three branches by design: they are operating and finance policy, not counterfactual uncertainty. The pessimistic-to-optimistic range is therefore **not** a confidence interval over the whole model. To make the leverage of $\rho$ visible, every scenario additionally reports benefit, net value, ROI, and break-even manual time at $\rho\in\{0.25,0.50,0.75\}$.

These values are configurable sensitivity defaults, not OTel observations. An installed configuration may override them; every scenario result must be read together with the assumptions that produced it.

### Calibrating assumptions and recording sources

The Methodology calibration workspace edits a custom draft of the same benchmark values consumed by the TypeScript worker. Named sets are saved in browser-local storage and do not overwrite the current worker-published pessimistic, base, and optimistic results. Each set records which calibrated branch the fourth **Custom** scenario uses. It validates numeric ranges and preserves the direction of the set's scenario envelope: relevant fractions, interaction overhead, manual-entry fractions, and unclassified multipliers increase from pessimistic to optimistic, while human review and code-entry rates decrease.

When **Custom** is selected, the browser feeds FastAPI-provided session evidence and the Methodology-chosen saved branch into the same pure TypeScript benchmark module used by the worker, then aggregates each session's latest value once. The other three scenario buttons continue to use persisted worker results. Custom changes displayed modeled time, benefit, net value, and ROI only; observed evidence and persisted worker artifacts remain fixed. Applying an exported configuration and restarting deliberately replaces the worker's active `scenarios`; `presetScenarios` retains the prior branch snapshot for reference.

Custom therefore does not require an additional formula column in this document. Its result is the existing $ROI_s$ calculation with $s$ resolved to the branch named by **Custom uses** in the saved set.

Each configuration-owner-maintained `benchmark.calibrationSources` entry records a title, publisher, publication date, HTTPS URL, evidence class, applicable assumption, finding used, and limitation. Dashboard users cannot add or remove citations. The base-input evidence table requires an exact applicability match and marks external aggregate or survey evidence as context only, so one citation cannot be treated as proof for every constant.

| Starter source | Defensible use | Claim limit |
|---|---|---|
| [Peng et al., *The Impact of AI on Developer Productivity: Evidence from GitHub Copilot*](https://arxiv.org/abs/2302.06590) | A controlled experiment reported 55.8% faster completion for participants using Copilot on one JavaScript HTTP-server task; use it as an external check on the scenario envelope. | It does not identify token relevance, review speed, tool overhead, capacity realization, or the effect for this installation's task mix. |
| [2024 Work Trend Index Annual Report](https://www.microsoft.com/en-us/worklab/work-trend-index/2024-ai-at-work-is-here-now-comes-the-hard-part) | The global survey reports that 90% of AI users said AI helps them save time; use it as adoption and perceived-value context. | It is self-reported, spans roles and task types, and does not establish a causal developer-productivity effect or numeric calibration constant. |

Direct parameter calibration requires local paired comparable tasks. Record with-AI and without-AI active time by phase, retain task-category boundaries, and report sample size, medians, and dispersion. External studies can inform priors and sensitivity bounds; they should not replace the local counterfactual.

### Current formulas with documented defaults substituted inline

Using the phase evidence symbols defined above, the implementation evaluates these expressions when the documented defaults are active:

| Phase | Pessimistic | Base | Optimistic |
|---|---|---|---|
| Planning | $0.10O_P/600+0.12N_P$ | $0.25O_P/400+0.25N_P$ | $0.50(O_P+0.25Q_P)/250+0.50N_P$ |
| Research | $0.10U_R/600+0.12N_R$ | $0.25U_R/400+0.25N_R$ | $0.50U_R/250+0.50N_R$ |
| Coding | $0.25C/(5\times60)$ | $0.50C/(5\times40)$ | $1.00C/(5\times25)$ |
| Validation | $D_V+0.10O_V/600+0.12N_V$ | $D_V+0.25O_V/400+0.25N_V$ | $D_V+0.50(O_V+0.25Q_V)/250+0.50N_V$ |
| Unclassified | $1.00T_{AI,unclassified}$ | $1.25T_{AI,unclassified}$ | $1.50T_{AI,unclassified}$ |

**Code correspondence:** the constants come from [config/value-model.example.json, lines 152-176](../config/value-model.example.json#L152-L176) and are substituted by [shared/benchmark.ts, lines 263-287](../shared/benchmark.ts#L263-L287).

With the documented labor-rate and capacity defaults, the economic conversion is identical in all three scenarios:

$$
\begin{aligned}
B_s &= \frac{T_{saved,s}}{60}(92)(0.50)=\frac{23}{30}T_{saved,s}\text{ dollars} \\
C_{AI} &= \frac{\sum nano\_aiu}{10^9\times100}=\frac{\sum nano\_aiu}{10^{11}}\text{ dollars} \\
R_s &= \frac{\Delta C_{gross,s}}{C_{manual,s}} \\
ROI_s &= \frac{B_s-C_{AI}}{C_{AI}}
\end{aligned}
$$

**Code correspondence:** scenario benefit, delivery-cost reduction, net value, and ROI are computed at [shared/benchmark.ts, lines 310-345](../shared/benchmark.ts#L310-L345); nano-AIU conversion is implemented at [src/experiment-evidence.ts, lines 195-215](../src/experiment-evidence.ts#L195-L215).

**Where:**

| Value or symbol | Unit | Definition |
|---|---:|---|
| $T_{saved,s}$ | minutes | Modeled minutes saved under scenario $s$ |
| $60$ | minutes/hour | Converts saved minutes to hours |
| $92$ | dollars/hour | Current example loaded labor proxy $H$ |
| $0.50$ | ratio | Current capacity realization $\rho$ |
| $B_s$ | dollars | Modeled realized labor benefit; with current example constants, one saved minute equals about $0.7667 |
| $nano\_aiu$ | nano-AIU | Atomic OTel AI usage value |
| $10^9$ | nano-AIU/credit | Converts nano-AIU to AI credits |
| $100$ | credits/dollar | Converts credits to dollars because one credit is $0.01 |
| $10^{11}$ | nano-AIU/dollar | Combined AI usage conversion |
| $C_{AI}$ | dollars | OTel-observed AI usage value |
| $R_s$ | ratio | Modeled delivery-cost reduction, bounded above by 1 |
| $ROI_s$ | ratio | Net modeled return divided by AI usage value |

### Current hard-coded source-delta rules

Implementation: [included and excluded paths](../src/source-delta.ts#L30-L32), [rename matching](../src/source-delta.ts#L119-L154), and [exact character/line accounting](../src/source-delta.ts#L156-L212).

When an explicit source-aware producer is used, these rules determine retained source $C$ before it enters the coding formula:

| Rule | Hard-coded value |
|---|---|
| Included source extensions | `.css`, `.html`, `.js`, `.jsx`, `.json`, `.svg`, `.ts`, `.tsx` |
| Excluded directories | `.copilot-value`, `.git`, `coverage`, `dist`, `node_modules` |
| Excluded files | `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock` |
| Rename candidate requirement | Same file extension |
| Modified-rename similarity threshold | **0.50** (50%) or greater |
| Character accounting | Exact character diff |
| Line accounting | Line diff; audit only, not used by ROI |

For a source-aware session, $C$ is the emitted `charactersAdded` value: characters added or modified during the session and present in the post-session snapshot. Removed characters are reported separately and are **not subtracted from or added to $C$**. For the central worker, $C=0$ because source evidence is unavailable.

`.json` is included by the current source-delta implementation. The discarded legacy final-source collector excluded `.json`; this is another reason historical final-source results are not directly comparable with current retained-delta results.

## Worked current-formula example

The source-delta smoke experiment lasted 21.178 seconds. Its overlap-safe active evidence was 2.961 seconds coding, 9.205 seconds validation, and 2.165 seconds unclassified, for $A_{\mathrm{active}}=14.331$ active seconds.

Using the wall-clock allocation formula:

$$
\begin{aligned}
T_{AI,coding} &= \frac{21.178}{60}\frac{2.961}{14.331}=0.072928\text{ min} \\
T_{AI,validation} &= \frac{21.178}{60}\frac{9.205}{14.331}=0.226715\text{ min} \\
T_{AI,unclassified} &= \frac{21.178}{60}\frac{2.165}{14.331}=0.053323\text{ min}
\end{aligned}
$$

**Code correspondence:** the general allocation is implemented at [src/phase-evidence.ts, lines 128-180](../src/phase-evidence.ts#L128-L180), and these exact displayed inputs are exercised at [test/benchmark.test.ts, lines 82-146](../test/benchmark.test.ts#L82-L146).

**Where:**

| Value | Unit | Definition |
|---|---:|---|
| $21.178$ | seconds | Engaged session duration $W_{engaged}$; all gaps were under the 300-second threshold, so it equals the elapsed window here |
| $2.961$ | seconds | Overlap-safe coding active time $a_{coding}$ |
| $9.205$ | seconds | Overlap-safe validation active time $a_{validation}$ |
| $2.165$ | seconds | Overlap-safe unclassified active time $a_{unclassified}$ |
| $14.331$ | seconds | Total overlap-safe active time $A_{\mathrm{active}}$ |
| $60$ | seconds/minute | Converts allocated seconds to minutes |
| $T_{AI,coding}$ | minutes | Engaged duration allocated to coding |
| $T_{AI,validation}$ | minutes | Engaged duration allocated to validation |
| $T_{AI,unclassified}$ | minutes | Engaged duration allocated to unclassified activity |

Planning and research had no attributed evidence in this small experiment, so both received zero AI time. The three nonzero phase times sum to 0.352967 minutes, exactly the 21.178-second engaged duration.

Under the base scenario, the experiment retained 32 changed source characters, validation had 6.484 seconds of tool runtime, 101 output tokens, 22 reasoning tokens, and one validation tool. The base branch sets $\omega_{V,base}=0$, so the 22 reasoning tokens do not enter the review term:

$$
\begin{aligned}
T_{manual,coding,base} &= \frac{0.50\times32}{5\times40}=0.080000\text{ min} \\
T_{manual,validation,base} &= \frac{6.484}{60}+\frac{0.25(101+0\times22)}{400}+1(0.25)=0.421192\text{ min} \\
T_{manual,unclassified,base} &= 1.25(0.053323)=0.066654\text{ min} \\
T_{manual,base} &= 0.080000+0.421192+0.066654=0.567846\text{ min}
\end{aligned}
$$

**Code correspondence:** the phase terms are implemented at [shared/benchmark.ts, lines 265-287](../shared/benchmark.ts#L265-L287), with total manual time at [lines 302-306](../shared/benchmark.ts#L302-L306); these exact displayed inputs are asserted at [test/benchmark.test.ts, lines 82-146](../test/benchmark.test.ts#L82-L146).

**Where:**

| Value or symbol | Unit | Definition |
|---|---:|---|
| $0.50$ | ratio | Base manual source-entry fraction $f_{base}$ |
| $32$ | characters | Retained changed source $C$ |
| $5$ | characters/word | Character-to-word conversion $c$ |
| $40$ | words/minute | Base code-entry rate $w_{base}$ |
| $6.484$ | seconds | Validation-tool runtime, converted to minutes by dividing by 60 |
| $0.25$ before $(101+0\times22)$ | ratio | Base relevant-token fraction $\alpha_{V,base}$ |
| $101$ | tokens | Validation output tokens $O_V$ |
| $0$ | ratio | Base reasoning-token weight $\omega_{V,base}$ |
| $22$ | tokens | Validation reasoning tokens $Q_V$, excluded at this weight |
| $400$ | tokens/minute | Base validation review rate $v_{V,base}$ |
| $1$ | execution | Validation tool count $N_V$ |
| $0.25$ after $1$ | minutes/execution | Base validation interaction overhead $\tau_{V,base}$ |
| $1.25$ | multiplier | Base unclassified manual multiplier $m_{base}$ |
| $0.053323$ | minutes | Measured unclassified AI time |
| $T_{manual,base}$ | minutes | Sum of all five manual phase estimates; planning and research are zero here |

Therefore the base modeled saving was $0.567846-0.352967=0.214879$ minutes. At $H=\$92/hour$, $\rho=0.50$, and $C_{AI}=\$0.157581$, this produces **$0.164740** of modeled benefit and **4.54%** modeled AI usage ROI. Under iteration 9 the same evidence produced 11.23%; the difference is entirely the removal of the 22 reasoning tokens from the review term.

Regression: [test/benchmark.test.ts, lines 82-146](../test/benchmark.test.ts#L82-L146) executes these displayed inputs through the shared benchmark and checks every rounded intermediate and result.

## Benchmark examples used during development

### 1. Cupcake storefront

**Application type:** Greenfield responsive commerce application  
**Stack:** React, TypeScript, Vite, CSS, local image assets  
**Purpose in the research:** Establish whether OTel could see value from a complete user-facing build rather than a synthetic code edit.

The application sold cupcakes through a product storefront. The implemented workflow included:

- responsive product browsing and imagery;
- adding products to a cart;
- an empty-cart state;
- a four-cupcake minimum-order rule;
- delivery-price calculation;
- delivery-date validation;
- checkout and order-confirmation states.

Validation covered production build, linting, zero editor diagnostics, image loading, no horizontal overflow, 320px mobile layout, 1280px desktop layout, cart constraints, delivery pricing, date errors, and successful confirmation.

Two builds played different roles:

| Build | Formula tested | What it exposed |
|---|---|---|
| Initial cupcake app | Daily OTel activity weights | The app worked, but edit/LoC instruments were absent and the emitter bypassed the Collector. Strict ROI was unavailable. |
| Sugarline rebuild | Named window, fixed tool weights, AI-cost correction, global speedup, phase speedup, mechanistic model | A single task could produce values from 0.7% to 5,941% depending on formula assumptions. |

The Sugarline experiment observed 21m 29s of elapsed AI-assisted time, five coding executions, seven research executions, eight planning executions, and later $11.5802 of AI usage value. It was the main regression example used to compare formula generations.

### 2. Northstar Cinema rewrite

**Application type:** Full domain rewrite of an existing frontend  
**Stack:** React, TypeScript, Vite, CSS  
**Purpose in the research:** Test whether a shorter task could still be uneconomic when long-context AI usage was expensive.

Sugarline was replaced with a cinema-booking application. The implemented workflow included:

- browsing films and schedules;
- selecting a showtime;
- an interactive seat map;
- seat-category pricing;
- a six-seat selection limit;
- checkout totals;
- booking confirmation.

Validation covered build, Oxlint, zero editor diagnostics, desktop and mobile booking flows, seat selection, pricing, checkout, OTel publication, and Grafana panels.

The experiment took 11m 39s and consumed 1,718.9 AI credits, worth $17.19. Under the global 55.8% speedup prior, the manual estimate was only 26.38 minutes and base ROI was -20.5%. Under phase priors it fell to -62.4%; under the later final-source mechanistic model it rose to 380.4%.

**What it exposed:** elapsed speed and delivered complexity do not determine ROI by themselves. Long-context AI cost and the manual-counterfactual model can reverse the conclusion.

### 3. Northstar retheme

**Application type:** Narrow visual change to an existing functional application  
**Implementation surface:** A 279-line, 5,067-character festival theme for Northstar Cinema  
**Purpose in the research:** Test whether the formula behaved sensibly for a small task with substantial pre-existing code.

The booking logic was intentionally kept unchanged. Validation covered build, lint, desktop/mobile containment, seat pricing, selection behavior, checkout, and confirmation so the experiment represented a successful retheme rather than a visually changed but broken app.

The task completed in 2m 59.6s. Its initial reading was 395.76 AI credits, or $3.96 of usage value. The source typing-equivalent was 25.3 minutes, but the global speedup prior inferred only 6.77 manual minutes.

Successive formulas produced materially different conclusions:

| Formula | Base ROI |
|---|---:|
| Global speedup prior | -4.5% |
| Phase-specific speedups | -56.3% |
| Mechanistic final-source model | 285.4% |

**What it exposed:** the same observed task moved from slightly unprofitable, to strongly unprofitable, to highly profitable without changing the application. Manual-counterfactual assumptions, not ROI arithmetic, dominated the answer. It also exposed the final-source-size bias because most of the cinema application was unchanged.

### 4. Neon Snake

**Application type:** Greenfield interactive game  
**Stack:** React 19, TypeScript 6, Vite 8, Phaser  
**Purpose in the research:** Test the mechanistic formula on stateful gameplay, canvas rendering, input handling, and runtime behavior rather than another form-based web application.

The game included:

- arrow-key and WASD controls;
- touch controls and swipe input;
- food spawning and collection;
- snake growth and score updates;
- increasing movement speed;
- pause and resume;
- wall and self collision;
- restart behavior;
- persistent high score.

Validation covered production build, Oxlint, nonblank canvas rendering, desktop and mobile layouts, keyboard/touch movement, food collection, growth, wall collision, self collision, pause/resume, restart, high-score persistence, and Grafana publication.

The experiment observed 11.53m of AI-assisted time and $18.69 of AI usage. The historical final-source mechanistic model estimated 110.06 manual minutes and 427.2% base ROI.

**What it exposed:** the formula produced similarly high implied speedups for a game and for commerce applications. That cross-domain consistency looked more like calibration bias than a universal productivity effect. Its historical ROI should not be treated as a current retained-delta result because no start-time source baseline existed.

### 5. Source-delta smoke fixture

**Application type:** Minimal deterministic TypeScript fixture  
**Starting source:** `export const baseline = 1;`  
**Completed source:** changed `1` to `2` and added `export const retained = true;`  
**Purpose in the research:** Isolate source accounting and OTel transport from application complexity.

The expected diff was one removed character, 32 added/modified retained characters, two added lines, and one removed line. It was the first example satisfying the current source-evidence contract. It proved:

- baseline capture occurs before work;
- unchanged code is not credited;
- exact character changes are counted;
- only aggregate evidence enters OTel;
- VictoriaMetrics readback matches the local deterministic diff;
- the benchmark uses the OTel-retained character value;
- missing or stale evidence fails the calculation.

The fixture also supplied a fully traceable current-formula calculation: 0.3530 AI-assisted minutes, $0.157581 AI usage, 0.5816 base manual minutes, 0.2286 modeled minutes saved, and **11.23%** base ROI at the revised $92/hour example rate.

### 6. Controlled benchmark fixtures

**Application type:** Synthetic phase and telemetry records used as deterministic unit tests  
**Purpose in the research:** Verify formula mechanics independently from noisy live telemetry.

The principal benchmark fixture contains explicit planning, research, coding, validation, and unclassified phase values; 34,513 retained characters; $11.58015725 AI usage; and a 0.9289 edit-survival signal. It was used to prove that survival remains visible but no longer changes retained-source ROI. Removing the duplicate discount changed the controlled result from 479.99% to 526.84%.

Automated fixtures also exercise:

- unique-span AI-credit and token aggregation;
- overlap-safe phase partitioning;
- exact reconciliation to experiment duration;
- exclusion of spans finishing after the experiment marker;
- scenario ordering and invalid assumptions;
- retained-source calculations and rename handling;
- aggregate OTLP payload privacy;
- failure on incomplete OTel source evidence;
- edit survival remaining audit-only.

The current TypeScript suite covers these behaviors, alongside stack health and end-to-end privacy validation.

## Historical result ledger

These values show how conclusions changed as the formula changed. They are intentionally not normalized into one supposedly comparable series.

| Task and formula version | Base result | Why it changed next |
|---|---:|---|
| Sugarline, fixed tool weights with allocated cost | 5,941% | Wrong task cost and arbitrary minutes/tool |
| Sugarline, same weights with actual AI cost | 556.3% | Cost fixed; numerator still unsupported |
| Sugarline, global speedup prior | 117.5% | Planning/research remained implicit |
| Sugarline, phase speedups | 0.7% | Phase priors produced implausible manual baselines |
| Sugarline, mechanistic final-source model | 734.2% | Final source overcredited unchanged code |
| Cinema rewrite, global speedup prior | -20.5% | One prior could not represent phase mix |
| Cinema rewrite, phase speedups | -62.4% | Phase priors remained uncalibrated |
| Cinema rewrite, mechanistic final-source model | 380.4% | Final source remained biased |
| Retheme, global speedup prior | -4.5% | Manual time depended entirely on 55.8% prior |
| Retheme, phase speedups | -56.3% | Manual estimate conflicted with artifact size |
| Retheme, mechanistic final-source model | 285.4% | Final source inflated coding output |
| Neon Snake, mechanistic final-source model | 427.2% | No start-time source baseline |
| Source-delta smoke, iteration 9 formula | 11.23% | Reasoning tokens were charged at a human review rate |
| Source-delta smoke, current formula (version 2) | 4.54% | Reasoning tokens removed from the review term; task is still too small for a stable general claim |

The last two rows use identical session evidence. Only the arithmetic changed. Results carrying different `formulaVersion` stamps are never summed into one portfolio total.

## What can be claimed now

### Supported claims

- AI-assisted engaged time is measured for each authoritative local OTel session across repositories, with elapsed time and activity density published beside it.
- Privacy-scrubbed spans are committed transactionally and deduplicated before worker processing; late event time does not exclude evidence.
- Phase allocations reconcile to that engaged time without overlap.
- AI-credit usage value is observed from matching direct turns when available, with unique OTel chat spans as fallback.
- Central sessions never borrow retained source from the installation repository; source-unavailable coding benefit is zero.
- When explicitly matched source evidence exists, its delta is deterministic, aggregate-only, exported through OTel, and read back from VictoriaMetrics.
- Planning, research, coding, validation, and unclassified work all contribute through explicit formulas.
- Every modeled calibration input is explicit and configurable.
- ROI is reproducible for the same session evidence, configuration, and formula version.
- Modeled delivery-cost reduction is bounded by the modeled manual baseline and does not change when a cheaper model produces the same evidence.
- A browser-local Custom result is reproducible from the same retained session evidence, saved set, and selected branch; it does not alter the retained evidence or worker metrics.
- Sessions with no eligible active spans produce no modeled value.

### Claims not yet supported

- That the modeled manual baseline equals what a specific developer would actually take.
- That the engaged-time threshold $G$ separates thinking from absence; it is an operating policy, not a measurement.
- That all retained source was authored by AI.
- That token relevance fractions represent useful work equally across task types.
- That the base scenario is more probable than the pessimistic or optimistic scenario, or that the three branches bound total model uncertainty. They hold $H$, $\rho$, and $G$ fixed.
- That phase mix can be compared across sessions while $C=0$ zeroes coding benefit. A research-heavy session scoring higher is an artifact of the retained-source gap, not a finding.
- That the model is net of rework, defect escape, or downstream review burden. Those are excluded and can only reduce value, so the reported figure is an upper bound in that respect.
- That a saved Custom calibration set is empirically valid merely because it can be applied to retained data.
- That one experiment establishes causal productivity improvement.
- That a central OTel session identifies its repository or proves retained source.
- That a difference between model-cohort medians was caused by a model or attributes any mixed session's value to one model.

## Recommended presentation language

Use:

> The dashboard reports modeled net value, break-even manual time, and a bounded delivery-cost reduction, with return on AI credit spend as a secondary ratio. Session-scoped OTel measures gap-bounded engaged time and phase evidence; matching local turns refine AI-credit and token totals. Retained source is unavailable in central collection, so coding benefit is zero. Manual equivalent time is estimated under explicit pessimistic, base, and optimistic presets or a named browser-local Custom sensitivity set. Custom selection does not change observed evidence or published worker metrics.

Avoid:

> Copilot produced a proven X% business ROI.

Also avoid presenting return on AI credit spend as the headline figure. Its denominator excludes labor and seat cost, so it improves when the same work is done with a cheaper model.

## Next research steps

1. Run paired comparable tasks with and without Copilot.
2. Record manual active time separately for planning, research, coding, and validation.
3. Calibrate token relevance, review rate, and code-entry rate by task category, and report the combined effective minutes-per-token because $\alpha$ and $v$ are only jointly identified.
4. Calibrate $\rho$ from follow-through data rather than leaving it a flat policy constant; it is the single most leveraged number in the model.
5. Measure the attribution sensitivity of the chat-span association window and the phase precedence order, and publish the resulting bound.
6. Close the retained-source gap with a same-session source-aware producer so the coding phase can be valued centrally.
7. Repeat each category enough times to report medians and dispersion.
8. Prefer net value, delivery-cost reduction, and break-even time alongside ROI, especially when AI cost is small.
9. Treat greenfield builds, rewrites, rethemes, bug fixes, and research-only tasks as separate populations.

The paired benchmark is the most important next step: it replaces the weakest modeled variable, manual counterfactual time, with observed evidence. Closing the retained-source gap is the most important product step, because coding is the phase with the strongest value story and it currently contributes only negative time.

## Implementation and evidence references

- Current benchmark formula: [shared/benchmark.ts, lines 225-358](../shared/benchmark.ts#L225-L358), re-exported through [src/benchmark.ts, line 1](../src/benchmark.ts#L1) for the worker
- Formula version stamp: [shared/benchmark.ts, lines 8-11](../shared/benchmark.ts#L8-L11) and the read-model guard in [backend/app/store.py](../backend/app/store.py)
- Transactional OTLP ingestion, replay deduplication, and cursor reads: [backend/app/main.py](../backend/app/main.py), [backend/app/store.py](../backend/app/store.py), and [src/incremental-evidence.ts](../src/incremental-evidence.ts)
- Session recalculation and retained-evidence migration: [src/continuous.ts](../src/continuous.ts) and [src/continuous-cli.ts](../src/continuous-cli.ts)
- Phase classification, gap bounding, and overlap handling: [src/phase-evidence.ts, lines 39-68](../src/phase-evidence.ts#L39-L68) and [lines 86-180](../src/phase-evidence.ts#L86-L180)
- OTel and direct-turn usage aggregation: [src/experiment-evidence.ts, lines 175-215](../src/experiment-evidence.ts#L175-L215) and [src/direct-turns.ts, lines 60-160](../src/direct-turns.ts#L60-L160)
- Retained source delta and OTLP payload: [src/source-delta.ts, lines 119-254](../src/source-delta.ts#L119-L254)
- Central session evaluation and evidence gate: [src/continuous.ts, lines 86-115](../src/continuous.ts#L86-L115) and [lines 300-330](../src/continuous.ts#L300-L330)
- Custom sensitivity evaluation and portfolio aggregation: [web/src/features/calibration/model.ts, lines 60-175](../web/src/features/calibration/model.ts#L60-L175)
- In-app Methodology flow and descriptive model cohorts: [web/src/features/methodology/MethodologyView.tsx](../web/src/features/methodology/MethodologyView.tsx) and [web/src/features/sessions/SessionViews.tsx](../web/src/features/sessions/SessionViews.tsx)
- Explicit calibration variables: [config/value-model.example.json](../config/value-model.example.json)
- Grafana presentation: [personal-copilot-value.json](../config/grafana/dashboards/personal-copilot-value.json)
- Formula and evidence tests: [test/benchmark.test.ts, lines 12-80](../test/benchmark.test.ts#L12-L80), [test/experiment-evidence.test.ts, lines 126-232](../test/experiment-evidence.test.ts#L126-L232), and [test/source-delta.test.ts, lines 16-99](../test/source-delta.test.ts#L16-L99)
- Local ignored experiment artifacts: `data/value/experiments` (generated at runtime; not checked in)
