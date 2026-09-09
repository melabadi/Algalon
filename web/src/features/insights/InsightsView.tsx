import { Activity, AlertTriangle, ArrowDown, ArrowUp, BarChart3, ExternalLink, Info, Minus } from 'lucide-react';
import { api } from '../../api';
import { ErrorBand, Kpi, LoadingBand, PanelHeading } from '../../components/ui';
import { useLoad } from '../../hooks/useLoad';
import { compact, duration, money, percent } from '../../lib/format';
import type { InsightMetric, Insights } from '../../types';

const trendIcons = { improving: <ArrowDown size={11} />, worsening: <ArrowUp size={11} />, up: <ArrowUp size={11} />, down: <ArrowDown size={11} />, flat: <Minus size={11} /> } as const;
const trendLabels = { improving: 'improved', worsening: 'worsened', up: 'higher now', down: 'lower now', flat: 'about the same' } as const;

function insightValue(metric: InsightMetric, value: number | null): string {
  if (value === null) return '—';
  if (metric.unit === 'ratio') return percent(value);
  if (metric.unit === 'usd') return money(value, value < 0.01 ? 4 : 2).replace('+', '');
  if (metric.unit === 'seconds') return duration(value);
  if (metric.unit === 'tokens') return compact(value);
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function InsightTrend({ metric }: { metric: InsightMetric }) {
  if (!metric.trend || metric.previous === null) return null;
  return <span className={`insight-trend trend-${metric.trend}`}>{trendIcons[metric.trend]}{trendLabels[metric.trend]}</span>;
}

function insightSamples(metric: InsightMetric, integrityWithheld: boolean): string {
  if (integrityWithheld && metric.key !== 'session_usage_coverage') {
    return `${metric.aggregateSize} complete observations; value withheld by integrity gate`;
  }
  if (metric.aggregateSize === 0) return 'No eligible observations in selected range';
  if (metric.scope === 'portfolio') return `${metric.aggregateSize} eligible; all are included`;
  return `${metric.aggregateSize} eligible; median of latest ${Math.min(5, metric.aggregateSize)} settled sessions`;
}

const signalLabels: Record<InsightMetric['signal']['level'], string> = {
  none: 'No signal',
  low: 'Low',
  high: 'High',
  danger: 'Danger',
  insufficient: 'Not enough data',
  not_rated: 'Not rated',
  unavailable: 'No data'
};

function signalDetail(metric: InsightMetric, integrityWithheld: boolean): string {
  if (integrityWithheld && metric.key !== 'session_usage_coverage') return 'Value withheld by integrity gate';
  const signal = metric.signal;
  if (signal.level === 'not_rated') return 'No useful direction for this metric';
  if (signal.level === 'unavailable') return 'No current observation';
  if (signal.level === 'insufficient') return `${metric.aggregateSize} of ${signal.minimumSamples} observations`;
  const attention = insightValue(metric, signal.attentionBoundary);
  const danger = insightValue(metric, signal.dangerBoundary);
  if (signal.direction === 'minimum') return `Low below ${attention} · danger below ${danger}`;
  return `High above ${attention} · danger above ${danger}`;
}

function signalLabel(metric: InsightMetric, integrityWithheld: boolean): string {
  if (integrityWithheld && metric.key !== 'session_usage_coverage') return 'Withheld';
  return signalLabels[metric.signal.level];
}

function InsightMetricTable({ metrics, integrityWithheld }: { metrics: InsightMetric[]; integrityWithheld: boolean }) {
  const groups = Array.from(new Set(metrics.map((metric) => metric.category))).map((category) => ({
    category,
    metrics: metrics.filter((metric) => metric.category === category)
  }));
  return <div className="table-scroll"><table className="insight-table"><thead><tr><th>Metric</th><th>Current</th><th>Prior period</th><th>Signal</th><th>How to use it</th></tr></thead>{groups.map((group) => <tbody key={group.category}><tr className="insight-group-row"><th colSpan={5} scope="rowgroup">{group.category}<span>{group.metrics.length} {group.metrics.length === 1 ? 'metric' : 'metrics'}</span></th></tr>{group.metrics.map((metric) => <tr key={metric.key}><td className="insight-metric-cell"><strong>{metric.label}</strong><small>{metric.description}</small><small>{metric.scope} · {metric.evidence}</small><small className="insight-source">Source: {metric.reference.url ? <a href={metric.reference.url} target="_blank" rel="noreferrer">{metric.reference.label}<ExternalLink size={10} /></a> : metric.reference.label}</small></td><td className="metric-number insight-current-cell"><span className="insight-value">{insightValue(metric, metric.current)}</span><small>{insightSamples(metric, integrityWithheld)}</small></td><td className="metric-number insight-prior-cell"><span className="insight-value">{insightValue(metric, metric.previous)}</span><InsightTrend metric={metric} /></td><td className="insight-signal-cell"><span className={`insight-signal signal-${metric.signal.level}`}>{signalLabel(metric, integrityWithheld)}</span><small>{signalDetail(metric, integrityWithheld)}</small></td><td className="insight-action">{metric.action}</td></tr>)}</tbody>)}</table></div>;
}

export function InsightsView({ days }: { days: number }) {
  const load = useLoad(() => api.insights(days), [days]);
  if (load.loading) return <LoadingBand label="Loading session metrics…" />;
  if (load.error || !load.data) return <ErrorBand message={load.error ?? 'Insights unavailable'} retry={load.retry} />;
  const insights: Insights = load.data;
  return <>
    <header className="view-header"><h1>Session insights</h1><p>Operational measurements from authoritative settled-session usage over the last {days} days. Prompt retention does not affect these values.</p></header>
    {insights.evidenceHealth.message && <div className="insight-banner" role="status"><Info size={15} /><span>{insights.evidenceHealth.message}</span></div>}
    <div className="kpi-grid insight-kpi-grid"><Kpi label="Settled sessions" value={String(insights.evidenceHealth.eligibleSessions)} kind="observed" icon={<Activity size={15} />} /><Kpi label="Complete usage" value={`${insights.evidenceHealth.completeSessions} / ${insights.evidenceHealth.eligibleSessions}`} kind={insights.evidenceHealth.degraded ? 'action' : 'derived'} icon={insights.evidenceHealth.degraded ? <AlertTriangle size={15} /> : <Info size={15} />} badgeLabel={insights.evidenceHealth.sessionUsageCoverage === null ? 'none' : percent(insights.evidenceHealth.sessionUsageCoverage)} /><Kpi label="OTel usage" value={String(insights.evidenceHealth.otelUsageSessions)} kind="observed" icon={<BarChart3 size={15} />} /><Kpi label="Direct usage" value={String(insights.evidenceHealth.directUsageSessions)} kind="observed" icon={<Info size={15} />} /></div>
    <section className="insight-reading-band" aria-label="How to read these measurements"><div><strong>Integrity gate</strong><span>Every settled session must carry the complete authoritative usage contract before Insights are shown.</span></div><div><strong>Current value</strong><span>{insights.zoneMethod.baselineRule}</span></div><div><strong>Source independence</strong><span>Complete OTel and complete direct-turn session usage are both authoritative; prompt indexing is not used.</span></div></section>
    <div className="layout-grid"><section className="panel span-12"><PanelHeading title="Metrics and signals" subtitle="Grouped by operational area and calculated only from authoritative session usage. Signal boundaries are product-owned triage defaults." /><InsightMetricTable metrics={insights.metrics} integrityWithheld={insights.evidenceHealth.degraded} /></section></div>
  </>;
}
