import type { Dispatch, ReactNode, SetStateAction } from 'react';
import { Activity, ArrowRight, Clock3, Coins, FileCode2, Info } from 'lucide-react';
import { api } from '../../api';
import { Badge, DefinitionList, ErrorBand, LoadingBand, PanelHeading } from '../../components/ui';
import { CalibrationWorkbench } from '../calibration/CalibrationWorkbench';
import { activityLabel, scenarios, type SavedCalibration, type ScenarioSelection, type TrackedPhase } from '../calibration/model';
import { useLoad } from '../../hooks/useLoad';
import { money, scenarioLabel } from '../../lib/format';
import type { BenchmarkScenario, PhaseToolPatterns, Scenario } from '../../types';
import { Formula } from './Formula';
import { economicVariables, phaseVariables, type VariableDefinition } from './variables';

export function MethodologyView({ scenario, savedCalibrations, setSavedCalibrations, activeCalibrationId, setActiveCalibrationId }: {
  scenario: ScenarioSelection;
  savedCalibrations: SavedCalibration[];
  setSavedCalibrations: Dispatch<SetStateAction<SavedCalibration[]>>;
  activeCalibrationId: string | null;
  setActiveCalibrationId: Dispatch<SetStateAction<string | null>>;
}) {
  const load = useLoad(() => api.methodology(), []);
  if (load.loading) return <LoadingBand />;
  if (load.error || !load.data) return <ErrorBand message={load.error ?? 'Methodology unavailable'} retry={load.retry} />;
  const methodology = load.data;
  const activeCalibration = savedCalibrations.find((calibration) => calibration.id === activeCalibrationId);
  const configured = scenario === 'custom'
    ? activeCalibration?.draft.scenarios[activeCalibration.customScenario]
    : methodology.config.benchmark?.scenarios?.[scenario];
  return <>
    <header className="view-header"><h1>Methodology and formula</h1><p>How local evidence becomes a modeled return on observed AI usage cost.</p></header>
    <section className="plain-roi-band" aria-labelledby="plain-roi-title">
      <div className="plain-roi-intro"><span>Plain-English summary</span><h2 id="plain-roi-title">How we measure ROI</h2><p>We measure one Copilot session, estimate how long the same work might take without AI, value the modeled time difference, then compare that value with the AI usage cost.</p></div>
      <ol className="plain-roi-steps">
        <li><Clock3 size={18} aria-hidden="true" /><div><strong>Measure the work</strong><span>Observe engaged time, AI credits, model usage, and tool activity. Idle longer than the configured gap is excluded.</span></div></li>
        <li><Activity size={18} aria-hidden="true" /><div><strong>Sort the work</strong><span>Allocate that engaged time once across planning, research, coding, validation, and unclassified activity.</span></div></li>
        <li><FileCode2 size={18} aria-hidden="true" /><div><strong>Estimate manual time</strong><span>Apply the selected scenario to the evidence for each activity type.</span></div></li>
        <li><Coins size={18} aria-hidden="true" /><div><strong>Compare value with cost</strong><span>Value modeled time saved, subtract observed AI cost, and report net value and break-even time before the ratio.</span></div></li>
      </ol>
      <p className="plain-roi-limit"><Info size={15} aria-hidden="true" />The stopwatch and AI usage are observed. The without-AI time is a scenario estimate, so the result is modeled ROI, not proof that AI caused a productivity gain.</p>
    </section>
    <div className="claim-band"><div><strong>{methodology.claim}</strong><p>A sensitivity model over measured Copilot sessions. It is not a causal productivity study or invoice reconciliation.</p></div><Badge kind="modeled">{scenarioLabel(scenario)} selected</Badge></div>
    <div className="methodology-flow">
      <MethodologyStep number={1} evidence="Observed" title="Measure the work" description="Capture the work that actually happened before introducing any counterfactual assumptions.">
        <PanelHeading title="Activity events and tool calls" subtitle="Session spans establish the time window; model and tool events provide the activity, usage, and cost evidence inside it." />
        <ActivityTrackingTable patterns={methodology.config.benchmark?.phaseToolPatterns} />
      </MethodologyStep>
      <MethodologyStep number={2} evidence="Derived" title="Sort the work" description="Classify activity, split overlap once, and allocate gap-bounded engaged time across the five work phases.">
        <PanelHeading title="Observed phase-time allocation" subtitle="OTel activity determines phase shares; those shares allocate the session's engaged time without double-counting overlap." />
        <PhaseAllocationFormula />
        <PhaseEvidenceHandoff />
      </MethodologyStep>
      <MethodologyStep number={3} evidence="Configured" title="Estimate manual time" description="Apply the selected scenario to each phase's evidence to construct the without-AI baseline.">
        <PanelHeading title="Phase-specific manual-time model" subtitle="Each row turns one activity class into equivalent manual minutes. The selected constants are shown beside the general formula." />
        <PhaseFormulaTable configured={configured} charactersPerWord={methodology.config.benchmark?.charactersPerWord} />
        <ManualTimeExplanation />
      </MethodologyStep>
      <MethodologyStep number={4} evidence="Modeled" title="Compare value with cost" description="Follow the dependencies from time gained through economic value, net gain, break-even time, and return.">
        <PanelHeading title="Value formula dependency tree" subtitle="Read from the measured and modeled inputs at the top to the final return at the bottom. Each level depends on the formulas directly above it." />
        <ValueFormulaTree formulas={methodology.formulas} />
        <ValueInputExplanation capacityRealization={methodology.config.benchmark?.capacityRealization} />
      </MethodologyStep>

      <section className="methodology-followup" aria-labelledby="scenario-assumptions-title">
        <header className="methodology-section-header"><span>After the four steps</span><h2 id="scenario-assumptions-title">Scenario assumption matrix</h2><p>Compare only the counterfactual inputs. Observed session evidence remains fixed across pessimistic, base, and optimistic views.</p></header>
        <div className="methodology-section-body"><ScenarioAssumptionTable calibrations={methodology.config.benchmark?.scenarios} /></div>
      </section>
      <section className="methodology-followup calibration-panel" aria-labelledby="calibration-workspace-title">
        <header className="methodology-section-header"><span>Make it local</span><h2 id="calibration-workspace-title">Calibration workspace</h2><p>Save named custom profiles, apply them to data views, preserve the three presets, and audit which base inputs have defensible source coverage.</p></header>
        <CalibrationWorkbench methodology={methodology} scenario={scenario} savedCalibrations={savedCalibrations} setSavedCalibrations={setSavedCalibrations} activeCalibrationId={activeCalibrationId} setActiveCalibrationId={setActiveCalibrationId} />
      </section>

      <section className="methodology-reference" aria-labelledby="methodology-reference-title">
        <header className="methodology-section-header"><span>Reference</span><h2 id="methodology-reference-title">Interpretation, guardrails, and variables</h2><p>Use these details to audit the model after following the core reasoning path above.</p></header>
        <div className="layout-grid">
          <section className="panel span-8"><PanelHeading title="Selected scenario calibration" /><DefinitionList values={[
            ['Scenario', scenarioLabel(scenario)], ['Relevant tokens (P/R/V)', `${((configured?.planning.relevantTokenFraction ?? 0) * 100).toFixed(0)}% / ${((configured?.research.relevantTokenFraction ?? 0) * 100).toFixed(0)}% / ${((configured?.validation.relevantTokenFraction ?? 0) * 100).toFixed(0)}%`], ['Reasoning tokens counted (P/V)', `${((configured?.planning.reasoningTokenWeight ?? 0) * 100).toFixed(0)}% / ${((configured?.validation.reasoningTokenWeight ?? 0) * 100).toFixed(0)}%`], ['Review rate (P/R/V)', `${configured?.planning.tokensPerMinute ?? '—'} / ${configured?.research.tokensPerMinute ?? '—'} / ${configured?.validation.tokensPerMinute ?? '—'} tok/min`], ['Tool overhead (P/R/V)', `${configured?.planning.interactionMinutesPerTool ?? '—'} / ${configured?.research.interactionMinutesPerTool ?? '—'} / ${configured?.validation.interactionMinutesPerTool ?? '—'} min`], ['Manual code entry', `${((configured?.coding.manualEntryFraction ?? 0) * 100).toFixed(0)}% @ ${configured?.coding.wordsPerMinute ?? '—'} WPM`], ['Characters per word', String(methodology.config.benchmark?.charactersPerWord ?? '—')], ['Unclassified', `${configured?.unclassifiedManualMultiplier ?? '—'}×`], ['Loaded rate', money(methodology.config.loadedHourlyRateUsd).replace('+', '')], ['Capacity realization', `${((methodology.config.benchmark?.capacityRealization ?? 0) * 100).toFixed(0)}%`], ['Capacity band', (methodology.config.benchmark?.capacityRealizationBand ?? []).map((value) => `${(value * 100).toFixed(0)}%`).join(' / ') || '—'], ['Max bridged idle gap', methodology.config.benchmark?.maxIdleGapSeconds == null ? '—' : `${methodology.config.benchmark.maxIdleGapSeconds}s`]
          ]} /></section>
          <section className="panel span-4"><PanelHeading title="Guardrails" /><DefinitionList values={[
            ['Negative savings', 'Preserved'], ['Overlapping spans', 'Split once'], ['Idle beyond gap', 'Excluded from engaged time'], ['Empty evidence', 'No modeled value'], ['Cached input', 'Excluded where required'], ['Cross-repo source', 'Never borrowed'], ['Formula version', `v${methodology.formulaVersion ?? '—'}; older results excluded`], ['Prompt ROI', 'Not yet attributed']
          ]} /></section>
          <section className="panel span-12"><PanelHeading title="Evidence hierarchy" /><div className="evidence-grid"><Evidence type="Observed local" title="Session behavior">Session-scoped OTel supplies span activity, phases, and tools. Direct VS Code turns supply complete credits and model/token totals when available.</Evidence><Evidence type="Deterministic" title="Engaged time and source delta">Engaged time is derived by bridging think-time gaps and discarding longer idle. Matched source evidence can supply exact character, line, and file changes; central collection reports it unavailable rather than borrowing another repository.</Evidence><Evidence type="Configured" title="Human counterfactual">Labor rate, capacity realization and its band, idle-gap threshold, token relevance, reasoning-token weight, review speed, tool overhead, and typing.</Evidence><Evidence type="Modeled" title="Economic output">Manual time, savings, benefit, net value, break-even time, delivery-cost reduction, and ROI.</Evidence></div></section>
          <section className="panel span-6 boundary-panel"><strong>Overall aggregation</strong><p>Use each session's latest value once. Portfolio AI-assisted time is the summed engaged time capped by the union of session windows, so concurrent sessions cannot double count. Overall ROI is total net value divided by total observed AI cost. Sessions benchmarked under an older formula version stay listed but are excluded from modeled totals.</p></section>
          <section className="panel span-6 privacy-panel"><strong>Interpretation limit</strong><p>Time, cost, and phases are measured. Source-dependent coding benefit is zero when retained-source evidence is unavailable, which also skews the phase mix. Equivalent manual time remains modeled; results are not causal proof.</p></section>
          <section className="panel span-12"><PanelHeading title="Portfolio roll-up formulas" subtitle="The same session logic is aggregated once per latest session, with overlapping session windows unioned before labor cost is calculated." /><PortfolioFormulaList formulas={methodology.formulas} /></section>
          <section className="panel span-12"><PanelHeading title="Economic and time variables" subtitle="Symbols used by the value formula and its economic conversion." /><VariableTable variables={economicVariables} /></section>
          <section className="panel span-12"><PanelHeading title="Phase evidence and calibration variables" subtitle="Symbols used to allocate observed time and construct the manual counterfactual." /><VariableTable variables={phaseVariables} /></section>
        </div>
      </section>
    </div>
  </>;
}

