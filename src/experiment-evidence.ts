import {
  calculatePhaseEvidence,
  type PhaseToolPatterns,
  type TimedPhaseSpan
} from './phase-evidence.js';
import type {
  ExperimentUsage,
  ModelUsage,
  TraceSession,
  TraceSessionEvidence
} from './experiment-types.js';
import { readStableJsonLines } from './trace-archive.js';

export {
  collectDirectTurnUsage,
  directTurnLogSignature,
  reuseDirectTurnUsage
} from './direct-turns.js';
export { collectSourceStats } from './source-stats.js';
export { readStableTraceArchive } from './trace-archive.js';
export type {
  ExperimentUsage,
  ModelUsage,
  TraceSession,
  TraceSessionEvidence
} from './experiment-types.js';
export type { SourceStats } from './source-stats.js';

interface OtelAttributeValue {
  stringValue?: string;
  intValue?: string | number;
  doubleValue?: number;
  boolValue?: boolean;
}

interface OtelAttribute {
  key?: string;
  value?: OtelAttributeValue;
}

interface OtelSpan {
  traceId?: string;
  spanId?: string;
  name?: string;
  startTimeUnixNano?: string;
  endTimeUnixNano?: string;
  attributes?: OtelAttribute[];
}

interface OtelRecord {
  resourceSpans?: Array<{
    resource?: { attributes?: OtelAttribute[] };
    scopeSpans?: Array<{ spans?: OtelSpan[] }>;
  }>;
}

interface MutableUsage {
  requests: number;
  inputTokens: number;
  cacheReadTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  nanoAiCredits: number;
}

interface MutableEvidence {
  seenSpans: Set<string>;
  seenTimedSpans: Set<string>;
  timedSpans: TimedPhaseSpan[];
  models: Map<string, MutableUsage>;
}

interface MutableSessionEvidence extends MutableEvidence {
  startedMilliseconds: number;
  endedMilliseconds: number;
  chatSessionIds: Set<string>;
}

export interface SerializedSessionEvidence {
  startedMilliseconds: number;
  endedMilliseconds: number;
  chatSessionIds: string[];
  seenSpans: string[];
  seenTimedSpans: string[];
  timedSpans: TimedPhaseSpan[];
  models: Record<string, MutableUsage>;
}

export interface SerializedTraceEvidence {
  version: 1;
  latestSpanTimestampMilliseconds: number;
  sessions: Record<string, SerializedSessionEvidence>;
}

const sourceExtensions = new Set(['.css', '.html', '.js', '.jsx', '.svg', '.ts', '.tsx']);
const excludedDirectories = new Set(['.copilot-value', '.git', 'coverage', 'dist', 'node_modules']);
const excludedFiles = new Set(['package-lock.json', 'pnpm-lock.yaml', 'yarn.lock']);

function attributeValue(attributes: OtelAttribute[] | undefined, key: string): OtelAttributeValue | undefined {
  return attributes?.find((attribute) => attribute.key === key)?.value;
}

function stringAttribute(attributes: OtelAttribute[] | undefined, key: string): string | undefined {
  return attributeValue(attributes, key)?.stringValue;
}

function numberAttribute(attributes: OtelAttribute[] | undefined, key: string): number {
  const value = attributeValue(attributes, key);
  const candidate = value?.intValue ?? value?.doubleValue ?? value?.stringValue ?? 0;
  const number = Number(candidate);
  return Number.isFinite(number) ? number : 0;
}

function spanStartMilliseconds(span: OtelSpan): number {
  try {
    return Number(BigInt(span.startTimeUnixNano ?? '0') / 1_000_000n);
  } catch {
    return 0;
  }
}

function spanEndMilliseconds(span: OtelSpan): number {
  try {
    return Number(BigInt(span.endTimeUnixNano ?? span.startTimeUnixNano ?? '0') / 1_000_000n);
  } catch {
    return spanStartMilliseconds(span);
  }
}

function createMutableEvidence(): MutableEvidence {
  return {
    seenSpans: new Set(),
    seenTimedSpans: new Set(),
    timedSpans: [],
    models: new Map()
  };
}

