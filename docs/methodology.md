# Algalon methodology and formula

**Status:** 31 August 2026<br>
**Formula version:** 2<br>
**Claim:** Modeled return on observed AI usage cost, not causal business ROI

This is the Markdown counterpart to Algalon's in-app **Methodology and formula** page. It documents the current method in the same order as the application: measure the work, sort it into phases, estimate equivalent manual time, and compare modeled value with observed AI usage cost. For the research history and superseded formulas, see [ROI Formula Evolution](roi-formula-evolution.md).

Values below are the bundled defaults. An installation can load different assumptions from `value-model.local.json`, and the in-app page always shows the values actually loaded by that installation.

## How Algalon measures ROI

1. **Measure the work.** Observe session bounds, span activity, AI credits, model usage, tokens, and tool activity. Idle longer than the configured gap is excluded from engaged time.
2. **Sort the work.** Allocate engaged time once across planning, research, coding, validation, and unclassified activity.
3. **Estimate manual time.** Apply the selected scenario to each phase's evidence to construct an explicit without-AI counterfactual.
4. **Compare value with cost.** Value modeled time saved, subtract observed AI usage cost, and report net value, break-even time, and delivery-cost reduction before return on AI credit spend.

The stopwatch and AI usage are observed. Equivalent manual time is a scenario estimate. The result is a sensitivity model, not proof that AI caused a productivity gain or an invoice reconciliation.

## 1. Measure the work

Algalon groups privacy-scrubbed OTel spans by authoritative resource-level `session.id`. The raw ID stays local; published session labels contain only a short hash. Matching read-only VS Code direct-turn logs refine complete AI-credit, model, and token totals when available. If the complete matching log set is unavailable, the session uses its complete OTel aggregate rather than mixing partial sources.

| Activity | Evidence tracked | How the evidence is used |
|---|---|---|
| Planning | Planning tool spans, nearby cost-bearing chat spans, output and reasoning tokens, tool count, overlap-safe activity | Output and scenario-weighted reasoning tokens plus tool interactions estimate equivalent manual planning time. |
| Research | Research tool spans, nearby cost-bearing chat spans, uncached input tokens, tool count, overlap-safe activity | Relevant uncached material plus tool interactions estimate equivalent manual research time. |
| Coding | Coding tool spans and nearby chat spans for timing; optional same-session retained-source character delta | Only matched retained source estimates manual coding time. Coding tools and tokens remain audit evidence and do not create coding benefit. |
| Validation | Validation tool spans, merged tool runtime, nearby chat output and reasoning tokens, tool count, overlap-safe activity | Runtime, output and scenario-weighted reasoning review, and tool interactions estimate equivalent manual validation time. |
| Unclassified | Cost-bearing chat or tool spans that match no configured phase | Measured unclassified engaged time is multiplied by an explicit scenario factor. |

Tool names are normalized and matched by configured substring patterns. The bundled examples include:

| Phase | Example configured patterns |
|---|---|
| Planning | `manage_todo_list`, `todo`, `ask_questions`, `questions` |
| Research | `search`, `read`, `fetch`, `grep`, `semantic`, `reference`, `documentation`, `list_dir`, `view_image`, `tool_search`, `usages` |
| Coding | `apply_patch`, `create_file`, `create_directory`, `replace_string_in_file`, `edit_notebook_file`, `rename` |
| Validation | `run_in_terminal`, `get_terminal_output`, `terminal`, `get_errors`, `test`, `playwright`, `screenshot`, `click`, `navigate`, `open_browser`, `run_task`, `task_output` |

The classification rules are deliberately mechanical:

- A cost-bearing model event is a `chat ...` span with `copilot_usage_nano_aiu > 0`.
- A tool event is an `execute_tool ...` span or a span carrying the tool-name attribute.
- A chat span inherits the first tool that starts from 0.25 seconds before through 3 seconds after the chat span ends.
- A chat span with no associated matching tool is unclassified.
- When one tool name matches multiple phases, precedence is planning, research, coding, then validation.
- Prompt text, source paths, commands, payloads, repository identity, and raw session IDs never become metric labels.

Privacy-scrubbed spans are committed transactionally to the local SQLite inbox and deduplicated by span identity before worker processing. The worker consumes monotonic cursor pages. Event time establishes session evidence; it is never an ingestion cutoff, so late spans can update their original session.

## 2. Sort the work

Let $j$ identify a non-overlapping segment between adjacent span boundaries, $d_j$ its duration, $n_j$ the number of distinct active phases, and $a_p$ the overlap-safe active time assigned to phase $p$:

