import { useId, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { matchingVariable, type VariableDefinition } from './variables';

function VariableToken({ display, variable }: { display: string; variable: VariableDefinition }) {
  const tooltipId = useId();
  const [position, setPosition] = useState<{ top: number; left: number; width: number; above: boolean } | null>(null);
  const description = `${display}: ${variable.definition} Unit: ${variable.unit}. Evidence: ${variable.source}.`;
  const showTooltip = (element: HTMLElement) => {
    const bounds = element.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 24);
    const left = Math.min(Math.max(12, bounds.left), window.innerWidth - width - 12);
    const above = window.innerHeight - bounds.bottom < 150 && bounds.top > 150;
    setPosition({ top: above ? bounds.top - 8 : bounds.bottom + 8, left, width, above });
  };
  return <>
    <span
      className="formula-variable"
      role="term"
      tabIndex={0}
      aria-label={description}
      aria-describedby={position ? tooltipId : undefined}
      onMouseEnter={(event) => showTooltip(event.currentTarget)}
      onMouseLeave={(event) => { if (document.activeElement !== event.currentTarget) setPosition(null); }}
      onFocus={(event) => showTooltip(event.currentTarget)}
      onBlur={() => setPosition(null)}
      onKeyDown={(event) => { if (event.key === 'Escape') event.currentTarget.blur(); }}
    >{display}</span>
    {position && createPortal(
      <span id={tooltipId} className={`formula-variable-tooltip${position.above ? ' above' : ''}`} role="tooltip" style={{ top: position.top, left: position.left, width: position.width }}>
        <strong>{display}</strong>
        <span className="formula-variable-meta">{variable.unit} · {variable.source}</span>
        <span>{variable.definition}</span>
      </span>,
      document.body
    )}
  </>;
}

export function Formula({ expression }: { expression: string }) {
  const content: ReactNode[] = [];
  let textStart = 0;
  let index = 0;
  while (index < expression.length) {
    const match = matchingVariable(expression, index);
    if (!match) {
      index += 1;
      continue;
    }
    const [symbol, variable] = match;
    if (index > textStart) content.push(expression.slice(textStart, index));
    content.push(<VariableToken key={`${index}-${symbol}`} display={symbol} variable={variable} />);
    index += symbol.length;
    textStart = index;
  }
  if (textStart < expression.length) content.push(expression.slice(textStart));
  return <code>{content.length ? content : expression}</code>;
}