function collectSpanEvidence(evidence: MutableEvidence, span: OtelSpan): void {
  const startedAt = spanStartMilliseconds(span);
  const endedAt = spanEndMilliseconds(span);
  const identity = span.spanId ?? `${span.traceId ?? ''}|${span.name}|${span.startTimeUnixNano ?? ''}`;
  if (!evidence.seenTimedSpans.has(identity)) {
    evidence.seenTimedSpans.add(identity);
    const inputTokens = numberAttribute(span.attributes, 'gen_ai.usage.input_tokens');
    const cacheReadTokens = numberAttribute(span.attributes, 'gen_ai.usage.cache_read.input_tokens');
    evidence.timedSpans.push({
      identity,
      name: span.name ?? 'unknown',
      toolName: stringAttribute(span.attributes, 'gen_ai.tool.name'),
      startMilliseconds: startedAt,
      endMilliseconds: endedAt,
      nanoAiCredits: numberAttribute(span.attributes, 'copilot_chat.copilot_usage_nano_aiu'),
      uncachedInputTokens: Math.max(0, inputTokens - cacheReadTokens),
      outputTokens: numberAttribute(span.attributes, 'gen_ai.usage.output_tokens'),
      reasoningTokens: numberAttribute(span.attributes, 'gen_ai.usage.reasoning_tokens')
    });
  }
  if (!span.name?.startsWith('chat ') || evidence.seenSpans.has(identity)) return;
  evidence.seenSpans.add(identity);

  const model = stringAttribute(span.attributes, 'gen_ai.request.model') ??
    stringAttribute(span.attributes, 'gen_ai.response.model') ?? 'unknown';
  const usage = evidence.models.get(model) ?? {
    requests: 0,
    inputTokens: 0,
    cacheReadTokens: 0,
    outputTokens: 0,
    reasoningTokens: 0,
    nanoAiCredits: 0
  };
  usage.requests += 1;
  usage.inputTokens += numberAttribute(span.attributes, 'gen_ai.usage.input_tokens');
  usage.cacheReadTokens += numberAttribute(span.attributes, 'gen_ai.usage.cache_read.input_tokens');
  usage.outputTokens += numberAttribute(span.attributes, 'gen_ai.usage.output_tokens');
  usage.reasoningTokens += numberAttribute(span.attributes, 'gen_ai.usage.reasoning_tokens');
  usage.nanoAiCredits += numberAttribute(span.attributes, 'copilot_chat.copilot_usage_nano_aiu');
  evidence.models.set(model, usage);
}

function usageFromEvidence(
  evidence: MutableEvidence,
  startMilliseconds: number,
  endMilliseconds: number,
  phasePatterns: PhaseToolPatterns,
  maxIdleGapSeconds?: number
): ExperimentUsage {
  const modelUsage = [...evidence.models.entries()].map(([model, usage]): ModelUsage => {
    const aiCredits = usage.nanoAiCredits / 1_000_000_000;
    return {
      model,
      requests: usage.requests,
      inputTokens: usage.inputTokens,
      cacheReadTokens: usage.cacheReadTokens,
      uncachedInputTokens: Math.max(0, usage.inputTokens - usage.cacheReadTokens),
      outputTokens: usage.outputTokens,
      reasoningTokens: usage.reasoningTokens,
      aiCredits,
      aiCostUsd: aiCredits / 100
    };
  }).sort((left, right) => left.model.localeCompare(right.model));
  const allocation = calculatePhaseEvidence(
    evidence.timedSpans,
    startMilliseconds,
    endMilliseconds,
    phasePatterns,
    maxIdleGapSeconds
  );

  const aiCredits = modelUsage.reduce((total, usage) => total + usage.aiCredits, 0);
  return {
    source: 'otel_traces',
    chatSpans: modelUsage.reduce((total, usage) => total + usage.requests, 0),
    inputTokens: modelUsage.reduce((total, usage) => total + usage.inputTokens, 0),
    cacheReadTokens: modelUsage.reduce((total, usage) => total + usage.cacheReadTokens, 0),
    uncachedInputTokens: modelUsage.reduce((total, usage) => total + usage.uncachedInputTokens, 0),
    outputTokens: modelUsage.reduce((total, usage) => total + usage.outputTokens, 0),
    reasoningTokens: modelUsage.reduce((total, usage) => total + usage.reasoningTokens, 0),
    aiCredits,
    aiCostUsd: aiCredits / 100,
    models: modelUsage,
    phases: allocation.phases,
    elapsedSeconds: allocation.elapsedSeconds,
    engagedSeconds: allocation.engagedSeconds,
    activeSeconds: allocation.activeSeconds,
    activityDensity: allocation.activityDensity
  };
}

