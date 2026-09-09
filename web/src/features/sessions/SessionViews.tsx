import { useDeferredValue, useState } from 'react';
import { Activity, AlertTriangle, BarChart3, ChevronRight, Clock3, Coins, Database, Download, FileCode2, Gauge, MessageSquareText, Search, Wrench } from 'lucide-react';
import { api } from '../../api';
import { ComparisonStrip, DefinitionList, ErrorBand, Kpi, LoadingBand, PanelHeading, SortableHeader } from '../../components/ui';
import { calibratedOverview, calibratedSession, observedAssistedSeconds, phases, scenarios, type SavedCalibration, type ScenarioSelection } from '../calibration/model';
import { useLoad } from '../../hooks/useLoad';
import { messages } from '../../i18n';
import { compact, credits, dateTime, duration, elapsedMinutes, exactInteger, exactIntegerText, money, minutes, percent, ratio, scenarioLabel } from '../../lib/format';
import { pagePath } from '../../lib/routes';
import { comparePrompts, compareSessions, type PromptSort, type SessionSort, type SortDirection } from '../../lib/sorting';
import type { ModelingStatus, Overview, Page, Prompt, Scenario, ScenarioResult, Session } from '../../types';

const metricHelp = messages.metricHelp;

type Navigate = (page: Page) => void;
interface BreadcrumbItem { label: string; page?: Page }

function sessionEngagedSeconds(session: Session): number | null {
  const value = session.usage.engagedSeconds;
  return value !== undefined && Number.isFinite(value) && value >= 0 ? value : null;
}

function Breadcrumbs({ items, navigate }: { items: BreadcrumbItem[]; navigate: Navigate }) {
  return <div className="breadcrumbs">{items.map((item, index) => <span key={`${item.label}-${index}`} className="breadcrumb-item">{index > 0 && <ChevronRight size={12} />}{item.page ? <a className="breadcrumb-link" href={pagePath(item.page)} onClick={(event) => { event.preventDefault(); navigate(item.page!); }}>{item.label}</a> : <span className="breadcrumb-current" aria-current="page">{item.label}</span>}</span>)}</div>;
}

function ViewHeader({ title, subtitle, breadcrumbs, navigate }: { title: string; subtitle: string; breadcrumbs?: BreadcrumbItem[]; navigate?: Navigate }) {
  return <header className="view-header">{breadcrumbs && navigate && <Breadcrumbs items={breadcrumbs} navigate={navigate} />}<h1>{title}</h1><p>{subtitle}</p></header>;
}

function ScenarioTable({ values, selected, customName }: { values: Partial<Record<ScenarioSelection, ScenarioResult | null>>; selected: ScenarioSelection; customName?: string }) {
  const rows: ScenarioSelection[] = customName ? [...scenarios, 'custom'] : scenarios;
  return <table><thead><tr><th>Scenario</th><th>Manual</th><th>Saved</th><th>Net value</th><th>Return</th></tr></thead><tbody>{rows.map((name) => {
    const result = values[name];
    return <tr key={name} className={name === selected ? 'selected-row' : ''}><td>{name === 'custom' ? `Custom · ${customName}` : scenarioLabel(name)}</td><td>{minutes(result?.estimatedManualMinutes)}</td><td className={(result?.estimatedMinutesSaved ?? 0) < 0 ? 'negative' : ''}>{minutes(result?.estimatedMinutesSaved)}</td><td className={(result?.netValueUsd ?? 0) < 0 ? 'negative' : ''}>{money(result?.netValueUsd)}</td><td className={(result?.roi ?? 0) < 0 ? 'negative' : ''}>{ratio(result?.roi)}</td></tr>;
  })}</tbody></table>;
}

function ActiveCalibrationNotice({ calibration }: { calibration?: SavedCalibration }) {
  if (!calibration) return null;
  return <div className="active-calibration-band"><Gauge size={16} /><div><strong>Custom · {calibration.name}</strong><span>Observed evidence is unchanged. Modeled values use this saved set’s {scenarioLabel(calibration.customScenario).toLowerCase()} calibration.</span></div></div>;
}

function ModelingStatusBand({ status }: { status: ModelingStatus }) {
  if (status === 'available') return null;
  const reason = status === 'invalid'
    ? 'the selected model inputs or session evidence are invalid'
    : 'no eligible modeled session evidence is available';
  return <div className="insight-banner" role="status"><AlertTriangle size={15} /><span>Modeled values are unavailable because {reason}. Observed evidence is unchanged.</span></div>;
}

function EmptySessions() {
  return <div className="empty-view"><Activity size={28} /><h2>No measured sessions yet</h2><p>Reload VS Code after installation, then use Copilot from any local repository. Sessions appear as OTel evidence reaches the local collector.</p></div>;
}

