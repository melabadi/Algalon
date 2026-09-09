# Session insight metrics

Session Insights is a local operational diagnostic over authoritative session usage. It does not produce a productivity score, change ROI, identify individual sessions, or depend on the prompt read model.

## Integrity boundary

The worker publishes one complete usage object for each authoritative OTel `session.id`. It may use complete direct-turn usage or complete OTel fallback, but both satisfy the same contract: source, requests, input/cache/uncached/output/reasoning tokens, AI credits and cost, a reconciled per-model breakdown, elapsed/engaged/active time, activity density, and complete phase evidence. Every model row must originate from at least one request. Phase token evidence requires a model span, tool runtime requires a tool call, and active phase time requires one of those origins. Model and session cost must match the observed conversion of 100 AI credits per US dollar. Each phase's allocated time must equal engaged time multiplied by that phase's share of active time. Direct-turn usage must contain positive requests and credits. A zero-request OTel session remains valid when every request-bound value is zero; independent tool spans may still contribute phase tool calls, while phase model and token totals cannot exceed the session totals.

A session becomes eligible 30 minutes after its last observed span. Session timestamps must identify absolute instants with an explicit UTC offset; portfolio duration uses the same rule and does not infer a local timezone. Completions up to five minutes ahead of the observation clock are treated as bounded clock skew and remain unsettled; timestamps farther in the future are invalid and stay in the integrity denominator. Every eligible session must satisfy the complete usage contract. If any settled session is incomplete or internally inconsistent, every behavioral metric row remains visible but its current/prior values and signal are withheld; only **Sessions with complete usage** retains a value. Prompt retention, prompt text, and prompt grouping cannot change an Insights value.

The read model persists a strict completion-time index. Insights reads only the selected and prior windows from that index while retaining every malformed timestamp in the integrity denominator.

Malformed artifact evidence remains in the local read model so the integrity denominator cannot improve by dropping bad rows. Public session and export payloads expose only finite JSON objects, and oversized scalar projections fail closed instead of overflowing portfolio totals.

Prompt counters and nano-AIU are accumulated as checked signed 64-bit integers. Credits and cost are derived once when each prompt group closes. The API emits a JSON-safe number only within JavaScript's exact range and always includes a canonical decimal `*Exact` companion; browser sorting, aggregation, and displayed counts use the full exact decimal value without compact notation. Negative, malformed, out-of-range, or aggregate-overflow evidence invalidates the whole prompt replacement instead of becoming zero, so incomplete evidence cannot overwrite a prior complete row. Disabling prompt content storage commits a SQLite text scrub before any inbox snapshot is read, so retained text is removed even when later indexing fails.

Current-formula benchmarks are accepted only when their phase time, phase-specific manual-work formulas, scenario totals, labor and AI costs, benefit, net value, ratios, break-even values, and capacity points satisfy the shared worker formula identities. AI cost is canonicalized as observed AI credits divided by 100 and must match exactly. Those values must also reconcile with the complete authoritative session usage contract, retained-source evidence, and every active model assumption. The worker fingerprints those calculation inputs and republishes retained sessions after restart when the model configuration changes. Migration uses accumulated inbox evidence and is withheld unless its exact session bounds match the tracked start and stored completion; retained and cached direct-turn costs are canonicalized before reuse. Failed or skipped migrations remain pending and retry on every worker poll, while the normal cursor path cannot bypass the migration gate. Artifact files are atomically replaced before publication metadata is marked current. Browser-local custom calibration applies the same complete-usage gate and requires absolute session bounds plus exact agreement among session duration, engaged time, and summed phase allocation before calculation. A missing branch, malformed saved profile, invalid session evidence, contradictory arithmetic, or non-finite value neutralizes every modeled portfolio output atomically. The API reports `modelingStatus` as `available`, `unavailable`, or `invalid`. Both non-available states withhold modeled values and show the corresponding portfolio warning; observed sessions, session windows, prompts, and AI usage remain visible and unchanged. UI fields labeled **AI time** use explicit engaged evidence and display unavailable when it is absent; they never substitute the wall-clock session window.

Backend CI proves this boundary in both directions:

- complete OTel session usage produces all twelve metrics with no prompt rows;
- adding partial or noisy prompt rows produces byte-for-byte identical metric payloads;
- missing fields, invalid numbers, inconsistent aggregate/model totals, invalid completion times, or invalid activity density fail closed;
- latest-session selection compares parsed UTC instants rather than timestamp text or local offsets;
- unsettled sessions and empty windows remain neutral.

## Aggregation

Each behavioral value is calculated once per settled session. Current values are medians of up to the latest five eligible sessions in the selected period. The prior value uses the immediately preceding period of equal length. Portfolio coverage values use all eligible sessions.

Request-normalized metrics divide authoritative session totals by authoritative session request count before taking the session median. Sessions with zero requests remain visible for session metrics but are excluded from request-normalized metrics.

## Operational signals

Signals are product-owned triage defaults, not published norms or productivity grades. `Low` or `High` marks the attention boundary; `Danger` marks the outer boundary. `No signal` only means no boundary was crossed.

| Metric | Attention boundary | Danger boundary |
| --- | ---: | ---: |
| Session cache reuse | Low below 50% | Below 20% |
| Uncached input per request | High above 25k | Above 100k |
| Context length per request | High above 32k | Above 64k |
| Session reasoning share | High above 50% | Above 75% |
| Output per request | High above 2k | Above 8k |
| Coding sessions where validation was detected | Low below 100% | Below 80% |
| Sessions with complete usage | Low below 100% | Below 95% |

Model requests per session, tool calls per request, AI usage per request, session duration, and activity density are not rated because neither direction is inherently better.

## Measurement register

| Metric | Session calculation | Interpretation |
| --- | --- | --- |
| Session cache reuse | cache-read input / input | Stable-prefix reuse; not a quality score. |
| Uncached input per request | uncached input / requests | Fresh context cost per model request. |
| Context length per request | input / requests | Total context read by each model request. |
| Session reasoning share | reasoning / (reasoning + output) | Hidden reasoning volume share, not correctness. |
| Output per request | output / requests | Non-reasoning generated tokens per request. |
| Model requests per session | requests | Session volume; an editor-window session is not a task. |
| Tool calls per request | phase tool calls / requests | Interaction volume; inspect accuracy and redundancy separately. |
| AI usage per request | observed AI cost / requests | Cost per request; requires an outcome denominator for efficiency claims. |
| Session duration | completed - started | Editor-window wall clock, not task duration. |
| Observed activity density | active span time / session wall clock | Telemetry density, not developer productivity. |
| Coding sessions where validation was detected | coding sessions with validation-tool evidence / coding sessions | Tool-pattern proxy, not proof of test quality. |
| Sessions with complete usage | complete settled sessions / settled sessions | Integrity gate for every other Insights metric. |

## Limits

- Session medians describe observed usage distribution; they do not establish causality or developer performance.
- Reasoning token counts reveal volume, not whether hidden reasoning was correct.
- Tool-call totals include every observed phase tool call.
- Prompt Explorer remains a local drill-down surface with its own retention limits, but it is not an Insights data source.