function summarizeRecords(
  records: Iterable<OtelRecord>,
  start: Date,
  end: Date,
  phasePatterns: PhaseToolPatterns,
  sessionId?: string,
  maxIdleGapSeconds?: number
): ExperimentUsage {
  const startMilliseconds = start.getTime();
  const endMilliseconds = end.getTime();
  if (!Number.isFinite(startMilliseconds) || !Number.isFinite(endMilliseconds) || startMilliseconds >= endMilliseconds) {
    throw new Error('Experiment trace window is invalid.');
  }

  const evidence = createMutableEvidence();

  for (const record of records) collectWindowEvidenceRecord(
    evidence,
    record,
    startMilliseconds,
    endMilliseconds,
    sessionId
  );
  return usageFromEvidence(evidence, startMilliseconds, endMilliseconds, phasePatterns, maxIdleGapSeconds);
}

function collectWindowEvidenceRecord(
  evidence: MutableEvidence,
  record: OtelRecord,
  startMilliseconds: number,
  endMilliseconds: number,
  sessionId?: string
): void {
  for (const resourceSpan of record.resourceSpans ?? []) {
    if (stringAttribute(resourceSpan.resource?.attributes, 'service.name') !== 'copilot-chat') continue;
    if (sessionId && stringAttribute(resourceSpan.resource?.attributes, 'session.id') !== sessionId) continue;
    for (const scopeSpan of resourceSpan.scopeSpans ?? []) {
      for (const span of scopeSpan.spans ?? []) {
        const startedAt = spanStartMilliseconds(span);
        const endedAt = spanEndMilliseconds(span);
        if (startedAt < startMilliseconds || startedAt > endMilliseconds || endedAt > endMilliseconds) continue;
        collectSpanEvidence(evidence, span);
      }
    }
  }
}

function createSessionEvidenceMap(): Map<string, MutableSessionEvidence> {
  return new Map();
}

function collectSessionEvidenceRecord(
  sessions: Map<string, MutableSessionEvidence>,
  record: OtelRecord,
  changedSessions?: Set<string>
): void {
  for (const resourceSpan of record.resourceSpans ?? []) {
    if (stringAttribute(resourceSpan.resource?.attributes, 'service.name') !== 'copilot-chat') continue;
    const sessionId = stringAttribute(resourceSpan.resource?.attributes, 'session.id');
    if (!sessionId) continue;
    const session = sessions.get(sessionId) ?? {
      ...createMutableEvidence(),
      startedMilliseconds: Number.POSITIVE_INFINITY,
      endedMilliseconds: 0,
      chatSessionIds: new Set<string>()
    };
    for (const scopeSpan of resourceSpan.scopeSpans ?? []) {
      for (const span of scopeSpan.spans ?? []) {
        const startedMilliseconds = spanStartMilliseconds(span);
        const endedMilliseconds = spanEndMilliseconds(span);
        if (startedMilliseconds <= 0 || endedMilliseconds < startedMilliseconds) continue;
        const identity = span.spanId ?? `${span.traceId ?? ''}|${span.name}|${span.startTimeUnixNano ?? ''}`;
        const newSpan = !session.seenTimedSpans.has(identity);
        session.startedMilliseconds = Math.min(session.startedMilliseconds, startedMilliseconds);
        session.endedMilliseconds = Math.max(session.endedMilliseconds, endedMilliseconds);
        const chatSessionId = stringAttribute(span.attributes, 'gen_ai.conversation.id') ??
          stringAttribute(span.attributes, 'copilot_chat.chat_session_id');
        const newChatSession = Boolean(chatSessionId && !session.chatSessionIds.has(chatSessionId));
        if (chatSessionId) session.chatSessionIds.add(chatSessionId);
        collectSpanEvidence(session, span);
        if (newSpan || newChatSession) changedSessions?.add(sessionId);
      }
    }
    sessions.set(sessionId, session);
  }
}

function sessionEvidenceFromMap(
  sessions: Map<string, MutableSessionEvidence>,
  phasePatterns: PhaseToolPatterns,
  selectedSessionIds?: ReadonlySet<string>,
  maxIdleGapSeconds?: number
): TraceSessionEvidence[] {
  return [...sessions.entries()]
    .filter(([sessionId]) => !selectedSessionIds || selectedSessionIds.has(sessionId))
    .filter(([, session]) => Number.isFinite(session.startedMilliseconds) && session.endedMilliseconds > 0)
    .map(([sessionId, session]) => {
      const usage = usageFromEvidence(
        session,
        session.startedMilliseconds,
        session.endedMilliseconds,
        phasePatterns,
        maxIdleGapSeconds
      );
      return {
        session: {
          sessionId,
          ...(session.chatSessionIds.size > 0
            ? { chatSessionIds: [...session.chatSessionIds].sort() }
            : {}),
          startedAt: new Date(session.startedMilliseconds).toISOString(),
          endedAt: new Date(session.endedMilliseconds).toISOString(),
          aiCostUsd: usage.aiCostUsd
        },
        usage
      };
    })
    .sort((left, right) => left.session.startedAt.localeCompare(right.session.startedAt) ||
      left.session.sessionId.localeCompare(right.session.sessionId));
}