interface ModelCohort {
  key: string;
  label: string;
  sessions: number;
  modeledSessions: number;
  requests: number;
  aiCostUsd: number;
  medianMinutesSaved: number | null;
  medianNetValueUsd: number | null;
  medianRoi: number | null;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const ordered = [...values].sort((left, right) => left - right);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 === 0 ? (ordered[middle - 1] + ordered[middle]) / 2 : ordered[middle];
}

function modelCohorts(sessions: Session[]): ModelCohort[] {
  const cohorts = new Map<string, {
    key: string;
    label: string;
    sessions: number;
    modeledSessions: number;
    requests: number;
    aiCostUsd: number;
    minutesSaved: number[];
    netValues: number[];
    returns: number[];
  }>();
  for (const session of sessions) {
    const models = (session.usage.models ?? [])
      .filter((model) => model.requests > 0)
      .sort((left, right) => left.model.localeCompare(right.model));
    const names = [...new Set(models.map((model) => model.model))];
    const key = names.length > 0 ? names.join('\u0000') : 'unknown';
    const current = cohorts.get(key) ?? {
      key,
      label: names.length === 0 ? 'Unknown model' : names.length === 1 ? names[0] : `Mixed · ${names.join(' + ')}`,
      sessions: 0,
      modeledSessions: 0,
      requests: 0,
      aiCostUsd: 0,
      minutesSaved: [],
      netValues: [],
      returns: []
    };
    current.sessions += 1;
    current.requests += models.reduce((total, model) => total + model.requests, 0);
    current.aiCostUsd += session.aiCostUsd;
    if (session.scenarioResult) {
      current.modeledSessions += 1;
      current.minutesSaved.push(session.scenarioResult.estimatedMinutesSaved);
      current.netValues.push(session.scenarioResult.netValueUsd);
      if (session.scenarioResult.roi !== null) current.returns.push(session.scenarioResult.roi);
    }
    cohorts.set(key, current);
  }
  return [...cohorts.values()]
    .map((cohort) => ({
      key: cohort.key,
      label: cohort.label,
      sessions: cohort.sessions,
      modeledSessions: cohort.modeledSessions,
      requests: cohort.requests,
      aiCostUsd: cohort.aiCostUsd,
      medianMinutesSaved: median(cohort.minutesSaved),
      medianNetValueUsd: median(cohort.netValues),
      medianRoi: median(cohort.returns)
    }))
    .sort((left, right) => right.sessions - left.sessions || right.aiCostUsd - left.aiCostUsd || left.label.localeCompare(right.label));
}