function MethodologyStep({ number, evidence, title, description, children }: {
  number: number;
  evidence: 'Observed' | 'Derived' | 'Configured' | 'Modeled';
  title: string;
  description: string;
  children: ReactNode;
}) {
  const titleId = `methodology-step-${number}`;
  return <section className={`methodology-step step-${evidence.toLowerCase()}`} aria-labelledby={titleId}>
    <header className="methodology-step-header"><span className="methodology-step-number">{number}</span><div><span className="methodology-step-evidence">{evidence}</span><h2 id={titleId}>{title}</h2><p>{description}</p></div></header>
    <div className="methodology-step-body">{children}</div>
  </section>;
}

function ActivityTrackingTable({ patterns }: { patterns?: PhaseToolPatterns }) {
  const rows: Array<{ phase: TrackedPhase; observed: string; use: string }> = [
    { phase: 'planning', observed: 'Planning tool spans; nearby cost-bearing chat spans; output tokens; tool count; overlap-safe engaged time.', use: 'Output tokens and tool interactions estimate equivalent manual planning time. Reasoning tokens count only where a scenario sets a nonzero weight.' },
    { phase: 'research', observed: 'Research tool spans; nearby cost-bearing chat spans; uncached input tokens; tool count; overlap-safe engaged time.', use: 'Uncached material and tool interactions estimate equivalent manual research time.' },
    { phase: 'coding', observed: 'Coding tool spans and nearby chat spans for timing; optional same-session retained-source character delta.', use: 'Only matched retained source estimates manual coding time. Tool calls and tokens remain audit evidence and do not create coding benefit.' },
    { phase: 'validation', observed: 'Validation tool spans; merged tool runtime; nearby chat output tokens; tool count; overlap-safe engaged time.', use: 'Runtime, review tokens, and tool interactions estimate equivalent manual validation time.' },
    { phase: 'unclassified', observed: 'Cost-bearing chat spans or tool spans with no configured activity match, plus their overlap-safe engaged time.', use: 'Measured unclassified engaged time is multiplied by the selected scenario factor; no activity is silently discarded.' }
  ];
  return <>
    <div className="methodology-table-wrap"><table className="methodology-table activity-tracking-table"><thead><tr><th>Activity</th><th>Events and evidence tracked</th><th>Configured tool-name patterns</th><th>How it affects the estimate</th></tr></thead><tbody>{rows.map((row) => {
      const toolPatterns = row.phase === 'unclassified' ? [] : patterns?.[row.phase] ?? [];
      return <tr key={row.phase}><td><strong>{activityLabel(row.phase)}</strong></td><td>{row.observed}</td><td>{toolPatterns.length > 0 ? <div className="tool-pattern-list">{toolPatterns.map((pattern) => <code key={pattern}>{pattern}</code>)}</div> : <span className="muted">No matching configured pattern</span>}</td><td>{row.use}</td></tr>;
    })}</tbody></table></div>
    <div className="tracking-rules" aria-label="Event classification rules">
      <div><strong>Session event</strong><span>Authoritative OTel <code>session.id</code> groups the spans; span activity plus think-time gaps supplies the engaged duration that phase allocation distributes.</span></div>
      <div><strong>Model event</strong><span>Only <code>chat ...</code> spans with <code>copilot_usage_nano_aiu &gt; 0</code> supply phase tokens and OTel fallback cost.</span></div>
      <div><strong>Tool event</strong><span><code>execute_tool ...</code> spans or their tool-name attribute supply counts, intervals, and activity classification.</span></div>
      <div><strong>Nearby-tool rule</strong><span>A chat inherits the first tool starting from 0.25 seconds before to 3 seconds after the chat ends; otherwise it is unclassified.</span></div>
      <div><strong>Idle rule</strong><span>Gaps at or under the configured threshold count as think time; anything longer is excluded from engaged time entirely.</span></div>
    </div>
  </>;
}