$$
a_p=\sum_{j:\,p\text{ active}}\frac{d_j}{n_j}
$$

**Code correspondence:** overlap splitting is implemented in [src/phase-evidence.ts](../src/phase-evidence.ts).

Multiple overlapping spans from the same phase count that phase once. Cross-phase overlap is divided equally among the distinct active phases.

Let $W$ be the full elapsed session window and $G$ the maximum idle gap treated as think time. The bundled default is $G=300$ seconds. $W_{engaged}$ is the union of activity blocks after gaps no longer than $G$ are bridged. Longer idle is excluded:

$$
W_{engaged}\le W
$$

**Code correspondence:** gap-bounded activity blocks are implemented in [src/phase-evidence.ts](../src/phase-evidence.ts).

The active phase shares allocate that engaged duration:

$$
T_{AI,p}=\frac{W_{engaged}}{60}\frac{a_p}{A_{active}},
\qquad
A_{active}=\sum_p a_p
$$

**Code correspondence:** phase allocation is implemented in [src/phase-evidence.ts](../src/phase-evidence.ts).

The allocation preserves one duration invariant:

$$
T_{AI}=\sum_p T_{AI,p}=\frac{W_{engaged}}{60}\le\frac{W}{60}
$$

**Code correspondence:** the benchmark sums allocated phase seconds in [shared/benchmark.ts](../shared/benchmark.ts).

Elapsed window duration and activity density remain visible as observed diagnostics. If there is no eligible active span, every phase receives zero and no ROI is modeled.

## 3. Estimate manual time

There is no separate without-AI stopwatch. Each phase turns its own evidence into modeled equivalent manual minutes under scenario $s$:

$$
\begin{aligned}
T_{manual,planning,s} &= \frac{\alpha_{P,s}(O_P+\omega_{P,s}Q_P)}{v_{P,s}}+N_P\tau_{P,s} \\
T_{manual,research,s} &= \frac{\alpha_{R,s}U_R}{v_{R,s}}+N_R\tau_{R,s} \\
T_{manual,coding,s} &= \frac{f_sC}{cw_s} \\
T_{manual,validation,s} &= D_V+\frac{\alpha_{V,s}(O_V+\omega_{V,s}Q_V)}{v_{V,s}}+N_V\tau_{V,s} \\
T_{manual,unclassified,s} &= m_sT_{AI,unclassified}
\end{aligned}
$$

**Code correspondence:** all five manual-time expressions are implemented in [shared/benchmark.ts](../shared/benchmark.ts).

| Symbol | Meaning |
|---|---|
| $\alpha_{P/R/V,s}$ | Fraction of phase tokens treated as relevant manual review work |
| $O_P,O_V$ | Output tokens attributed to planning or validation |
| $Q_P,Q_V$ | Reasoning tokens attributed to planning or validation |
| $\omega_{P/V,s}$ | Share of planning or validation reasoning tokens entering the review term |
| $U_R$ | Uncached input tokens attributed to research; cached input is excluded |
| $v_{P/R/V,s}$ | Human review rate in tokens per minute |
| $N_P,N_R,N_V$ | Tool executions classified into the named phase |
| $\tau_{P/R/V,s}$ | Manual interaction overhead per tool execution |
| $C$ | Matched source characters added or modified and retained at completion |
| $f_s$ | Fraction of retained source assumed to require manual entry |
| $c$ | Characters-per-word conversion |
| $w_s$ | Manual source-entry rate |
| $D_V$ | Validation tool-active time after overlapping validation intervals are merged |
| $m_s$ | Manual-time multiplier for unclassified engaged time |

Reasoning tokens are not rendered to the developer. The pessimistic and base branches therefore use $\omega=0$; the optimistic branch's 0.25 is an explicit upper sensitivity bound. Research uses uncached input and has no reasoning-token term.

Central collection has no trustworthy repository root or retained-source delta. It sets $C=0$, labels source evidence **Telemetry only**, and creates no positive coding benefit. A source-aware producer may set $C>0$ only when complete retained-source evidence is matched to the same session.

## 4. Compare value with cost

The five phase estimates form the modeled manual-only baseline. Phase savings remain negative when measured engaged time exceeds modeled manual time:

$$
\begin{aligned}
T_{manual,s} &= \sum_p T_{manual,p,s} \\
T_{saved,p,s} &= T_{manual,p,s}-T_{AI,p} \\
T_{saved,s} &= \sum_p T_{saved,p,s}=T_{manual,s}-T_{AI}
\end{aligned}
$$

**Code correspondence:** phase and session totals are implemented in [shared/benchmark.ts](../shared/benchmark.ts).

