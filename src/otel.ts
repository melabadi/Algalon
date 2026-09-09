export type ToolCategory = 'coding_proxy' | 'research_proxy' | 'planning_proxy' | 'unmapped';
export type EditDecision = 'accepted' | 'rejected' | 'unmapped';

export interface OtelCollectionOptions {
  victoriaMetricsUrl: string;
  startTime?: string;
  codingToolPatterns: string[];
  researchToolPatterns: string[];
  planningToolPatterns: string[];
  acceptedDecisionValues: string[];
  rejectedDecisionValues: string[];
}

import type { OtelSnapshot } from './schema.js';
import { normalizeIdentifier } from './normalize.js';

interface MeasurementWindow {
  startSeconds: number;
  endSeconds: number;
  durationSeconds: number;
}

interface QueryResult {
  metric: Record<string, string>;
  value: [number, string];
}

interface QueryResponse {
  status: string;
  data?: {
    result?: QueryResult[];
  };
  error?: string;
}

interface SeriesResponse {
  status: string;
  data?: Array<Record<string, string>>;
  error?: string;
}

interface SeriesIncrease {
  labels: Record<string, string>;
  value: number;
}

function containsPattern(value: string, patterns: string[]): boolean {
  const normalized = normalizeIdentifier(value);
  return patterns.some((pattern) => normalized.includes(normalizeIdentifier(pattern)));
}

export function classifyToolName(
  toolName: string,
  codingPatterns: string[],
  researchPatterns: string[],
  planningPatterns: string[]
): ToolCategory {
  if (containsPattern(toolName, codingPatterns)) {
    return 'coding_proxy';
  }
  if (containsPattern(toolName, planningPatterns)) {
    return 'planning_proxy';
  }
  if (containsPattern(toolName, researchPatterns)) {
    return 'research_proxy';
  }
  return 'unmapped';
}

export function classifyEditDecision(
  labels: Record<string, string>,
  acceptedValues: string[],
  rejectedValues: string[]
): EditDecision {
  const outcomeEntries = Object.entries(labels).filter(([key]) =>
    /(decision|outcome|action|accepted|result|kind)/i.test(key)
  );

  for (const [key, value] of outcomeEntries) {
    const normalizedKey = normalizeIdentifier(key);
    const normalizedValue = normalizeIdentifier(value);
    if (normalizedKey.includes('accepted')) {
      if (normalizedValue === 'true' || normalizedValue === '1') {
        return 'accepted';
      }
      if (normalizedValue === 'false' || normalizedValue === '0') {
        return 'rejected';
      }
    }
  }

  const values = outcomeEntries.map(([, value]) => value);
  if (values.some((value) => containsPattern(value, rejectedValues))) {
    return 'rejected';
  }
  if (values.some((value) => containsPattern(value, acceptedValues))) {
    return 'accepted';
  }
  return 'unmapped';
}

export function measurementWindow(day: string, now = new Date()): MeasurementWindow {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) {
    throw new Error(`Invalid measurement day '${day}'.`);
  }
  const startMilliseconds = Date.parse(`${day}T00:00:00Z`);
  const tomorrowMilliseconds = startMilliseconds + 86_400_000;
  const endMilliseconds = Math.min(tomorrowMilliseconds, now.getTime());
  if (!Number.isFinite(startMilliseconds) || endMilliseconds <= startMilliseconds) {
    throw new Error(`Measurement day '${day}' is in the future.`);
  }
  const startSeconds = Math.floor(startMilliseconds / 1000);
  const endSeconds = Math.floor(endMilliseconds / 1000);
  return {
    startSeconds,
    endSeconds,
    durationSeconds: Math.max(1, endSeconds - startSeconds)
  };
}

export function measurementWindowSince(startTime: string, now = new Date()): MeasurementWindow {
  const startMilliseconds = Date.parse(startTime);
  const endMilliseconds = now.getTime();
  if (!Number.isFinite(startMilliseconds) || startMilliseconds >= endMilliseconds) {
    throw new Error(`Invalid experiment start time '${startTime}'.`);
  }
  const startSeconds = Math.floor(startMilliseconds / 1000);
  const endSeconds = Math.floor(endMilliseconds / 1000);
  return {
    startSeconds,
    endSeconds,
    durationSeconds: Math.max(1, endSeconds - startSeconds)
  };
}

function escapeLabelValue(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/"/g, '\\"');
}

