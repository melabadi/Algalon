import { useId, type ReactNode } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown, Info, RefreshCw } from 'lucide-react';
import { messages } from '../i18n';
import type { SortDirection } from '../lib/sorting';

export type EvidenceKind = 'observed' | 'derived' | 'modeled' | 'gap' | 'watch' | 'action';

export function SortableHeader<Sort extends string>({ label, sort, activeSort, direction, defaultDirection = 'descending', onSort }: {
  label: string;
  sort: Sort;
  activeSort: Sort;
  direction: SortDirection;
  defaultDirection?: SortDirection;
  onSort: (sort: Sort) => void;
}) {
  const active = sort === activeSort;
  const nextDirection = active
    ? direction === 'descending' ? 'ascending' : 'descending'
    : defaultDirection;
  return (
    <th aria-sort={active ? direction : 'none'}>
      <button type="button" className={`sortable-header${active ? ' active' : ''}`} onClick={() => onSort(sort)} aria-label={messages.a11y.sortBy(label, nextDirection)}>
        <span>{label}</span>
        {active ? direction === 'ascending' ? <ArrowUp size={13} /> : <ArrowDown size={13} /> : <ArrowUpDown size={13} />}
      </button>
    </th>
  );
}

export function Badge({ kind, children }: { kind: EvidenceKind; children: ReactNode }) {
  return <span className={`badge badge-${kind}`} title={messages.badges[kind]}>{children}</span>;
}

export function MetricTooltip({ label, children }: { label: string; children: string }) {
  const id = useId();
  return (
    <span className="metric-tooltip">
      <button type="button" className="metric-tooltip-trigger" aria-label={messages.a11y.explain(label)} aria-describedby={id}><Info size={12} /></button>
      <span className="metric-tooltip-content" id={id} role="tooltip">{children}</span>
    </span>
  );
}

export function Kpi({ label, value, kind, icon, help, badgeLabel }: { label: string; value: string; kind: EvidenceKind; icon: ReactNode; help?: string; badgeLabel?: string }) {
  const negative = value.startsWith('-') || value.startsWith('$-');
  return (
    <div className="kpi">
      <div className="kpi-label">{icon}<span>{label}</span>{help && <MetricTooltip label={label}>{help}</MetricTooltip>}</div>
      <div className={`kpi-value value-${negative ? 'negative' : kind}`}>{value}</div>
      <Badge kind={kind}>{badgeLabel ?? (kind === 'gap' ? messages.state.attributionGap : kind)}</Badge>
    </div>
  );
}

export function ComparisonStrip({ facts, note }: { facts: Array<{ label: string; value: string; kind: 'observed' | 'derived' | 'modeled'; help?: string }>; note: string }) {
  return (
    <section className="comparison-strip" aria-label={messages.a11y.comparisonEvidence}>
      {facts.map((fact) => {
        const negative = fact.value.startsWith('-') || fact.value.startsWith('$-');
        return <div className="comparison-fact" key={fact.label}><span className="comparison-label">{fact.label}{fact.help && <MetricTooltip label={fact.label}>{fact.help}</MetricTooltip>}</span><strong className={`value-${negative ? 'negative' : fact.kind}`}>{fact.value}</strong></div>;
      })}
      <p className="comparison-note">{note}</p>
    </section>
  );
}

export function LoadingBand({ label = messages.state.loadingEvidence }: { label?: string }) {
  return <div className="state-band"><span className="spinner" />{label}</div>;
}

export function ErrorBand({ message, retry }: { message: string; retry: () => void }) {
  return <div className="state-band state-error" role="alert"><span>{messages.state.unableToLoadEvidence(message)}</span><button className="command-button" onClick={retry}><RefreshCw size={15} />{messages.controls.retryNow}</button></div>;
}

export function PanelHeading({ title, subtitle }: { title: string; subtitle?: string }) {
  return <div className="panel-heading"><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>;
}

export function DefinitionList({ values }: { values: Array<[string, string]> }) {
  return <dl className="definition-list">{values.map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}</dl>;
}