At loaded labor rate $H$, capacity realization $\rho$, and observed AI usage cost $C_{AI}$:

$$
\begin{aligned}
C_{manual,s} &= \frac{T_{manual,s}}{60}H \\
C_{assisted} &= \frac{T_{AI}}{60}H+C_{AI} \\
\Delta C_{gross,s} &= C_{manual,s}-C_{assisted} \\
B_s &= \frac{T_{saved,s}}{60}H\rho \\
V_{net,s} &= B_s-C_{AI} \\
R_s &= \frac{\Delta C_{gross,s}}{C_{manual,s}} \\
ROI_s &= \frac{V_{net,s}}{C_{AI}},\qquad C_{AI}>0
\end{aligned}
$$

**Code correspondence:** labor cost, benefit, net value, delivery-cost reduction, and ROI are implemented in [shared/benchmark.ts](../shared/benchmark.ts).

One AI credit is $0.01$:

$$
C_{AI}=\frac{\sum copilot\_usage\_nano\_aiu}{10^{11}}
$$

**Code correspondence:** OTel and direct-turn cost conversion is implemented in [src/experiment-evidence.ts](../src/experiment-evidence.ts) and [src/direct-turns.ts](../src/direct-turns.ts).

The break-even manual duration is the point where realized labor benefit exactly covers observed AI usage:

$$
T_{manual,break\text{-}even}=T_{AI}+\frac{C_{AI}}{(H/60)\rho}
$$

**Code correspondence:** break-even duration is implemented in [shared/benchmark.ts](../shared/benchmark.ts).

Algalon leads with modeled net value, break-even manual time, and bounded delivery-cost reduction. Return on AI credit spend is secondary because its denominator excludes labor and seat cost and rises when identical evidence is produced by a cheaper model.

## Scenario assumption matrix

Only configured counterfactual assumptions vary by scenario. Observed evidence does not change.

| Assumption | Pessimistic | Base | Optimistic |
|---|---:|---:|---:|
| Relevant token fraction $\alpha_{P/R/V,s}$ | 0.10 / 0.10 / 0.10 | 0.25 / 0.25 / 0.25 | 0.50 / 0.50 / 0.50 |
| Reasoning-token weight $\omega_{P/V,s}$ | 0 / 0 | 0 / 0 | 0.25 / 0.25 |
| Human review rate $v_{P/R/V,s}$ | 600 / 600 / 600 tok/min | 400 / 400 / 400 tok/min | 250 / 250 / 250 tok/min |
| Tool interaction overhead $\tau_{P/R/V,s}$ | 0.12 / 0.12 / 0.12 min/tool | 0.25 / 0.25 / 0.25 min/tool | 0.50 / 0.50 / 0.50 min/tool |
| Manual source-entry fraction $f_s$ | 25% | 50% | 100% |
| Manual source-entry rate $w_s$ | 60 words/min | 40 words/min | 25 words/min |
| Unclassified multiplier $m_s$ | 1.00 | 1.25 | 1.50 |

**Code correspondence:** bundled defaults are defined in [config/value-model.example.json](../config/value-model.example.json), validated in [src/schema.ts](../src/schema.ts), and consumed by [shared/benchmark.ts](../shared/benchmark.ts).

The bundled global values are:

| Input | Default | Treatment |
|---|---:|---|
| Loaded labor rate $H$ | $92/hour | Rounded BLS wage-plus-benefits proxy; replace with local finance data |
| Capacity realization $\rho$ | 0.50 | Operating assumption; also reported at 0.25 / 0.50 / 0.75 |
| Maximum bridged idle gap $G$ | 300 seconds | Operating policy, not a measured constant |
| Characters per word $c$ | 5 | Directly supported standardized-word convention |
| Audit typing rate | 52 words/min | Secondary typing-equivalent audit metric; excluded from ROI |

Pessimistic, base, and optimistic are sensitivity branches, not probabilities or a confidence interval. $H$, $\rho$, and $G$ are identical across the three presets.

## Calibration workspace and Custom

The in-app calibration workspace exposes the model inputs without changing observed evidence.

- **Pessimistic**, **Base**, and **Optimistic** always show the worker-published branches.
- Named calibration sets are saved in browser-local storage.
- Each set retains three calibrated branches and records which branch **Custom uses**.
- **Custom** runs the same shared TypeScript benchmark over FastAPI-provided session evidence in the browser.
- Custom changes displayed modeled values only. It does not rewrite observed time, cost, tokens, tools, prompts, source completeness, worker artifacts, or `copilot_value_*` metrics.
- Exporting a calibration preserves the original branches in `presetScenarios`. Applying that fragment to `value-model.local.json` and restarting deliberately changes what the worker publishes.
- Relevant-token fractions, planning/validation reasoning-token weights, interaction overhead, manual-entry fractions, and unclassified multipliers must not decrease from pessimistic to optimistic. Review and code-entry rates must not increase.