export class TraceEvidenceAccumulator {
  private readonly accumulated: Map<string, MutableSessionEvidence>;

  constructor(serialized?: SerializedTraceEvidence) {
    this.accumulated = new Map(Object.entries(serialized?.sessions ?? {}).map(([sessionId, session]) => [
      sessionId,
      {
        startedMilliseconds: session.startedMilliseconds,
        endedMilliseconds: session.endedMilliseconds,
        chatSessionIds: new Set(session.chatSessionIds),
        seenSpans: new Set(session.seenSpans),
        seenTimedSpans: new Set(session.seenTimedSpans),
        timedSpans: session.timedSpans,
        models: new Map(Object.entries(session.models))
      }
    ]));
  }

  consume(records: Iterable<unknown>): Set<string> {
    const changedSessions = new Set<string>();
    for (const record of records) {
      collectSessionEvidenceRecord(
        this.accumulated,
        record as OtelRecord,
        changedSessions
      );
    }
    return changedSessions;
  }

  evidence(
    phasePatterns: PhaseToolPatterns,
    selectedSessionIds?: ReadonlySet<string>,
    maxIdleGapSeconds?: number
  ): TraceSessionEvidence[] {
    return sessionEvidenceFromMap(this.accumulated, phasePatterns, selectedSessionIds, maxIdleGapSeconds);
  }

  sessions(): TraceSession[] {
    return traceSessionsFromMap(this.accumulated);
  }

  latestSpanTimestampMilliseconds(): number {
    return Math.max(
      0,
      ...[...this.accumulated.values()].map((session) => session.endedMilliseconds)
    );
  }

  serialize(): SerializedTraceEvidence {
    return {
      version: 1,
      latestSpanTimestampMilliseconds: this.latestSpanTimestampMilliseconds(),
      sessions: Object.fromEntries([...this.accumulated.entries()]
        .filter(([, session]) => Number.isFinite(session.startedMilliseconds) &&
          session.endedMilliseconds > 0)
        .map(([sessionId, session]) => [
          sessionId,
          {
            startedMilliseconds: session.startedMilliseconds,
            endedMilliseconds: session.endedMilliseconds,
            chatSessionIds: [...session.chatSessionIds].sort(),
            seenSpans: [...session.seenSpans].sort(),
            seenTimedSpans: [...session.seenTimedSpans].sort(),
            timedSpans: session.timedSpans,
            models: Object.fromEntries(session.models)
          }
        ]))
    };
  }
}

function traceSessionsFromMap(sessions: Map<string, MutableSessionEvidence>): TraceSession[] {
  return [...sessions.entries()]
    .filter(([, session]) => Number.isFinite(session.startedMilliseconds) && session.endedMilliseconds > 0)
    .map(([sessionId, session]) => ({
      sessionId,
      ...(session.chatSessionIds.size > 0
        ? { chatSessionIds: [...session.chatSessionIds].sort() }
        : {}),
      startedAt: new Date(session.startedMilliseconds).toISOString(),
      endedAt: new Date(session.endedMilliseconds).toISOString(),
      aiCostUsd: [...session.models.values()].reduce(
        (total, usage) => total + usage.nanoAiCredits,
        0
      ) / 1_000_000_000 / 100
    }))
    .sort((left, right) => left.startedAt.localeCompare(right.startedAt) ||
      left.sessionId.localeCompare(right.sessionId));
}

export function aggregateTraceSessionEvidence(
  records: Iterable<unknown>,
  phasePatterns: PhaseToolPatterns,
  maxIdleGapSeconds?: number
): TraceSessionEvidence[] {
  const sessions = createSessionEvidenceMap();
  for (const record of records) collectSessionEvidenceRecord(sessions, record as OtelRecord);
  return sessionEvidenceFromMap(sessions, phasePatterns, undefined, maxIdleGapSeconds);
}

export function summarizeTraceRecords(
  records: unknown[],
  start: Date,
  end: Date,
  phasePatterns: PhaseToolPatterns,
  sessionId?: string,
  maxIdleGapSeconds?: number
): ExperimentUsage {
  return summarizeRecords(records as OtelRecord[], start, end, phasePatterns, sessionId, maxIdleGapSeconds);
}