function FormulaNode({ evidence, eyebrow, title, expression, description, supporting = [] }: {
  evidence: 'observed' | 'derived' | 'configured' | 'modeled';
  eyebrow?: string;
  title: string;
  expression: string;
  description: string;
  supporting?: Array<{ label: string; expression: string }>;
}) {
  return <div className={`formula-tree-node formula-node-${evidence}`}><span>{eyebrow ?? evidence}</span><h3>{title}</h3><Formula expression={expression} />{supporting.map((item) => <div className="formula-tree-supporting" key={item.label}><small>{item.label}</small><Formula expression={item.expression} /></div>)}<p>{description}</p></div>;
}

function ValueFormulaTree({ formulas }: { formulas: Record<string, string> }) {
  const formula = (key: string, fallback: string) => formulas[key] ?? fallback;
  return <div className="formula-tree" aria-label="Value formula dependency tree">
    <div className="formula-tree-level formula-tree-inputs">
      <FormulaNode evidence="derived" eyebrow="From step 2" title="AI-assisted time" expression="T_AI = sum_p(T_AI,p) = W_engaged / 60" description="The phase allocation from step 2 sums back to total gap-bounded engaged time." />
      <FormulaNode evidence="modeled" eyebrow="From step 3" title="Manual-only time" expression="T_manual,s = sum_p(T_manual,p,s)" description="The named manual-time outputs from step 3 form the counterfactual baseline." />
      <FormulaNode evidence="observed" eyebrow="From step 1" title="AI usage cost" expression={formula('aiCost', 'C_AI = observed AI credit spend')} description="Observed credits become the session's AI usage cost." />
    </div>
    <div className="formula-tree-connector" aria-hidden="true" />
    <div className="formula-tree-level formula-tree-single">
      <FormulaNode evidence="derived" title="Modeled time gained" expression={formula('totalSavings', 'T_saved,s = sum_p(T_saved,p,s)')} supporting={[{ label: 'Compare each phase', expression: formula('phaseSavings', 'T_saved,p,s = T_manual,p,s - T_AI,p') }]} description="For each phase, subtract the step-2 time from the step-3 manual estimate, then sum the results. Negative savings remain negative." />
    </div>
    <div className="formula-tree-connector formula-tree-branch-connector" aria-hidden="true" />
    <div className="formula-tree-level formula-tree-branches">
      <FormulaNode evidence="modeled" title="Gross delivery savings" expression={formula('grossCostSavings', 'Delta_C_gross,s = C_manual,s - C_assisted')} supporting={[{ label: 'Manual-only labor', expression: formula('manualLaborCost', 'C_manual,s = (T_manual,s / 60) x H') }, { label: 'AI-assisted total', expression: formula('aiAssistedTotalCost', 'C_assisted = (T_AI / 60) x H + C_AI') }]} description="The full loaded-rate delivery-cost difference, before capacity realization." />
      <FormulaNode evidence="modeled" title="Realized labor benefit" expression={formula('benefit', 'B_s = (T_saved,s / 60) x H x rho')} description="Apply loaded labor rate and capacity realization to modeled time gained." />
      <FormulaNode evidence="modeled" title="Break-even manual time" expression={formula('breakEven', 'T_manual,break-even = T_AI + C_AI / ((H / 60) x rho)')} description="The manual-only duration needed for realized benefit to cover observed AI cost." />
    </div>
    <div className="formula-tree-connector formula-tree-branch-connector" aria-hidden="true" />
    <div className="formula-tree-level formula-tree-outcomes">
      <FormulaNode evidence="modeled" title="Realized net gain" expression="N_s = B_s - C_AI" description="Subtract observed AI usage cost from the realized labor benefit." />
      <FormulaNode evidence="modeled" title="Delivery cost reduction" expression={formula('deliveryCostReduction', 'R_s = Delta_C_gross,s / C_manual,s')} description="Express gross delivery savings as a share of modeled manual-only labor cost." />
    </div>
    <div className="formula-tree-connector" aria-hidden="true" />
    <div className="formula-tree-level formula-tree-single formula-tree-final">
      <FormulaNode evidence="modeled" title="Return on AI credit spend" expression={formula('roi', 'ROI_s = (B_s - C_AI) / C_AI')} description="Report net gain per dollar of observed AI usage. Labor and seat cost are not in this denominator." />
    </div>
  </div>;
}

