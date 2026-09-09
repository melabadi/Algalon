import assert from 'node:assert/strict';
import { appendFile, mkdtemp, rename, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  IncrementalTraceSessionCollector,
  OtlpInboxSessionCollector,
} from '../src/incremental-evidence.js';

const phasePatterns = {
  planning: ['todo'],
  research: ['read'],
  coding: ['apply_patch'],
  validation: ['run_in_terminal']
};

function attribute(key: string, stringValue: string) {
  return { key, value: { stringValue } };
}

function integerAttribute(key: string, intValue: number) {
  return { key, value: { intValue } };
}

function traceRecord(spanId: string, offsetSeconds: number) {
  const epochMilliseconds = Date.parse('2026-08-05T10:00:00Z');
  const nanos = (offset: number) => String(BigInt(epochMilliseconds + offset * 1_000) * 1_000_000n);
  return {
    resourceSpans: [{
      resource: {
        attributes: [
          attribute('service.name', 'copilot-chat'),
          attribute('session.id', 'session-a')
        ]
      },
      scopeSpans: [{
        spans: [{
          traceId: `trace-${spanId}`,
          spanId,
          name: 'chat model',
          startTimeUnixNano: nanos(offsetSeconds),
          endTimeUnixNano: nanos(offsetSeconds + 1),
          attributes: [integerAttribute('copilot_chat.copilot_usage_nano_aiu', 1_000_000_000)]
        }]
      }]
    }]
  };
}

function jsonLine(value: unknown): string {
  return `${JSON.stringify(value)}\n`;
}

test('resumes by byte cursor while accepting late spans and deduplicating rotation replay', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'copilot-value-incremental-'));
  try {
    const tracePath = path.join(root, 'traces.json');
    const newest = traceRecord('newest', 20);
    await writeFile(tracePath, jsonLine(newest));

    const initialCollector = new IncrementalTraceSessionCollector(tracePath, phasePatterns);
    const initial = await initialCollector.scan();
    assert.equal(initial.recordsRead, 1);
    assert.equal(initial.evidence[0]?.usage.chatSpans, 1);
    assert.equal(initial.latestTimestamp, '2026-08-05T10:00:21.000Z');
    const persisted = JSON.parse(JSON.stringify(initialCollector.serialize()));
    initialCollector.commit();

    const resumed = new IncrementalTraceSessionCollector(tracePath, phasePatterns, persisted);
    const idle = await resumed.scan();
    assert.equal(idle.recordsRead, 0);
    assert.equal(idle.bytesRead, 0);
    assert.equal(idle.stateChanged, false);

    await appendFile(tracePath, jsonLine(traceRecord('late', 10)));
    const late = await resumed.scan();
    assert.equal(late.recordsRead, 1);
    assert.equal(late.evidence[0]?.usage.chatSpans, 2);
    assert.equal(late.evidence[0]?.session.startedAt, '2026-08-05T10:00:10.000Z');
    assert.equal(late.latestTimestamp, '2026-08-05T10:00:21.000Z');
    resumed.commit();

    await rename(tracePath, path.join(root, 'traces-1.json'));
    await writeFile(tracePath, jsonLine(newest) + jsonLine(traceRecord('after-rotation', 30)));
    const rotated = await resumed.scan();
    assert.equal(rotated.recordsRead, 2);
    assert.equal(rotated.evidence[0]?.usage.chatSpans, 3);
    assert.deepEqual(rotated.changedSessionIds, ['session-a']);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('advances a transactional OTLP inbox cursor across paged records', async () => {
  const records = [traceRecord('first', 0), traceRecord('second', 10)];
  const request = async (input: string | URL | Request): Promise<Response> => {
    const url = new URL(String(input));
    const after = Number(url.searchParams.get('after'));
    return new Response(JSON.stringify(
      after === 0
        ? { records: [records[0]], nextCursor: 1, hasMore: true }
        : after === 1
          ? { records: [records[1]], nextCursor: 2, hasMore: false }
          : { records: [], nextCursor: after, hasMore: false }
    ));
  };
  const collector = new OtlpInboxSessionCollector(
    'http://app:8000/api/internal/otel/records', phasePatterns, undefined, undefined, request
  );

  const initial = await collector.scan();
  assert.equal(initial.recordsRead, 1);
  assert.equal(initial.caughtUp, false);
  assert.equal(initial.evidence[0]?.usage.chatSpans, 1);
  assert.deepEqual(initial.changedSessionIds, ['session-a']);
  const state = collector.serialize();
  assert.equal(state.cursor, 1);
  assert.equal(state.caughtUp, false);

  const resumed = new OtlpInboxSessionCollector(
    'http://app:8000/api/internal/otel/records', phasePatterns, state, undefined, request
  );
  const completed = await resumed.scan();
  assert.equal(completed.recordsRead, 1);
  assert.equal(completed.caughtUp, true);
  assert.equal(completed.evidence[0]?.usage.chatSpans, 2);
  assert.deepEqual(completed.changedSessionIds, ['session-a']);
  const completedState = resumed.serialize();
  assert.equal(completedState.cursor, 2);
  assert.equal(completedState.caughtUp, true);
  resumed.commit();

  const idleCollector = new OtlpInboxSessionCollector(
    'http://app:8000/api/internal/otel/records',
    phasePatterns,
    completedState,
    undefined,
    request,
  );
  const idle = await idleCollector.scan();
  assert.equal(idle.recordsRead, 0);
  assert.equal(idle.caughtUp, true);
  assert.equal(idle.stateChanged, false);
});

test('rebuilds accumulated evidence when upgrading the OTLP inbox state', async () => {
  const records = [traceRecord('first', 0), traceRecord('second', 10)];
  const firstOnlyRequest = async (): Promise<Response> => new Response(JSON.stringify({
    records: [records[0]], nextCursor: 1, hasMore: false
  }));
  const partial = new OtlpInboxSessionCollector(
    'http://app:8000/api/internal/otel/records',
    phasePatterns,
    undefined,
    undefined,
    firstOnlyRequest,
  );
  await partial.scan();
  const staleState = { ...partial.serialize(), version: 3 as const, cursor: 2 };
  const requestedAfter: number[] = [];
  const replayRequest = async (input: string | URL | Request): Promise<Response> => {
    const after = Number(new URL(String(input)).searchParams.get('after'));
    requestedAfter.push(after);
    return new Response(JSON.stringify({
      records: after === 0 ? records : [],
      nextCursor: after === 0 ? 2 : after,
      hasMore: false,
    }));
  };

  const rebuilt = new OtlpInboxSessionCollector(
    'http://app:8000/api/internal/otel/records',
    phasePatterns,
    staleState,
    undefined,
    replayRequest,
  );
  const scan = await rebuilt.scan();

  assert.deepEqual(requestedAfter, [0]);
  assert.equal(scan.recordsRead, 2);
  assert.equal(scan.evidence[0]?.usage.chatSpans, 2);
  assert.equal(rebuilt.serialize().version, 4);
});