export function discoverTraceSessions(records: unknown[]): TraceSession[] {
  const sessions = new Map<string, {
    startedMilliseconds: number;
    endedMilliseconds: number;
    nanoAiCredits: number;
    spans: Set<string>;
    chatSessionIds: Set<string>;
  }>();
  for (const record of records as OtelRecord[]) {
    for (const resourceSpan of record.resourceSpans ?? []) {
      if (stringAttribute(resourceSpan.resource?.attributes, 'service.name') !== 'copilot-chat') continue;
      const sessionId = stringAttribute(resourceSpan.resource?.attributes, 'session.id');
      if (!sessionId) continue;
      const session = sessions.get(sessionId) ?? {
        startedMilliseconds: Number.POSITIVE_INFINITY,
        endedMilliseconds: 0,
        nanoAiCredits: 0,
        spans: new Set<string>(),
        chatSessionIds: new Set<string>()
      };
      for (const scopeSpan of resourceSpan.scopeSpans ?? []) {
        for (const span of scopeSpan.spans ?? []) {
          const startedMilliseconds = spanStartMilliseconds(span);
          const endedMilliseconds = spanEndMilliseconds(span);
          if (startedMilliseconds <= 0 || endedMilliseconds < startedMilliseconds) continue;
          session.startedMilliseconds = Math.min(session.startedMilliseconds, startedMilliseconds);
          session.endedMilliseconds = Math.max(session.endedMilliseconds, endedMilliseconds);
          const chatSessionId = stringAttribute(span.attributes, 'gen_ai.conversation.id') ??
            stringAttribute(span.attributes, 'copilot_chat.chat_session_id');
          if (chatSessionId) session.chatSessionIds.add(chatSessionId);
          const identity = span.spanId ?? `${span.traceId ?? ''}|${span.name}|${span.startTimeUnixNano ?? ''}`;
          if (!session.spans.has(identity) && span.name?.startsWith('chat ')) {
            session.spans.add(identity);
            session.nanoAiCredits += numberAttribute(span.attributes, 'copilot_chat.copilot_usage_nano_aiu');
          }
        }
      }
      sessions.set(sessionId, session);
    }
  }
  return [...sessions.entries()]
    .filter(([, session]) => Number.isFinite(session.startedMilliseconds) && session.endedMilliseconds > 0)
    .map(([sessionId, session]) => ({
      sessionId,
      ...session.chatSessionIds.size > 0
        ? { chatSessionIds: [...session.chatSessionIds].sort() }
        : {},
      startedAt: new Date(session.startedMilliseconds).toISOString(),
      endedAt: new Date(session.endedMilliseconds).toISOString(),
      aiCostUsd: session.nanoAiCredits / 1_000_000_000 / 100
    }))
    .sort((left, right) => left.startedAt.localeCompare(right.startedAt) || left.sessionId.localeCompare(right.sessionId));
}

export async function collectTraceUsage(
  filePath: string,
  start: Date,
  end: Date,
  phasePatterns: PhaseToolPatterns,
  sessionId?: string,
  maxIdleGapSeconds?: number
): Promise<ExperimentUsage> {
  const startMilliseconds = start.getTime();
  const endMilliseconds = end.getTime();
  if (!Number.isFinite(startMilliseconds) || !Number.isFinite(endMilliseconds) || startMilliseconds >= endMilliseconds) {
    throw new Error('Experiment trace window is invalid.');
  }
  const evidence = await readStableJsonLines(
    filePath,
    createMutableEvidence,
    (result, record) => collectWindowEvidenceRecord(
      result,
      record as OtelRecord,
      startMilliseconds,
      endMilliseconds,
      sessionId
    )
  );
  return usageFromEvidence(evidence, startMilliseconds, endMilliseconds, phasePatterns, maxIdleGapSeconds);
}

export async function collectTraceSessions(filePath: string): Promise<TraceSession[]> {
  const sessions = await readStableJsonLines(
    filePath,
    createSessionEvidenceMap,
    (result, record) => collectSessionEvidenceRecord(result, record as OtelRecord)
  );
  return traceSessionsFromMap(sessions);
}

export async function collectTraceSessionEvidence(
  filePath: string,
  phasePatterns: PhaseToolPatterns,
  maxIdleGapSeconds?: number
): Promise<TraceSessionEvidence[]> {
  const sessions = await readStableJsonLines(
    filePath,
    createSessionEvidenceMap,
    (result, record) => collectSessionEvidenceRecord(result, record as OtelRecord)
  );
  return sessionEvidenceFromMap(sessions, phasePatterns, undefined, maxIdleGapSeconds);
}