A saved set is still a sensitivity input. Saving or applying it does not establish that its assumptions are empirically valid.

## Evidence hierarchy

| Evidence class | Inputs and outputs | Claim strength |
|---|---|---|
| Observed local | Session bounds, span activity, tools, AI credits, model and token usage | Directly measured local behavior, subject to telemetry coverage |
| Derived | Engaged time, activity density, uncached input, overlap-safe phase allocation | Deterministic transformation of observations and configured classification rules |
| Deterministic source evidence | Same-session retained character, line, and file delta when explicitly available | Strong only when source-aware evidence proves the session match |
| Configured | Labor rate, capacity realization, idle gap, token relevance, reasoning weight, review speed, tool overhead, source-entry assumptions | Explicit assumptions requiring local calibration |
| Modeled | Manual time, savings, benefit, net value, break-even duration, delivery-cost reduction, ROI | Scenario-sensitive outputs, not observations |

## Guardrails

- Preserve negative savings and net value.
- Split overlapping phase activity once.
- Exclude idle beyond $G$ from engaged time while retaining elapsed duration as a diagnostic.
- Produce no modeled value when a session has no eligible active spans.
- Exclude cached input where the phase formula requires uncached material.
- Never borrow retained source from the installation repository or another session.
- Do not discount retained source a second time with edit survival.
- Keep prompt ROI unavailable until prompt-level phase and retained-source attribution exist.
- Aggregate only results carrying the current formula version. Older sessions remain visible as observed evidence but are excluded from modeled totals.
- Keep `benchmarkFormulaVersion` and FastAPI's `CURRENT_FORMULA_VERSION` equal. `SESSION_CALCULATION_VERSION` is an independent retained-session re-evaluation trigger, not the formula version.

## Portfolio roll-up

Each session contributes its latest value once. To bound concurrency inflation, portfolio AI-assisted time is capped by the union of the full session windows. This cap does not reconstruct the union of engaged intervals, so it is a conservative concurrency bound rather than proof that every overlapping engaged second was counted once. For session $i$ with interval $I_i$, both $W_{engaged,i}$ and the interval measure $\mu$ are in seconds:

$$
T_{AI,portfolio}=\frac{1}{60}\min\left(\sum_i W_{engaged,i},\ \mu\left(\bigcup_i I_i\right)\right)
$$

**Code correspondence:** persisted portfolio aggregation is implemented in [backend/app/store.py](../backend/app/store.py), and browser-local Custom aggregation is implemented in [web/src/features/calibration/model.ts](../web/src/features/calibration/model.ts).

The portfolio then sums manual time and observed AI usage:

$$
\begin{aligned}
T_{manual,portfolio,s} &= \sum_i T_{manual,i,s} \\
T_{saved,portfolio,s} &= T_{manual,portfolio,s}-T_{AI,portfolio} \\
B_{portfolio,s} &= \frac{T_{saved,portfolio,s}}{60}H\rho \\
V_{net,portfolio,s} &= B_{portfolio,s}-\sum_i C_{AI,i} \\
ROI_{portfolio,s} &= \frac{V_{net,portfolio,s}}{\sum_i C_{AI,i}}
\end{aligned}
$$

**Code correspondence:** persisted portfolio aggregation is implemented in [backend/app/store.py](../backend/app/store.py), and browser-local Custom aggregation is implemented in [web/src/features/calibration/model.ts](../web/src/features/calibration/model.ts).

Portfolio ROI is not the arithmetic mean of session ROI percentages.

### Model cohorts

The Overall view groups currently displayed sessions by their exact set of request-bearing models. A mixed-model session remains intact in one explicit **Mixed** cohort; no share of its value is assigned to one model. Session count, requests, and AI usage are summed. Time gained, net value, and return are medians over valid whole-session results for the selected scenario.

Model cohorts are descriptive. Differences do not establish causal model performance because task mix, user behavior, session composition, calibration, and routing remain confounded.

## Calibration evidence

The source register separates four outcomes for each exact model input:

- **Direct support:** the source measures the same construct or defines the exact convention.
- **Proxy benchmark:** the source measures an analogous construct in another task or population.
- **Context only:** the source bounds interpretation or demonstrates heterogeneity without calibrating the input.
- **Local evidence needed:** no curated source supports the exact value.