function ManualTimeExplanation() {
  return <div className="manual-time-summary">
    <div className="methodology-explanation"><strong>How <Formula expression="T_manual,p,s" /> is estimated</strong><p>There is no separate without-AI stopwatch. Each row above takes the evidence assigned to phase <Formula expression="p" /> in step 2, applies scenario <Formula expression="s" />, and produces the named manual-time variable on the left side of its equation.</p></div>
    <div className="manual-time-output" aria-label="Step 3 output passed to step 4"><span>Output passed to step 4</span><Formula expression="T_manual,s = sum_p(T_manual,p,s)" /><p>Sum the five phase outputs into the manual-only time used at the top of the value tree.</p></div>
  </div>;
}

function ValueInputExplanation({ capacityRealization }: { capacityRealization?: number }) {
  const capacitySetting = capacityRealization == null ? 'At the configured setting' : `At the current ${(capacityRealization * 100).toFixed(0)}% setting`;
  return <div className="methodology-explanation"><strong>What <Formula expression="rho" /> (rho, ρ) means</strong><p>Capacity realization is the configured share of modeled saved labor assumed to become economic value. {capacitySetting}, that share enters <Formula expression="B_s" /> and ROI; it does not change measured time or modeled time saved.</p></div>;
}

function PortfolioFormulaList({ formulas }: { formulas: Record<string, string> }) {
  const entries = [
    ['Unique portfolio time', formulas.portfolioAiTime],
    ['Portfolio modeled savings', formulas.portfolioSavings],
    ['Portfolio delivery cost', formulas.portfolioAssistedCost]
  ];
  return <div className="formula-reference-list">{entries.map(([label, expression]) => <div key={label}><span>{label}</span><Formula expression={expression ?? 'Formula unavailable'} /></div>)}</div>;
}

