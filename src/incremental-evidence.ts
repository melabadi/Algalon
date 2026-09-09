import type { PhaseToolPatterns } from './phase-evidence.js';
import {
  TraceEvidenceAccumulator,
  type SerializedTraceEvidence,
  type TraceSession,
  type TraceSessionEvidence
} from './experiment-evidence.js';
import {
  readIncrementalJsonLines,
  type TraceArchiveCursor
} from './trace-archive.js';

export interface IncrementalTraceEvidenceState {
  version: 1;
  cursor: TraceArchiveCursor;
  evidence: SerializedTraceEvidence;
}

export interface IncrementalTraceScan {
  evidence: TraceSessionEvidence[];
  sessions: TraceSession[];
  changedSessionIds: string[];
  recordsRead: number;
  bytesRead: number;
  caughtUp: boolean;
  stateChanged: boolean;
  latestTimestamp?: string;
}

function sameCursor(left: TraceArchiveCursor, right: TraceArchiveCursor): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export class IncrementalTraceSessionCollector {
  private cursor: TraceArchiveCursor;
  private readonly accumulator: TraceEvidenceAccumulator;
  private readonly pendingSessionIds = new Set<string>();

  constructor(
    private readonly filePath: string,
    private readonly phasePatterns: PhaseToolPatterns,
    state?: IncrementalTraceEvidenceState,
    private readonly maxIdleGapSeconds?: number
  ) {
    this.cursor = state?.cursor ?? { segments: {} };
    this.accumulator = new TraceEvidenceAccumulator(state?.evidence);
  }

  async scan(includeEvidence = true): Promise<IncrementalTraceScan> {
    const previousCursor = this.cursor;
    const incremental = await readIncrementalJsonLines<unknown>(this.filePath, previousCursor);
    this.cursor = incremental.cursor;
    for (const sessionId of this.accumulator.consume(incremental.records)) {
      this.pendingSessionIds.add(sessionId);
    }
    const latestMilliseconds = this.accumulator.latestSpanTimestampMilliseconds();
    const changedSessionIds = [...this.pendingSessionIds].sort();
    return {
      evidence: includeEvidence ? this.accumulator.evidence(this.phasePatterns, this.pendingSessionIds, this.maxIdleGapSeconds) : [],
      sessions: this.accumulator.sessions(),
      changedSessionIds,
      recordsRead: incremental.records.length,
      bytesRead: incremental.bytesRead,
      caughtUp: true,
      stateChanged: !sameCursor(previousCursor, this.cursor) || changedSessionIds.length > 0,
      ...(latestMilliseconds > 0
        ? { latestTimestamp: new Date(latestMilliseconds).toISOString() }
        : {})
    };
  }

  evidence(sessionIds: ReadonlySet<string>): TraceSessionEvidence[] {
    return this.accumulator.evidence(this.phasePatterns, sessionIds, this.maxIdleGapSeconds);
  }

  commit(): void {
    this.pendingSessionIds.clear();
  }

  get inboxCursor(): undefined {
    return undefined;
  }

  serialize(): IncrementalTraceEvidenceState {
    return {
      version: 1,
      cursor: this.cursor,
      evidence: this.accumulator.serialize()
    };
  }
}

export interface LegacyOtlpInboxTraceEvidenceState {
  version: 2 | 3;
  cursor: number;
  evidence: SerializedTraceEvidence;
}

export interface OtlpInboxTraceEvidenceState {
  version: 4;
  cursor: number;
  evidence: SerializedTraceEvidence;
  caughtUp?: boolean;
}

export type TraceEvidenceState =
  IncrementalTraceEvidenceState | LegacyOtlpInboxTraceEvidenceState | OtlpInboxTraceEvidenceState;

interface OtlpInboxPage {
  records: unknown[];
  nextCursor: number;
  hasMore: boolean;
}

type FetchLike = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;

async function readOtlpInbox(
  endpoint: string,
  after: number,
  request: FetchLike,
): Promise<{ records: unknown[]; cursor: number; caughtUp: boolean }> {
  const url = new URL(endpoint);
  url.searchParams.set('after', String(after));
  url.searchParams.set('limit', '10000');
  const response = await request(url, { signal: AbortSignal.timeout(60_000) });
  if (!response.ok) {
    throw new Error(`OTLP inbox read failed (${response.status}): ${(await response.text()).slice(0, 500)}`);
  }
  const page = await response.json() as OtlpInboxPage;
  if (!Array.isArray(page.records) || !Number.isSafeInteger(page.nextCursor) ||
    page.nextCursor < after || typeof page.hasMore !== 'boolean') {
    throw new Error('OTLP inbox returned an invalid cursor page.');
  }
  if (page.hasMore && page.nextCursor === after) {
    throw new Error('OTLP inbox cursor did not advance.');
  }
  return { records: page.records, cursor: page.nextCursor, caughtUp: !page.hasMore };
}

export class OtlpInboxSessionCollector {
  private cursor: number;
  private caughtUp: boolean;
  private readonly accumulator: TraceEvidenceAccumulator;
  private readonly pendingSessionIds = new Set<string>();

  constructor(
    private readonly endpoint: string,
    private readonly phasePatterns: PhaseToolPatterns,
    state?: TraceEvidenceState,
    private readonly maxIdleGapSeconds?: number,
    private readonly request: FetchLike = fetch,
  ) {
    const currentState = state?.version === 4 ? state : undefined;
    this.cursor = currentState?.cursor ?? 0;
    this.caughtUp = currentState ? currentState.caughtUp !== false : false;
    this.accumulator = new TraceEvidenceAccumulator(
      currentState?.evidence
    );
    if (currentState?.caughtUp === false) {
      for (const sessionId of Object.keys(currentState.evidence.sessions)) {
        this.pendingSessionIds.add(sessionId);
      }
    }
  }

  async scan(includeEvidence = true): Promise<IncrementalTraceScan> {
    const previousCursor = this.cursor;
    const previouslyCaughtUp = this.caughtUp;
    const incremental = await readOtlpInbox(this.endpoint, previousCursor, this.request);
    this.cursor = incremental.cursor;
    this.caughtUp = incremental.caughtUp;
    for (const sessionId of this.accumulator.consume(incremental.records)) {
      this.pendingSessionIds.add(sessionId);
    }
    const latestMilliseconds = this.accumulator.latestSpanTimestampMilliseconds();
    const changedSessionIds = [...this.pendingSessionIds].sort();
    return {
      evidence: includeEvidence ? this.accumulator.evidence(
        this.phasePatterns, this.pendingSessionIds, this.maxIdleGapSeconds
      ) : [],
      sessions: this.accumulator.sessions(),
      changedSessionIds,
      recordsRead: incremental.records.length,
      bytesRead: 0,
      caughtUp: this.caughtUp,
      stateChanged: previousCursor !== this.cursor || previouslyCaughtUp !== this.caughtUp ||
        changedSessionIds.length > 0,
      ...(latestMilliseconds > 0
        ? { latestTimestamp: new Date(latestMilliseconds).toISOString() }
        : {})
    };
  }

  evidence(sessionIds: ReadonlySet<string>): TraceSessionEvidence[] {
    return this.accumulator.evidence(this.phasePatterns, sessionIds, this.maxIdleGapSeconds);
  }

  commit(): void {
    this.pendingSessionIds.clear();
  }

  get inboxCursor(): number {
    return this.cursor;
  }

  serialize(): OtlpInboxTraceEvidenceState {
    return {
      version: 4,
      cursor: this.cursor,
      evidence: this.accumulator.serialize(),
      caughtUp: this.caughtUp,
    };
  }
}