export function OverallView({ scenario, days, calibration, navigate }: { scenario: ScenarioSelection; days: number; calibration?: SavedCalibration; navigate: Navigate }) {
  const [sessionSearch, setSessionSearch] = useState('');
  const [sessionSort, setSessionSort] = useState<SessionSort>('startedAt');
  const [sortDirection, setSortDirection] = useState<SortDirection>('descending');
  const deferredSessionSearch = useDeferredValue(sessionSearch.trim().toLocaleLowerCase());
  const sortFromHeader = (sort: SessionSort) => {
    if (sort === sessionSort) {
      setSortDirection((current) => current === 'descending' ? 'ascending' : 'descending');
      return;
    }
    setSessionSort(sort);
    setSortDirection(sort === 'experiment' ? 'ascending' : 'descending');
  };
  const load = useLoad(
    () => Promise.all(scenarios.map((name) => api.overview(name, days))).then((values) => Object.fromEntries(values.map((value) => [value.scenario, value])) as Record<Scenario, Overview>),
    [days]
  );
  if (load.loading) return <LoadingBand />;
  if (load.error || !load.data) return <ErrorBand message={load.error ?? 'No data'} retry={load.retry} />;
  const overviews = load.data;
  const customOverview = calibration ? calibratedOverview(overviews.base, calibration.draft, calibration.customScenario) : null;
  const selected = scenario === 'custom' && customOverview ? customOverview : overviews[scenario as Scenario];
  const totals = selected.totals;
  const modeledWithheld = selected.modelingStatus !== 'available';
  const displayedSessions = modeledWithheld
    ? selected.sessions.map((session) => ({ ...session, scenarioResult: null }))
    : selected.sessions;
  const observedAiSeconds = observedAssistedSeconds(selected.sessions);
  const scenarioValues = Object.fromEntries(scenarios.map((name) => [
    name,
    overviews[name].modelingStatus === 'available' ? {
    estimatedManualMinutes: overviews[name].totals.estimatedManualMinutes,
    estimatedMinutesSaved: overviews[name].totals.estimatedMinutesSaved,
    estimatedManualLaborCostUsd: overviews[name].totals.estimatedManualLaborCostUsd,
    estimatedAiAssistedLaborCostUsd: overviews[name].totals.estimatedAiAssistedLaborCostUsd,
    estimatedAiAssistedTotalCostUsd: overviews[name].totals.estimatedAiAssistedTotalCostUsd,
    estimatedGrossCostSavingsUsd: overviews[name].totals.estimatedGrossCostSavingsUsd,
    estimatedBenefitUsd: overviews[name].totals.estimatedBenefitUsd,
    netValueUsd: overviews[name].totals.netValueUsd,
    roi: overviews[name].totals.roi,
    taskTimeReduction: overviews[name].totals.taskTimeReduction,
    breakEvenManualMinutes: 0,
      phases: {}
    } : null
  ])) as Partial<Record<ScenarioSelection, ScenarioResult | null>>;
  if (customOverview) {
    scenarioValues.custom = customOverview.modelingStatus !== 'available'
      ? null
      : { ...customOverview.totals, breakEvenManualMinutes: 0, phases: {} };
  }
  const filteredSessions = displayedSessions.filter((session) => {
    if (!deferredSessionSearch) return true;
    return [session.experiment, dateTime(session.startedAt), session.latestMessageAt ? dateTime(session.latestMessageAt) : '', session.status, session.sourceEvidenceComplete ? 'complete source' : 'telemetry only'].some((value) => value.toLocaleLowerCase().includes(deferredSessionSearch));
  }).sort((left, right) => compareSessions(left, right, sessionSort, sortDirection));
  const cohorts = modelCohorts(filteredSessions);

  return <>
    <ViewHeader title="Overall Copilot value" subtitle="Aggregate local Copilot sessions across repositories and compare observed delivery with the modeled manual-only baseline." />
    <div className="view-actions"><a className="command-button" href={api.exportCsv()} download="algalon-data.csv"><Download size={15} />{messages.controls.exportAllData}</a></div>
    <ActiveCalibrationNotice calibration={scenario === 'custom' ? calibration : undefined} />
    <ModelingStatusBand status={selected.modelingStatus} />
    <div className="kpi-grid comparison-kpi-grid">
      <Kpi label="Manual-only time" value={modeledWithheld ? '—' : elapsedMinutes(totals.estimatedManualMinutes)} kind="modeled" icon={<FileCode2 size={15} />} help={metricHelp.manualOnlyTime} />
      <Kpi label="AI-assisted time" value={elapsedMinutes(observedAiSeconds === null ? null : observedAiSeconds / 60)} kind="observed" icon={<Clock3 size={15} />} help={metricHelp.aiAssistedTime} />
      <Kpi label="Time gained" value={modeledWithheld ? '—' : minutes(totals.estimatedMinutesSaved)} kind="modeled" icon={<Gauge size={15} />} help={metricHelp.timeGained} />
      <Kpi label="Manual-only cost" value={modeledWithheld ? '—' : money(totals.estimatedManualLaborCostUsd).replace('+', '')} kind="modeled" icon={<Coins size={15} />} help={metricHelp.manualOnlyCost} />
      <Kpi label="AI-assisted delivery cost" value={modeledWithheld ? '—' : money(totals.estimatedAiAssistedTotalCostUsd).replace('+', '')} kind="modeled" icon={<Coins size={15} />} help={metricHelp.aiAssistedTotal} />
      <Kpi label="Realized net gain" value={modeledWithheld ? '—' : money(totals.netValueUsd)} kind="modeled" icon={<BarChart3 size={15} />} help={metricHelp.realizedNetGain} />
    </div>
    <ComparisonStrip facts={[
      { label: 'Measured sessions', value: String(totals.sessions), kind: 'observed' },
      { label: 'Session window', value: duration(totals.durationSeconds), kind: 'observed', help: metricHelp.sessionWindow },
      { label: 'Observed AI usage', value: money(totals.aiCostUsd).replace('+', ''), kind: 'observed', help: metricHelp.observedAiUsage },
      { label: 'Gross delivery savings', value: modeledWithheld ? '—' : money(totals.estimatedGrossCostSavingsUsd), kind: 'modeled', help: metricHelp.grossDeliverySavings },
      { label: 'Delivery cost reduction', value: modeledWithheld ? '—' : percent(totals.modeledDeliveryCostReduction ?? undefined), kind: 'modeled', help: metricHelp.deliveryCostReduction },
      { label: 'Realized labor benefit', value: modeledWithheld ? '—' : money(totals.estimatedBenefitUsd), kind: 'modeled', help: metricHelp.realizedLaborBenefit },
      { label: 'Return / AI credit spend', value: modeledWithheld ? '—' : ratio(totals.roi), kind: 'modeled', help: metricHelp.returnOnAiCost }
    ]} note="Gross delivery savings use the full loaded-rate difference. Realized net gain applies configured capacity realization, then subtracts observed AI usage." />
    {selected.sessions.length === 0 ? <EmptySessions /> : <div className="layout-grid"><section className="panel span-12"><div className="panel-toolbar"><PanelHeading title="Measured sessions" subtitle="Select a row to inspect session evidence and ROI sensitivity." /><div className="session-table-controls"><label className="search-field"><Search size={15} aria-hidden="true" /><input type="search" value={sessionSearch} onChange={(event) => setSessionSearch(event.target.value)} placeholder="Filter sessions" aria-label="Filter sessions" /></label></div></div>
      {filteredSessions.length === 0 ? <div className="empty-inline">No sessions match “{sessionSearch.trim()}”.</div> : <div className="table-scroll"><table className="session-table"><thead><tr>
        <SortableHeader label="Session" sort="experiment" activeSort={sessionSort} direction={sortDirection} defaultDirection="ascending" onSort={sortFromHeader} /><SortableHeader label="Started" sort="startedAt" activeSort={sessionSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="Latest message" sort="latestMessageAt" activeSort={sessionSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="Prompts" sort="promptCount" activeSort={sessionSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="AI time" sort="engagedSeconds" activeSort={sessionSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="AI cost" sort="aiCostUsd" activeSort={sessionSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="Saved" sort="estimatedMinutesSaved" activeSort={sessionSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="Net value" sort="netValueUsd" activeSort={sessionSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="Return" sort="roi" activeSort={sessionSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="Evidence" sort="sourceEvidenceComplete" activeSort={sessionSort} direction={sortDirection} onSort={sortFromHeader} />
      </tr></thead><tbody>{filteredSessions.map((session) => {
        const engagedSeconds = sessionEngagedSeconds(session);
        return <tr key={session.experiment} className="clickable-row" onClick={() => navigate({ name: 'session', experiment: session.experiment })}><td>{session.experiment}</td><td>{dateTime(session.startedAt)}</td><td>{session.latestMessageAt ? dateTime(session.latestMessageAt) : '—'}</td><td>{session.promptCount}</td><td>{engagedSeconds === null ? '—' : duration(engagedSeconds)}</td><td>{money(session.aiCostUsd).replace('+', '')}</td><td>{minutes(modeledWithheld ? undefined : session.scenarioResult?.estimatedMinutesSaved)}</td><td>{money(modeledWithheld ? undefined : session.scenarioResult?.netValueUsd)}</td><td>{ratio(modeledWithheld ? undefined : session.scenarioResult?.roi)}</td><td className={session.sourceEvidenceComplete ? 'positive' : 'muted'}>{session.sourceEvidenceComplete ? 'Source + OTel' : 'Telemetry only'}</td></tr>;
      })}</tbody></table></div>}
    </section>{cohorts.length > 0 && <section className="panel span-12"><PanelHeading title="ROI by model cohort" subtitle="Descriptive whole-session outcomes grouped by exact model set. Mixed sessions stay intact; this does not attribute benefit to individual models." /><div className="table-scroll"><table className="model-cohort-table"><thead><tr><th>Model cohort</th><th>Sessions</th><th>Modeled</th><th>Requests</th><th>AI usage</th><th>Median time gained</th><th>Median net value</th><th>Median return</th></tr></thead><tbody>{cohorts.map((cohort) => <tr key={cohort.key}><td><strong>{cohort.label}</strong></td><td>{cohort.sessions}</td><td>{cohort.modeledSessions} / {cohort.sessions}</td><td>{compact(cohort.requests)}</td><td>{money(cohort.aiCostUsd).replace('+', '')}</td><td className={(cohort.medianMinutesSaved ?? 0) < 0 ? 'negative' : ''}>{minutes(cohort.medianMinutesSaved)}</td><td className={(cohort.medianNetValueUsd ?? 0) < 0 ? 'negative' : ''}>{money(cohort.medianNetValueUsd)}</td><td className={(cohort.medianRoi ?? 0) < 0 ? 'negative' : ''}>{ratio(cohort.medianRoi)}</td></tr>)}</tbody></table></div></section>}<section className="panel span-8"><PanelHeading title="Portfolio scenario sensitivity" subtitle="Each session contributes its latest value once." /><ScenarioTable values={scenarioValues} selected={scenario} customName={calibration?.name} /></section><section className="panel span-4"><PanelHeading title="Coverage" /><DefinitionList values={[
      ['Sessions with retained-source evidence', `${displayedSessions.filter((item) => item.sourceEvidenceComplete).length} / ${displayedSessions.length}`], ['Sessions with modeled ROI', `${displayedSessions.filter((item) => item.scenarioResult).length} / ${displayedSessions.length}`], ['Prompts indexed locally', String(totals.prompts)], ['Prompt ROI attributed', `0 / ${totals.prompts}`]
    ]} /></section></div>}
  </>;
}

function PromptChainTable({ prompts, experiment, navigate }: { prompts: Prompt[]; experiment: string; navigate: Navigate }) {
  const [promptSort, setPromptSort] = useState<PromptSort>('startedAt');
  const [sortDirection, setSortDirection] = useState<SortDirection>('ascending');
  const sortFromHeader = (sort: PromptSort) => {
    if (sort === promptSort) {
      setSortDirection((current) => current === 'descending' ? 'ascending' : 'descending');
      return;
    }
    setPromptSort(sort);
    setSortDirection(sort === 'content' ? 'ascending' : 'descending');
  };
  if (prompts.length === 0) return <div className="empty-inline">No prompts have been indexed for this session.</div>;
  const sortedPrompts = [...prompts].sort((left, right) => comparePrompts(left, right, promptSort, sortDirection));
  return <div className="table-scroll"><table><thead><tr>
    <SortableHeader label="Prompt content" sort="content" activeSort={promptSort} direction={sortDirection} defaultDirection="ascending" onSort={sortFromHeader} /><SortableHeader label="Time" sort="startedAt" activeSort={promptSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="Requests" sort="modelRequests" activeSort={promptSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="Tools" sort="toolCalls" activeSort={promptSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="Input" sort="inputTokens" activeSort={promptSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="Cache" sort="cacheReadRatio" activeSort={promptSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="Output" sort="outputTokens" activeSort={promptSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="AI usage" sort="aiCredits" activeSort={promptSort} direction={sortDirection} onSort={sortFromHeader} /><SortableHeader label="ROI" sort="roi" activeSort={promptSort} direction={sortDirection} onSort={sortFromHeader} />
  </tr></thead><tbody>{sortedPrompts.map((prompt) => <tr key={prompt.promptId} className="clickable-row" onClick={() => navigate({ name: 'prompt', experiment, promptId: prompt.promptId })}><td className="prompt-preview">{prompt.contentAvailable ? prompt.content : 'Prompt content storage disabled'}</td><td>{dateTime(prompt.startedAt)}</td><td>{exactIntegerText(prompt.modelRequestsExact, prompt.modelRequests)}</td><td>{exactIntegerText(prompt.toolCallsExact, prompt.toolCalls)}</td><td>{exactIntegerText(prompt.inputTokensExact, prompt.inputTokens)}</td><td>{(prompt.cacheReadRatio * 100).toFixed(1)}%</td><td>{exactIntegerText(prompt.outputTokensExact, prompt.outputTokens)}</td><td><span className="usage-credit">{credits(prompt.aiCredits)}</span><span className="usage-cost">{money(prompt.aiCostUsd).replace('+', '')}</span></td><td className="muted italic">Not attributed</td></tr>)}</tbody></table></div>;
}

export function SessionView({ experiment, scenario, calibration, navigate }: { experiment: string; scenario: ScenarioSelection; calibration?: SavedCalibration; navigate: Navigate }) {
  const apiScenario = scenario === 'custom' ? 'base' : scenario;
  const load = useLoad(() => Promise.all([api.session(experiment, apiScenario), api.prompts(experiment)]), [experiment, apiScenario]);
  if (load.loading) return <LoadingBand />;
  if (load.error || !load.data) return <ErrorBand message={load.error ?? 'Session unavailable'} retry={load.retry} />;
  const [sourceSession, prompts] = load.data;
  const customSession = calibration ? calibratedSession(sourceSession, calibration.draft, calibration.customScenario) : null;
  const session = scenario === 'custom' && customSession ? customSession : sourceSession;
  const sessionModelingStatus = session.modelingStatus;
  const modeledWithheld = sessionModelingStatus !== 'available';
  const selected = modeledWithheld ? null : session.scenarioResult;
  const allScenarios: Partial<Record<ScenarioSelection, ScenarioResult | null>> = {
    ...(sourceSession.modelingStatus === 'available' ? sourceSession.benchmark?.scenarios ?? {} : {}),
    ...(customSession ? {
      custom: customSession.modelingStatus === 'available' ? customSession.scenarioResult : null
    } : {})
  };
  const phaseUsage = session.usage.phases ?? {};
  const source = session.source as Record<string, number | string>;
  const engagedSeconds = sessionEngagedSeconds(session);
  return <><ViewHeader title="Session detail" subtitle={`${dateTime(session.startedAt)} to ${dateTime(session.completedAt)}. Compare manual-only and AI-assisted delivery before inspecting prompt evidence.`} breadcrumbs={[{ label: 'Overall', page: { name: 'overall' } }, { label: experiment }]} navigate={navigate} /><ActiveCalibrationNotice calibration={scenario === 'custom' ? calibration : undefined} /><ModelingStatusBand status={sessionModelingStatus} />
    <div className="kpi-grid comparison-kpi-grid"><Kpi label="Manual-only time" value={modeledWithheld ? '—' : elapsedMinutes(selected?.estimatedManualMinutes)} kind="modeled" icon={<FileCode2 size={15} />} help={metricHelp.manualOnlyTime} /><Kpi label="AI-assisted time" value={elapsedMinutes(engagedSeconds === null ? null : engagedSeconds / 60)} kind="observed" icon={<Clock3 size={15} />} help={metricHelp.aiAssistedTime} /><Kpi label="Time gained" value={modeledWithheld ? '—' : minutes(selected?.estimatedMinutesSaved)} kind="modeled" icon={<Gauge size={15} />} help={metricHelp.timeGained} /><Kpi label="Manual-only cost" value={modeledWithheld ? '—' : money(selected?.estimatedManualLaborCostUsd).replace('+', '')} kind="modeled" icon={<Coins size={15} />} help={metricHelp.manualOnlyCost} /><Kpi label="AI-assisted total" value={modeledWithheld ? '—' : money(selected?.estimatedAiAssistedTotalCostUsd).replace('+', '')} kind="modeled" icon={<Coins size={15} />} help={metricHelp.aiAssistedTotal} /><Kpi label="Realized net gain" value={modeledWithheld ? '—' : money(selected?.netValueUsd)} kind="modeled" icon={<BarChart3 size={15} />} help={metricHelp.realizedNetGain} /></div>
    <ComparisonStrip facts={[{ label: 'Break-even manual time', value: elapsedMinutes(selected?.breakEvenManualMinutes), kind: 'modeled', help: metricHelp.breakEvenManualTime }, { label: 'Session window', value: duration(session.durationSeconds), kind: 'observed', help: metricHelp.sessionWindow }, { label: 'Time reduction', value: percent(selected?.taskTimeReduction), kind: 'modeled', help: metricHelp.timeReduction }, { label: 'Observed AI usage', value: money(session.aiCostUsd).replace('+', ''), kind: 'observed', help: metricHelp.observedAiUsage }, { label: 'Delivery cost reduction', value: percent(selected?.modeledDeliveryCostReduction ?? undefined), kind: 'modeled', help: metricHelp.deliveryCostReduction }, { label: 'Realized labor benefit', value: money(selected?.estimatedBenefitUsd), kind: 'modeled', help: metricHelp.realizedLaborBenefit }, { label: 'Return / AI credit spend', value: ratio(selected?.roi), kind: 'modeled', help: metricHelp.returnOnAiCost }]} note="AI-assisted total combines the session's labor equivalent at the configured loaded rate with observed AI usage. Realized net gain applies capacity realization." />
    <div className="layout-grid"><section className="panel span-12"><PanelHeading title={`Prompts in this session (${prompts.length})`} subtitle="Ordered local prompt groups. Select a row to inspect its content and request-chain evidence." /><PromptChainTable prompts={prompts} experiment={experiment} navigate={navigate} /></section><section className="panel span-5"><PanelHeading title="Scenario sensitivity" /><ScenarioTable values={allScenarios} selected={scenario} customName={calibration?.name} /></section><section className="panel span-7"><PanelHeading title={`${scenarioLabel(scenario)} phase impact`} subtitle="Measured AI time and modeled minutes saved by phase." /><PhaseTable result={selected} usage={phaseUsage} /></section><section className="panel span-7"><PanelHeading title="Token and model evidence" /><TokenStrip session={session} /><DefinitionList values={[["Model requests", String(session.chatSpans)], ["Cache-read input", compact(session.tokens.cacheRead)], ["Uncached input", compact(session.tokens.uncachedInput)], ["Output / reasoning", `${compact(session.tokens.output)} / ${compact(session.tokens.reasoning)}`]]} /></section><section className="panel span-5"><PanelHeading title="Source evidence" subtitle={session.sourceEvidenceComplete ? 'Matched retained-source evidence for this session.' : 'Repository source is not inferred from central telemetry.'} /><DefinitionList values={session.sourceEvidenceComplete ? [['Evidence state', 'Source + OTel'], ['Files added / modified', `${source.filesAdded ?? 0} / ${source.filesModified ?? 0}`], ['Characters added / removed', `${source.charactersAdded ?? 0} / ${source.charactersRemoved ?? 0}`], ['Lines added / removed', `${source.linesAdded ?? 0} / ${source.linesRemoved ?? 0}`], ['Typing equivalent', modeledWithheld ? '—' : minutes(session.benchmark?.typingEquivalentMinutes)]] : [['Evidence state', 'Telemetry only'], ['Retained source', 'Unavailable'], ['Coding benefit', 'Zero (conservative)'], ['Observed evidence', 'Time, phases, tools, tokens, cost'], ['Reason', 'Standard Copilot OTel has no retained-source delta']]} /></section></div>
  </>;
}

function PhaseTable({ result, usage }: { result: ScenarioResult | null; usage: Session['usage']['phases'] }) {
  return <div className="phase-table">{phases.map((phase) => {
    const seconds = usage?.[phase]?.allocatedSeconds ?? 0;
    const saved = result?.phases?.[phase]?.estimatedMinutesSaved;
    const maxSeconds = Math.max(...phases.map((name) => usage?.[name]?.allocatedSeconds ?? 0), 1);
    return <div className="phase-row" key={phase}><span className="phase-name">{scenarioLabel(phase)}</span><span className="phase-track"><i style={{ width: `${seconds / maxSeconds * 100}%` }} /></span><span>{duration(seconds)}</span><strong className={(saved ?? 0) < 0 ? 'negative' : ''}>{minutes(saved)}</strong></div>;
  })}</div>;
}

function TokenStrip({ session }: { session: Session }) {
  const total = Math.max(session.tokens.cacheRead + session.tokens.uncachedInput + session.tokens.output + session.tokens.reasoning, 1);
  const values = [session.tokens.cacheRead, session.tokens.uncachedInput, session.tokens.output, session.tokens.reasoning];
  return <div className="token-strip">{values.map((value, index) => <span key={index} className={`token token-${index}`} style={{ width: `${value / total * 100}%` }} />)}</div>;
}

export function PromptsView({ experiment, scenario, calibration, navigate }: { experiment: string; scenario: ScenarioSelection; calibration?: SavedCalibration; navigate: Navigate }) {
  const apiScenario = scenario === 'custom' ? 'base' : scenario;
  const load = useLoad(() => Promise.all([api.prompts(experiment), api.session(experiment, apiScenario)]), [experiment, apiScenario]);
  if (load.loading) return <LoadingBand />;
  if (load.error || !load.data) return <ErrorBand message={load.error ?? 'Prompts unavailable'} retry={load.retry} />;
  const [prompts] = load.data;
  const totals = prompts.reduce((value, prompt) => {
    const input = exactInteger(prompt.inputTokensExact, prompt.inputTokens);
    const cacheRead = exactInteger(prompt.cacheReadTokensExact, prompt.cacheReadTokens);
    return {
      requests: value.requests + exactInteger(prompt.modelRequestsExact, prompt.modelRequests),
      tools: value.tools + exactInteger(prompt.toolCallsExact, prompt.toolCalls),
      uncached: value.uncached + (input > cacheRead ? input - cacheRead : 0n),
      credits: value.credits + prompt.aiCredits
    };
  }, { requests: 0n, tools: 0n, uncached: 0n, credits: 0 });
  return <><ViewHeader title="Prompts in this session" subtitle="Ordered local trace groups. Select a prompt to inspect content and evidence." breadcrumbs={[{ label: 'Overall', page: { name: 'overall' } }, { label: experiment, page: { name: 'session', experiment } }, { label: 'Prompts' }]} navigate={navigate} /><ActiveCalibrationNotice calibration={scenario === 'custom' ? calibration : undefined} /><div className="kpi-grid"><Kpi label="Prompt groups" value={String(prompts.length)} kind="observed" icon={<MessageSquareText size={15} />} /><Kpi label="Model requests" value={totals.requests.toString()} kind="observed" icon={<Search size={15} />} /><Kpi label="Tool calls" value={totals.tools.toString()} kind="observed" icon={<Wrench size={15} />} /><Kpi label="Uncached input" value={totals.uncached.toString()} kind="observed" icon={<Database size={15} />} /><Kpi label="Turn AI credits" value={credits(totals.credits)} kind="observed" icon={<Coins size={15} />} /><Kpi label="Prompt ROI coverage" value={`0 / ${prompts.length}`} kind="gap" icon={<Gauge size={15} />} /></div><div className="layout-grid"><section className="panel span-12"><PanelHeading title="Prompt chain" subtitle="Evidence is grouped from each user-request span until the next prompt." /><PromptChainTable prompts={prompts} experiment={experiment} navigate={navigate} /></section><section className="panel span-12 boundary-panel"><strong>Contract boundary</strong><p>Prompt groups and credits follow direct VS Code user turns when the matching local log exists, with OTel trace fallback otherwise. Dollar values use $0.01 per credit. Session ROI remains OTel-based; per-prompt ROI requires prompt-level phase and retained-source attribution.</p></section></div></>;
}

export function PromptView({ promptId, experiment, scenario, calibration, navigate }: { promptId: string; experiment: string; scenario: ScenarioSelection; calibration?: SavedCalibration; navigate: Navigate }) {
  const apiScenario = scenario === 'custom' ? 'base' : scenario;
  const load = useLoad(() => Promise.all([api.prompt(promptId), api.session(experiment, apiScenario)]), [promptId, experiment, apiScenario]);
  if (load.loading) return <LoadingBand />;
  if (load.error || !load.data) return <ErrorBand message={load.error ?? 'Prompt unavailable'} retry={load.retry} />;
  const [rawPrompt, sourceSession] = load.data;
  const prompt = {
    ...rawPrompt,
    ordinal: exactIntegerText(rawPrompt.ordinalExact, rawPrompt.ordinal),
    capturedContentLength: rawPrompt.capturedContentLengthExact,
    modelRequests: exactIntegerText(rawPrompt.modelRequestsExact, rawPrompt.modelRequests),
    toolCalls: exactIntegerText(rawPrompt.toolCallsExact, rawPrompt.toolCalls),
    inputTokens: rawPrompt.inputTokensExact,
    cacheReadTokens: rawPrompt.cacheReadTokensExact,
    outputTokens: rawPrompt.outputTokensExact,
    reasoningTokens: rawPrompt.reasoningTokensExact,
    models: rawPrompt.modelsExact
  };
  const session = scenario === 'custom' && calibration ? calibratedSession(sourceSession, calibration.draft, calibration.customScenario) : sourceSession;
  return <><ViewHeader title="Prompt detail" subtitle={`${dateTime(prompt.startedAt)}. ${prompt.modelRequests} model requests and ${prompt.toolCalls} tool calls are grouped under this request.`} breadcrumbs={[{ label: 'Overall', page: { name: 'overall' } }, { label: experiment, page: { name: 'session', experiment } }, { label: 'Prompts', page: { name: 'prompts', experiment } }, { label: `Prompt ${prompt.ordinal}` }]} navigate={navigate} /><ActiveCalibrationNotice calibration={scenario === 'custom' ? calibration : undefined} /><div className="kpi-grid"><Kpi label="Model requests" value={String(prompt.modelRequests)} kind="observed" icon={<Search size={15} />} /><Kpi label="Tool calls" value={String(prompt.toolCalls)} kind="observed" icon={<Wrench size={15} />} /><Kpi label="Input tokens" value={prompt.inputTokens} kind="observed" icon={<Database size={15} />} /><Kpi label="Cache read" value={`${(prompt.cacheReadRatio * 100).toFixed(1)}%`} kind="derived" icon={<Activity size={15} />} /><Kpi label="Output tokens" value={prompt.outputTokens} kind="observed" icon={<FileCode2 size={15} />} /><Kpi label="AI credits" value={credits(prompt.aiCredits)} kind="observed" icon={<Coins size={15} />} /></div><div className="layout-grid"><section className="panel span-8"><PanelHeading title="Prompt content" /><pre className="prompt-content">{prompt.contentAvailable ? prompt.content : 'Prompt content storage is disabled in value-model.local.json.'}</pre><p className="footnote">Captured request length: {prompt.capturedContentLength} characters including supplied editor/browser context when present.</p></section><section className="panel span-4"><PanelHeading title="ROI context" /><DefinitionList values={[["Prompt AI credits", credits(prompt.aiCredits)], ["Prompt dollar equivalent", money(prompt.aiCostUsd).replace('+', '')], ["Session OTel usage", money(session.aiCostUsd).replace('+', '')], ["Session return", ratio(session.scenarioResult?.roi)], ["Prompt return", 'Not attributed']]} /></section><section className="panel span-7"><PanelHeading title="Request-chain evidence" /><DefinitionList values={[["Models involved", Object.entries(prompt.models).map(([model, count]) => `${model} ×${count}`).join(', ') || 'Unknown'], ["Total input / cache read", `${prompt.inputTokens} / ${prompt.cacheReadTokens}`], ["Output / reasoning", `${prompt.outputTokens} / ${prompt.reasoningTokens}`], ["Tool calls before next prompt", String(prompt.toolCalls)], ["Usage source", prompt.usageSource === 'copilot_turn_log' ? 'VS Code turn log' : 'OTel trace fallback'], ["Observed AI usage", `${prompt.aiCredits.toFixed(6)} credits / $${prompt.aiCostUsd.toFixed(8)}`]]} /></section><section className="panel span-5"><PanelHeading title="What is needed for prompt ROI" /><div className="attribution-box"><Gauge size={24} /><h3>Prompt-level attribution</h3><p>Capture prompt phase evidence and before/after source snapshots, calculate retained prompt delta, then apply the same scenario model. Until then, only cost and session-level ROI context are defensible.</p></div></section></div></>;
}