function Evidence({ type, title, children }: { type: string; title: string; children: ReactNode }) {
  return <div className="evidence-item"><Badge kind={type.startsWith('Observed') ? 'observed' : type === 'Deterministic' ? 'derived' : 'modeled'}>{type}</Badge><h3>{title}</h3><p>{children}</p></div>;
}

function PhaseAllocationFormula() {
  return <div className="equation-grid"><div><span>1. Split overlapping activity</span><Formula expression="a_p = sum_(j: p active) (d_j / n_j)" /><p>Each segment is divided equally among its distinct active phases. Multiple same-phase spans count once.</p></div><div><span>2. Bound the session by idle</span><Formula expression="W_engaged &lt;= W" /><p>Engaged time joins span activity across gaps at or under the configured threshold and discards longer idle.</p></div><div><span>3. Allocate engaged time</span><Formula expression="T_AI,p = (W_engaged / 60) x (a_p / A_active)" /><p>Active phase shares allocate the engaged duration, including the think-time gaps inside it.</p></div><div><span>4. Preserve the duration invariant</span><Formula expression="T_AI = sum_p(T_AI,p) = W_engaged / 60" /><p>If no eligible activity exists, every phase receives zero and no ROI is modeled.</p></div></div>;
}

function PhaseEvidenceHandoff() {
  return <div className="step-handoff" aria-label="Step 2 output becomes step 3 input">
    <div className="step-handoff-side"><span>Step 2 produces</span><strong>Evidence grouped by phase <Formula expression="p" /></strong><div className="step-handoff-symbols"><Formula expression="T_AI,p" /><Formula expression="O_P, O_V" /><Formula expression="Q_P, Q_V" /><Formula expression="U_R" /><Formula expression="N_X" /><Formula expression="C" /><Formula expression="D_V" /></div><p>Allocated time and the observed evidence keep the same phase identity.</p></div>
    <ArrowRight className="step-handoff-arrow" size={22} aria-hidden="true" />
    <div className="step-handoff-side"><span>Step 3 consumes</span><strong>The same phase evidence plus scenario <Formula expression="s" /></strong><Formula expression="T_manual,p,s" /><p>One phase-specific equation converts that bundle into modeled manual minutes.</p></div>
  </div>;
}

