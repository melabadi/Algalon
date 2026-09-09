import type { Prompt, Session } from '../types';
import { exactInteger } from './format';

export type SessionSort = 'startedAt' | 'latestMessageAt' | 'experiment' | 'promptCount' | 'engagedSeconds' | 'aiCostUsd' | 'estimatedMinutesSaved' | 'netValueUsd' | 'roi' | 'sourceEvidenceComplete';
export type PromptSort = 'content' | 'startedAt' | 'modelRequests' | 'toolCalls' | 'inputTokens' | 'cacheReadRatio' | 'outputTokens' | 'aiCredits' | 'roi';
export type SortDirection = 'ascending' | 'descending';

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

function compareValues(
  left: bigint | number | string | null,
  right: bigint | number | string | null,
  direction: SortDirection,
  fallback: () => number
): number {
  if (left === null && right !== null) return 1;
  if (left !== null && right === null) return -1;
  if (left === null || right === null) return fallback();
  const comparison = typeof left === 'string' && typeof right === 'string'
    ? collator.compare(left, right)
    : typeof left === 'bigint' && typeof right === 'bigint'
      ? left < right ? -1 : left > right ? 1 : 0
      : Number(left) - Number(right);
  return (direction === 'ascending' ? comparison : -comparison) || fallback();
}

function sessionSortValue(session: Session, sort: SessionSort): number | string | null {
  if (sort === 'startedAt') return Date.parse(session.startedAt);
  if (sort === 'latestMessageAt') return session.latestMessageAt ? Date.parse(session.latestMessageAt) : null;
  if (sort === 'estimatedMinutesSaved') return session.scenarioResult?.estimatedMinutesSaved ?? null;
  if (sort === 'netValueUsd') return session.scenarioResult?.netValueUsd ?? null;
  if (sort === 'roi') return session.scenarioResult?.roi ?? null;
  if (sort === 'sourceEvidenceComplete') return session.sourceEvidenceComplete ? 1 : 0;
  if (sort === 'engagedSeconds') {
    const value = session.usage.engagedSeconds;
    return value !== undefined && Number.isFinite(value) && value >= 0 ? value : null;
  }
  return session[sort];
}

export function compareSessions(
  left: Session,
  right: Session,
  sort: SessionSort,
  direction: SortDirection
): number {
  return compareValues(
    sessionSortValue(left, sort),
    sessionSortValue(right, sort),
    direction,
    () => collator.compare(left.experiment, right.experiment)
  );
}

function promptSortValue(prompt: Prompt, sort: PromptSort): bigint | number | string | null {
  if (sort === 'content') return prompt.contentAvailable ? prompt.content : null;
  if (sort === 'startedAt') return Date.parse(prompt.startedAt);
  if (sort === 'modelRequests') return exactInteger(prompt.modelRequestsExact, prompt.modelRequests);
  if (sort === 'toolCalls') return exactInteger(prompt.toolCallsExact, prompt.toolCalls);
  if (sort === 'inputTokens') return exactInteger(prompt.inputTokensExact, prompt.inputTokens);
  if (sort === 'outputTokens') return exactInteger(prompt.outputTokensExact, prompt.outputTokens);
  return prompt[sort];
}

export function comparePrompts(
  left: Prompt,
  right: Prompt,
  sort: PromptSort,
  direction: SortDirection
): number {
  return compareValues(
    promptSortValue(left, sort),
    promptSortValue(right, sort),
    direction,
    () => {
      const leftOrdinal = exactInteger(left.ordinalExact, left.ordinal);
      const rightOrdinal = exactInteger(right.ordinalExact, right.ordinal);
      return leftOrdinal < rightOrdinal ? -1 : leftOrdinal > rightOrdinal ? 1 : 0;
    }
  );
}