The bundled register intentionally includes positive and negative findings:

| Source | Defensible use | Limit |
|---|---|---|
| Peng et al., [*The Impact of AI on Developer Productivity*](https://arxiv.org/abs/2302.06590) | Context: 55.8% faster completion in one bounded JavaScript task | Does not set any current phase constant |
| Microsoft and LinkedIn, [*2024 Work Trend Index Annual Report*](https://www.microsoft.com/en-us/worklab/work-trend-index/2024-ai-at-work-is-here-now-comes-the-hard-part) | Context: 90% of surveyed AI users reported saving time | Self-report across roles; does not set capacity realization |
| Becker et al., [*Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity*](https://arxiv.org/abs/2507.09089) | Context: experienced maintainers took 19% longer across mature-project tasks | Different task population and tooling; does not set a constant |
| Brysbaert, [*How Many Words Do We Read per Minute?*](https://doi.org/10.1016/j.jml.2019.104047) | Proxy for challenging the plausibility of token-review rates | Prose words are not model tokens or technical review |
| Dhakal et al., [*Observations on Typing from 136 Million Keystrokes*](https://doi.org/10.1145/3173574.3174220) | Proxy for the 52 WPM audit rate; direct support for five characters per standardized word | Transcription is not source-code authoring |
| U.S. Bureau of Labor Statistics, [*Software Developers, Quality Assurance Analysts, and Testers*](https://www.bls.gov/ooh/computer-and-information-technology/software-developers.htm) | Wage component of the $92/hour example proxy | U.S. occupational median, not local loaded labor cost |
| U.S. Bureau of Labor Statistics, [*Employer Costs for Employee Compensation - March 2026*](https://www.bls.gov/news.release/ecec.nr0.htm) | Benefits component of the $92/hour example proxy | Different period and population from the wage source |
| Cui et al., [*The Effects of Generative AI on High-Skilled Work*](https://doi.org/10.1287/mnsc.2025.00535) | Context: 26.08% more completed tasks across three workplace RCTs | Throughput differs from elapsed time and does not set phase constants |
| Mozannar et al., [*Reading Between the Lines*](https://doi.org/10.1145/3613904.3641936) | Proxy for the 0.12 and 0.25 minute tool-interaction settings | Suggestion verification is not a general tool execution |
| Wyrich et al., [*40 Years of Designing Code Comprehension Experiments*](https://doi.org/10.1145/3626522) | Context showing heterogeneous code-comprehension designs | Does not provide a transferable tokens-per-minute rate |

Only the five-character standardized-word convention has direct literature support for the exact modeled input. The supplied labor, typing, and interaction values are proxies. Relevant-token fractions, reasoning weight, coding assumptions, capacity realization, idle threshold, and the unclassified multiplier still require local evidence or explicit policy.

## Interpretation

Use this language:

> The dashboard reports modeled net value, break-even manual time, and a bounded delivery-cost reduction, with return on AI credit spend as a secondary ratio. Session-scoped OTel measures gap-bounded engaged time and phase evidence; matching local turns refine AI-credit and token totals. Retained source is unavailable in central collection, so coding benefit is zero. Manual equivalent time is estimated under explicit pessimistic, base, and optimistic presets or a named browser-local Custom sensitivity set.

Do not say that Copilot produced a proven business ROI. Do not present model-cohort differences as model attribution. Stronger causal claims require paired comparable tasks with and without AI, local phase timing, explicit task-category boundaries, and enough repetitions to report medians and dispersion.

## Implementation references

- In-app methodology flow: [web/src/features/methodology/MethodologyView.tsx](../web/src/features/methodology/MethodologyView.tsx)
- Formula variable help: [web/src/features/methodology/variables.ts](../web/src/features/methodology/variables.ts)
- Calibration workspace: [web/src/features/calibration/CalibrationWorkbench.tsx](../web/src/features/calibration/CalibrationWorkbench.tsx)
- Shared benchmark: [shared/benchmark.ts](../shared/benchmark.ts)
- Phase evidence: [src/phase-evidence.ts](../src/phase-evidence.ts)
- Session processing: [src/continuous.ts](../src/continuous.ts)
- Transactional inbox: [backend/app/main.py](../backend/app/main.py), [backend/app/store.py](../backend/app/store.py), and [src/incremental-evidence.ts](../src/incremental-evidence.ts)
- Model cohorts and portfolio views: [web/src/features/sessions/SessionViews.tsx](../web/src/features/sessions/SessionViews.tsx)
- Bundled defaults and evidence register: [config/value-model.example.json](../config/value-model.example.json)