function PhaseFormulaTable({ configured, charactersPerWord = 5 }: { configured?: BenchmarkScenario; charactersPerWord?: number }) {
  const rows = [
    ['Planning', 'T_manual,planning,s = alpha_P,s x (O_P + omega_P,s x Q_P) / v_P,s + N_P x tau_P,s', configured ? `${configured.planning.relevantTokenFraction} x (O_P + ${configured.planning.reasoningTokenWeight ?? 0} x Q_P) / ${configured.planning.tokensPerMinute} + N_P x ${configured.planning.interactionMinutesPerTool}` : 'Configuration unavailable', 'Output tokens, weighted reasoning tokens, and planning tools'],
    ['Research', 'T_manual,research,s = alpha_R,s x U_R / v_R,s + N_R x tau_R,s', configured ? `${configured.research.relevantTokenFraction} x U_R / ${configured.research.tokensPerMinute} + N_R x ${configured.research.interactionMinutesPerTool}` : 'Configuration unavailable', 'Uncached input and research tools'],
    ['Coding', 'T_manual,coding,s = f_s x C / (c x w_s)', configured ? `${configured.coding.manualEntryFraction} x C / (${charactersPerWord} x ${configured.coding.wordsPerMinute})` : 'Configuration unavailable', 'Matched retained source; zero when unavailable'],
    ['Validation', 'T_manual,validation,s = D_V + alpha_V,s x (O_V + omega_V,s x Q_V) / v_V,s + N_V x tau_V,s', configured ? `D_V + ${configured.validation.relevantTokenFraction} x (O_V + ${configured.validation.reasoningTokenWeight ?? 0} x Q_V) / ${configured.validation.tokensPerMinute} + N_V x ${configured.validation.interactionMinutesPerTool}` : 'Configuration unavailable', 'Merged tool runtime, output tokens, and validation tools'],
    ['Unclassified', 'T_manual,unclassified,s = m_s x T_AI,unclassified', configured ? `${configured.unclassifiedManualMultiplier} x T_AI,unclassified` : 'Configuration unavailable', 'Measured unclassified engaged time']
  ];
  return <div className="methodology-table-wrap"><table className="methodology-table phase-formula-table"><thead><tr><th>Phase</th><th>General equation</th><th>Selected constants</th><th>Evidence from step 2</th></tr></thead><tbody>{rows.map(([phase, formula, selected, evidence]) => <tr key={phase}><td>{phase}</td><td><Formula expression={formula} /></td><td><Formula expression={selected} /></td><td>{evidence}</td></tr>)}</tbody></table></div>;
}