function exactSelector(metricName: string, labels: Record<string, string>): string {
  const matchers = Object.entries(labels)
    .filter(([key]) => key !== '__name__')
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}="${escapeLabelValue(value)}"`);
  return matchers.length > 0 ? `${metricName}{${matchers.join(',')}}` : metricName;
}

async function requestJson<T>(url: URL): Promise<T> {
  const response = await fetch(url, { signal: AbortSignal.timeout(10_000) });
  if (!response.ok) {
    throw new Error(`VictoriaMetrics request failed (${response.status}): ${(await response.text()).slice(0, 500)}`);
  }
  return response.json() as Promise<T>;
}

async function query(
  victoriaMetricsUrl: string,
  expression: string,
  timeSeconds: number
): Promise<QueryResult[]> {
  const url = new URL('/prometheus/api/v1/query', victoriaMetricsUrl);
  url.searchParams.set('query', expression);
  url.searchParams.set('time', String(timeSeconds));
  const response = await requestJson<QueryResponse>(url);
  if (response.status !== 'success') {
    throw new Error(`VictoriaMetrics query failed: ${response.error ?? 'unknown error'}`);
  }
  return response.data?.result ?? [];
}

async function scalarQuery(
  victoriaMetricsUrl: string,
  expression: string,
  timeSeconds: number
): Promise<number> {
  const results = await query(victoriaMetricsUrl, expression, timeSeconds);
  return results.reduce((total, result) => {
    const value = Number(result.value[1]);
    return Number.isFinite(value) ? total + value : total;
  }, 0);
}

async function counterIncrease(
  victoriaMetricsUrl: string,
  metricName: string,
  window: MeasurementWindow
): Promise<number> {
  return scalarQuery(
    victoriaMetricsUrl,
    `sum(increase(${metricName}[${window.durationSeconds}s]))`,
    window.endSeconds
  );
}

async function series(
  victoriaMetricsUrl: string,
  metricName: string,
  window: MeasurementWindow
): Promise<Array<Record<string, string>>> {
  const url = new URL('/prometheus/api/v1/series', victoriaMetricsUrl);
  url.searchParams.append('match[]', metricName);
  url.searchParams.set('start', String(window.startSeconds));
  url.searchParams.set('end', String(window.endSeconds));
  const response = await requestJson<SeriesResponse>(url);
  if (response.status !== 'success') {
    throw new Error(`VictoriaMetrics series query failed: ${response.error ?? 'unknown error'}`);
  }
  return response.data ?? [];
}

async function seriesIncreases(
  victoriaMetricsUrl: string,
  metricName: string,
  window: MeasurementWindow
): Promise<SeriesIncrease[]> {
  const labelSets = await series(victoriaMetricsUrl, metricName, window);
  return Promise.all(labelSets.map(async (labels) => ({
    labels,
    value: await scalarQuery(
      victoriaMetricsUrl,
      `sum(increase(${exactSelector(metricName, labels)}[${window.durationSeconds}s]))`,
      window.endSeconds
    )
  })));
}

async function histogramAverage(
  victoriaMetricsUrl: string,
  metricName: string,
  window: MeasurementWindow
): Promise<number | null> {
  const [sum, count] = await Promise.all([
    counterIncrease(victoriaMetricsUrl, `${metricName}_sum`, window),
    counterIncrease(victoriaMetricsUrl, `${metricName}_count`, window)
  ]);
  if (count <= 0) {
    return null;
  }
  return Math.max(0, Math.min(1, sum / count));
}

function isSuccessful(labels: Record<string, string>): boolean {
  const success = labels.success;
  if (success !== undefined) {
    return normalizeIdentifier(success) === 'true' || success === '1';
  }
  const statusCode = labels.status_code ?? labels.otel_status_code;
  return statusCode === undefined || !normalizeIdentifier(statusCode).includes('error');
}

function toolName(labels: Record<string, string>): string {
  return labels.gen_ai_tool_name ?? labels.tool_name ?? labels.span_name?.replace(/^execute_tool\s+/i, '') ?? 'unknown';
}

function isToolSpanSeries(labels: Record<string, string>): boolean {
  return normalizeIdentifier(labels.gen_ai_operation_name ?? '') === 'execute_tool' ||
    normalizeIdentifier(labels.span_name ?? '').startsWith('execute_tool');
}

function aggregateToolSeries(series: SeriesIncrease[]): Map<string, SeriesIncrease> {
  const aggregated = new Map<string, SeriesIncrease>();
  for (const item of series) {
    const key = `${normalizeIdentifier(toolName(item.labels))}|${isSuccessful(item.labels) ? 'success' : 'failure'}`;
    const current = aggregated.get(key);
    aggregated.set(key, {
      labels: current?.labels ?? item.labels,
      value: (current?.value ?? 0) + item.value
    });
  }
  return aggregated;
}

function mergeToolSeries(nativeSeries: SeriesIncrease[], traceSeries: SeriesIncrease[]): SeriesIncrease[] {
  const nativeTotals = aggregateToolSeries(nativeSeries);
  const traceTotals = aggregateToolSeries(traceSeries.filter((item) =>
    isToolSpanSeries(item.labels) && item.labels.service_name !== 'copilot-value-validation'
  ));
  return [...new Set([...nativeTotals.keys(), ...traceTotals.keys()])].map((key) => {
    const nativeItem = nativeTotals.get(key);
    const traceItem = traceTotals.get(key);
    if (!nativeItem) return traceItem as SeriesIncrease;
    if (!traceItem) return nativeItem;
    return nativeItem.value >= traceItem.value ? nativeItem : traceItem;
  });
}

export async function collectOtelSnapshot(
  day: string,
  options: OtelCollectionOptions,
  now = new Date()
): Promise<OtelSnapshot> {
  const window = options.startTime
    ? measurementWindowSince(options.startTime, now)
    : measurementWindow(day, now);
  const [
    sessions,
    agentInvocations,
    agentTurns,
    nativeToolSeries,
    traceSpanSeries,
    editDecisionSeries,
    locSeries,
    actionSeries,
    editSurvivalFourGram,
    editSurvivalNoRevert
  ] = await Promise.all([
    counterIncrease(options.victoriaMetricsUrl, 'copilot_chat_session_count', window),
    counterIncrease(options.victoriaMetricsUrl, 'copilot_chat_agent_invocation_duration_count', window),
    counterIncrease(options.victoriaMetricsUrl, 'copilot_chat_agent_turn_count_sum', window),
    seriesIncreases(options.victoriaMetricsUrl, 'copilot_chat_tool_call_count', window),
    seriesIncreases(options.victoriaMetricsUrl, 'copilot_trace_calls', window),
    seriesIncreases(options.victoriaMetricsUrl, 'copilot_chat_edit_acceptance_count', window),
    seriesIncreases(options.victoriaMetricsUrl, 'copilot_chat_lines_of_code_count', window),
    seriesIncreases(options.victoriaMetricsUrl, 'copilot_chat_user_action_count', window),
    histogramAverage(options.victoriaMetricsUrl, 'copilot_chat_edit_survival_four_gram', window),
    histogramAverage(options.victoriaMetricsUrl, 'copilot_chat_edit_survival_no_revert', window)
  ]);
  const toolSeries = mergeToolSeries(nativeToolSeries, traceSpanSeries);

  let toolCalls = 0;
  let successfulToolCalls = 0;
  let codingToolCalls = 0;
  let researchToolCalls = 0;
  let planningToolCalls = 0;
  let unmappedToolCalls = 0;
  for (const item of toolSeries) {
    toolCalls += item.value;
    if (!isSuccessful(item.labels)) {
      continue;
    }
    successfulToolCalls += item.value;
    const category = classifyToolName(
      toolName(item.labels),
      options.codingToolPatterns,
      options.researchToolPatterns,
      options.planningToolPatterns
    );
    if (category === 'coding_proxy') {
      codingToolCalls += item.value;
    } else if (category === 'research_proxy') {
      researchToolCalls += item.value;
    } else if (category === 'planning_proxy') {
      planningToolCalls += item.value;
    } else {
      unmappedToolCalls += item.value;
    }
  }

  let acceptedEditDecisions = 0;
  let rejectedEditDecisions = 0;
  let unmappedEditDecisions = 0;
  for (const item of editDecisionSeries) {
    const decision = classifyEditDecision(
      item.labels,
      options.acceptedDecisionValues,
      options.rejectedDecisionValues
    );
    if (decision === 'accepted') {
      acceptedEditDecisions += item.value;
    } else if (decision === 'rejected') {
      rejectedEditDecisions += item.value;
    } else {
      unmappedEditDecisions += item.value;
    }
  }

  const agentEditLoc = locSeries.reduce((total, item) => total + item.value, 0);
  const appliedUserActions = actionSeries.reduce((total, item) => {
    const action = Object.entries(item.labels).find(([key]) => /action/i.test(key))?.[1] ?? '';
    return containsPattern(action, ['apply', 'insert', 'copy']) ? total + item.value : total;
  }, 0);
  const active = sessions + agentInvocations + toolCalls + acceptedEditDecisions + agentEditLoc > 0;

  return {
    day,
    activeDay: active ? 1 : 0,
    sessions,
    agentInvocations,
    agentTurns,
    toolCalls,
    successfulToolCalls,
    codingToolCalls,
    researchToolCalls,
    planningToolCalls,
    unmappedToolCalls,
    acceptedEditDecisions,
    rejectedEditDecisions,
    unmappedEditDecisions,
    agentEditLoc,
    appliedUserActions,
    editSurvivalFourGram,
    editSurvivalNoRevert
  };
}