function ScenarioAssumptionTable({ calibrations }: { calibrations?: Record<Scenario, BenchmarkScenario> }) {
  const rows: Array<[string, string, (calibration: BenchmarkScenario) => string]> = [
    ['Relevant token fraction', 'alpha_P/R/V,s', (value) => `${value.planning.relevantTokenFraction} / ${value.research.relevantTokenFraction} / ${value.validation.relevantTokenFraction}`],
    ['Reasoning token weight', 'omega_P/V,s', (value) => `${value.planning.reasoningTokenWeight ?? 0} / ${value.validation.reasoningTokenWeight ?? 0}`],
    ['Human review rate', 'v_P/R/V,s', (value) => `${value.planning.tokensPerMinute} / ${value.research.tokensPerMinute} / ${value.validation.tokensPerMinute} tok/min`],
    ['Tool interaction overhead', 'tau_P/R/V,s', (value) => `${value.planning.interactionMinutesPerTool} / ${value.research.interactionMinutesPerTool} / ${value.validation.interactionMinutesPerTool} min/tool`],
    ['Manual source-entry fraction', 'f_s', (value) => `${(value.coding.manualEntryFraction * 100).toFixed(0)}%`],
    ['Manual source-entry rate', 'w_s', (value) => `${value.coding.wordsPerMinute} words/min`],
    ['Unclassified multiplier', 'm_s', (value) => `${value.unclassifiedManualMultiplier}x`]
  ];
  return <div className="methodology-table-wrap"><table className="methodology-table scenario-table"><thead><tr><th>Assumption</th><th>Symbol</th>{scenarios.map((value) => <th key={value}>{scenarioLabel(value)}</th>)}</tr></thead><tbody>{rows.map(([label, symbol, format]) => <tr key={symbol}><td>{label}</td><td><Formula expression={symbol} /></td>{scenarios.map((value) => <td key={value}>{calibrations?.[value] ? format(calibrations[value]) : '—'}</td>)}</tr>)}</tbody></table></div>;
}

function VariableTable({ variables }: { variables: VariableDefinition[] }) {
  return <div className="methodology-table-wrap"><table className="methodology-table variable-table"><thead><tr><th>Symbol</th><th>Unit</th><th>Evidence class</th><th>Definition</th></tr></thead><tbody>{variables.map((variable) => <tr key={variable.symbol}><td><Formula expression={variable.symbol} /></td><td>{variable.unit}</td><td><span className={`variable-source source-${variable.source.toLowerCase()}`}>{variable.source}</span></td><td>{variable.definition}</td></tr>)}</tbody></table></div>;
}
