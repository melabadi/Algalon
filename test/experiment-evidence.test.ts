import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rename, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  aggregateTraceSessionEvidence,
  collectDirectTurnUsage,
  collectSourceStats,
  collectTraceSessionEvidence,
  directTurnLogSignature,
  discoverTraceSessions,
  readStableTraceArchive,
  reuseDirectTurnUsage,
  TraceEvidenceAccumulator,
  summarizeTraceRecords
} from '../src/experiment-evidence.js';
import { readIncrementalJsonLines, readStableJsonLines } from '../src/trace-archive.js';

test('aggregates direct turn usage from every conversation while preserving OTel phases', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'copilot-value-turn-'));
  try {
    const chatSessionId = 'conversation-test';
    const logDirectory = path.join(
      root,
      'workspace',
      'GitHub.copilot-chat',
      'debug-logs',
      chatSessionId
    );
    await mkdir(logDirectory, { recursive: true });
    const start = new Date('2026-08-05T10:00:00Z');
    const events = [
      { ts: start.getTime(), type: 'user_message', attrs: { content: 'Prompt' } },
      { ts: start.getTime() + 100, type: 'llm_request', attrs: { model: 'gpt-test', inputTokens: 100, cachedTokens: 80, outputTokens: 20, reasoningTokens: 5, copilotUsageNanoAiu: 2_000_000_000 } },
      { ts: start.getTime() + 200, type: 'llm_request', attrs: { model: 'gpt-test', inputTokens: 150, cachedTokens: 100, outputTokens: 30, reasoningTokens: 7, copilotUsageNanoAiu: 3_000_000_000 } },
      { ts: start.getTime() + 1_100, type: 'llm_request', attrs: { model: 'gpt-test', inputTokens: 50, cachedTokens: 0, outputTokens: 10, reasoningTokens: 0, copilotUsageNanoAiu: 1_000_000_000 } },
      { ts: start.getTime() + 1_200, type: 'user_message', attrs: { content: 'Outside session' } },
      { ts: start.getTime() + 1_300, type: 'llm_request', attrs: { model: 'gpt-test', inputTokens: 999, cachedTokens: 0, outputTokens: 999, reasoningTokens: 0, copilotUsageNanoAiu: 9_000_000_000 } },
    ];
    await writeFile(
      path.join(logDirectory, 'main.jsonl'),
      `${events.map((event) => JSON.stringify(event)).join('\n')}\n`
    );
    const secondChatSessionId = 'conversation-second';
    const secondLogDirectory = path.join(
      root,
      'workspace',
      'GitHub.copilot-chat',
      'debug-logs',
      secondChatSessionId
    );
    await mkdir(secondLogDirectory, { recursive: true });
    await writeFile(
      path.join(secondLogDirectory, 'main.jsonl'),
      `${[
        { ts: start.getTime() + 300, type: 'user_message', attrs: { content: 'Second conversation' } },
        { ts: start.getTime() + 400, type: 'llm_request', attrs: { model: 'gpt-test', inputTokens: 40, cachedTokens: 10, outputTokens: 8, reasoningTokens: 2, copilotUsageNanoAiu: 1_000_000_000 } }
      ].map((event) => JSON.stringify(event)).join('\n')}\n`
    );
    const phaseTiming = summarizeTraceRecords(
      [{ resourceSpans: [] }],
      start,
      new Date(start.getTime() + 1000),
      { planning: ['todo'], research: ['read'], coding: ['apply_patch'], validation: ['run_in_terminal'] }
    );
    const phaseUsage = phaseTiming.phases;

    const usage = await collectDirectTurnUsage(
      root,
      [chatSessionId, secondChatSessionId],
      start,
      new Date(start.getTime() + 1000),
      phaseTiming
    );

    assert.ok(usage);
    assert.equal(usage.source, 'copilot_turn_log');
    assert.equal(usage.chatSpans, 4);
    assert.equal(usage.inputTokens, 340);
    assert.equal(usage.cacheReadTokens, 190);
    assert.equal(usage.reasoningTokens, 14);
    assert.equal(usage.aiCredits, 7);
    assert.equal(usage.aiCostUsd, 0.07);
    assert.deepEqual(usage.phases, phaseUsage);
    const refreshedPhases = structuredClone(phaseUsage);
    refreshedPhases.coding.allocatedSeconds = 42;
    const reused = reuseDirectTurnUsage(usage, { ...phaseTiming, phases: refreshedPhases });
    assert.equal(reused.aiCredits, usage.aiCredits);
    assert.equal(reused.inputTokens, usage.inputTokens);
    assert.equal(reused.phases.coding.allocatedSeconds, 42);

    const initialSignature = await directTurnLogSignature(root, [chatSessionId, secondChatSessionId]);
    assert.match(initialSignature ?? '', /^[a-f0-9]{64}$/);
    await writeFile(
      path.join(secondLogDirectory, 'main.jsonl'),
      `${JSON.stringify({ ts: start.getTime(), type: 'user_message' })}\n`
    );
    assert.notEqual(
      await directTurnLogSignature(root, [chatSessionId, secondChatSessionId]),
      initialSignature
    );

    const partialUsage = await collectDirectTurnUsage(
      root,
      [chatSessionId, 'conversation-missing'],
      start,
      new Date(start.getTime() + 1000),
      phaseTiming
    );
    assert.equal(partialUsage, null);
    assert.equal(await directTurnLogSignature(root, [chatSessionId, 'conversation-missing']), null);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

function attribute(key: string, value: string) {
  return { key, value: { stringValue: value } };
}

function integerAttribute(key: string, value: number) {
  return { key, value: { intValue: String(value) } };
}

test('aggregates model tokens and authoritative AI credits from unique OTel chat spans', () => {
  const span = {
    traceId: 'trace-1',
    spanId: 'span-1',
    name: 'chat gpt-test',
    startTimeUnixNano: '1785859260000000000',
    attributes: [
      attribute('gen_ai.request.model', 'gpt-test'),
      integerAttribute('gen_ai.usage.input_tokens', 100),
      integerAttribute('gen_ai.usage.cache_read.input_tokens', 80),
      integerAttribute('gen_ai.usage.output_tokens', 20),
      integerAttribute('gen_ai.usage.reasoning_tokens', 5),
      integerAttribute('copilot_chat.copilot_usage_nano_aiu', 2_500_000_000)
    ]
  };
  const records = [{
    resourceSpans: [{
      resource: { attributes: [attribute('service.name', 'copilot-chat')] },
      scopeSpans: [{ spans: [span, span] }]
    }]
  }];

  const usage = summarizeTraceRecords(
    records,
    new Date('2026-08-04T16:00:59Z'),
    new Date('2026-08-04T16:22:28Z'),
    { planning: ['todo'], research: ['read'], coding: ['apply_patch'], validation: ['run_in_terminal'] }
  );

  assert.equal(usage.chatSpans, 1);
  assert.equal(usage.inputTokens, 100);
  assert.equal(usage.cacheReadTokens, 80);
  assert.equal(usage.uncachedInputTokens, 20);
  assert.equal(usage.outputTokens, 20);
  assert.equal(usage.reasoningTokens, 5);
  assert.equal(usage.aiCredits, 2.5);
  assert.equal(usage.aiCostUsd, 0.025);
  assert.deepEqual(usage.models.map(({ model, requests }) => ({ model, requests })), [
    { model: 'gpt-test', requests: 1 }
  ]);
  assert.equal(Object.values(usage.phases).reduce((total, phase) => total + phase.allocatedSeconds, 0), 0);
  assert.equal(usage.phases.unclassified.allocatedSeconds, 0);
  assert.equal(usage.engagedSeconds, 0);
  assert.equal(usage.activityDensity, 0);
  assert.equal(usage.elapsedSeconds, 1289);
});

test('partitions elapsed time across planning, research, coding, and validation without overlap', () => {
  const startMilliseconds = Date.parse('2026-08-04T16:00:00Z');
  const nanos = (offsetSeconds: number) => String(BigInt(startMilliseconds + offsetSeconds * 1000) * 1_000_000n);
  const span = (
    id: string,
    name: string,
    start: number,
    end: number,
    attributes: Array<ReturnType<typeof attribute> | ReturnType<typeof integerAttribute>>
  ) => ({
    traceId: 'trace-1', spanId: id, name, startTimeUnixNano: nanos(start), endTimeUnixNano: nanos(end), attributes
  });
  const records = [{ resourceSpans: [{
    resource: { attributes: [attribute('service.name', 'copilot-chat')] },
    scopeSpans: [{ spans: [
      span('chat-research', 'chat model', 0, 10, [integerAttribute('copilot_chat.copilot_usage_nano_aiu', 1)]),
      span('read', 'execute_tool read_file', 10, 15, [attribute('gen_ai.tool.name', 'read_file')]),
      span('chat-code', 'chat model', 20, 30, [integerAttribute('copilot_chat.copilot_usage_nano_aiu', 1)]),
      span('patch', 'execute_tool apply_patch', 30, 35, [attribute('gen_ai.tool.name', 'apply_patch')]),
      span('test', 'execute_tool run_in_terminal', 40, 50, [attribute('gen_ai.tool.name', 'run_in_terminal')]),
      span('after-end', 'execute_tool run_in_terminal', 55, 80, [attribute('gen_ai.tool.name', 'run_in_terminal')])
    ] }]
  }] }];

  const usage = summarizeTraceRecords(
    records,
    new Date('2026-08-04T16:00:00Z'),
    new Date('2026-08-04T16:01:00Z'),
    { planning: ['todo'], research: ['read'], coding: ['apply_patch'], validation: ['run_in_terminal'] }
  );
  const allocated = Object.values(usage.phases).reduce((total, phase) => total + phase.allocatedSeconds, 0);
  assert.ok(Math.abs(allocated - usage.engagedSeconds) < 1e-9);
  assert.equal(usage.engagedSeconds, 50);
  assert.equal(usage.elapsedSeconds, 60);
  assert.ok(usage.phases.research.allocatedSeconds > 0);
  assert.ok(usage.phases.coding.allocatedSeconds > 0);
  assert.ok(usage.phases.validation.allocatedSeconds > 0);
  assert.equal(usage.phases.planning.allocatedSeconds, 0);
  assert.equal(usage.phases.validation.toolCalls, 1);
  assert.equal(usage.phases.validation.toolActiveSeconds, 10);
});

test('excludes idle longer than the configured gap from allocated session time', () => {
  const startMilliseconds = Date.parse('2026-08-04T16:00:00Z');
  const nanos = (offsetSeconds: number) => String(BigInt(startMilliseconds + offsetSeconds * 1000) * 1_000_000n);
  const toolSpan = (id: string, start: number, end: number) => ({
    traceId: 'trace-idle',
    spanId: id,
    name: 'execute_tool run_in_terminal',
    startTimeUnixNano: nanos(start),
    endTimeUnixNano: nanos(end),
    attributes: [attribute('gen_ai.tool.name', 'run_in_terminal')]
  });
  const records = [{ resourceSpans: [{
    resource: { attributes: [attribute('service.name', 'copilot-chat')] },
    scopeSpans: [{ spans: [toolSpan('early', 0, 60), toolSpan('late', 3_540, 3_600)] }]
  }] }];
  const patterns = { planning: ['todo'], research: ['read'], coding: ['apply_patch'], validation: ['run_in_terminal'] };
  const window = [new Date('2026-08-04T16:00:00Z'), new Date('2026-08-04T17:00:00Z')] as const;

  const bounded = summarizeTraceRecords(records, window[0], window[1], patterns, undefined, 300);
  assert.equal(bounded.elapsedSeconds, 3_600);
  assert.equal(bounded.engagedSeconds, 120);
  assert.equal(bounded.phases.validation.allocatedSeconds, 120);

  const unbounded = summarizeTraceRecords(records, window[0], window[1], patterns, undefined, 7_200);
  assert.equal(unbounded.engagedSeconds, 3_600);
  assert.equal(unbounded.phases.validation.allocatedSeconds, 3_600);
});

test('discovers resource session ids and filters usage to one Copilot session', () => {
  const startMilliseconds = Date.parse('2026-08-05T10:00:00Z');
  const nanos = (offsetSeconds: number) => String(BigInt(startMilliseconds + offsetSeconds * 1000) * 1_000_000n);
  const record = (
    sessionId: string,
    conversationId: string,
    id: string,
    offsetSeconds: number,
    credits: number
  ) => ({
    resourceSpans: [{
      resource: { attributes: [attribute('service.name', 'copilot-chat'), attribute('session.id', sessionId)] },
      scopeSpans: [{ spans: [{
        traceId: `trace-${id}`,
        spanId: id,
        name: 'chat model',
        startTimeUnixNano: nanos(offsetSeconds),
        endTimeUnixNano: nanos(offsetSeconds + 1),
        attributes: [
          attribute('gen_ai.conversation.id', conversationId),
          attribute('gen_ai.request.model', 'model'),
          integerAttribute('gen_ai.usage.input_tokens', 100),
          integerAttribute('copilot_chat.copilot_usage_nano_aiu', credits)
        ]
      }] }]
    }]
  });
  const records = [
    record('session-a', 'conversation-z', 'a', 1, 1_000_000_000),
    record('session-a', 'conversation-a', 'a2', 2, 1_000_000_000),
    record('session-b', 'conversation-b', 'b', 10, 2_000_000_000)
  ];

  const sessions = discoverTraceSessions(records);
  assert.deepEqual(sessions, [
    {
      sessionId: 'session-a',
      chatSessionIds: ['conversation-a', 'conversation-z'],
      startedAt: '2026-08-05T10:00:01.000Z',
      endedAt: '2026-08-05T10:00:03.000Z',
      aiCostUsd: 0.02
    },
    {
      sessionId: 'session-b',
      chatSessionIds: ['conversation-b'],
      startedAt: '2026-08-05T10:00:10.000Z',
      endedAt: '2026-08-05T10:00:11.000Z',
      aiCostUsd: 0.02
    }
  ]);

  const usage = summarizeTraceRecords(
    records,
    new Date('2026-08-05T10:00:00Z'),
    new Date('2026-08-05T10:01:00Z'),
    { planning: ['todo'], research: ['read'], coding: ['apply_patch'], validation: ['run_in_terminal'] },
    'session-b'
  );
  assert.equal(usage.chatSpans, 1);
  assert.equal(usage.aiCostUsd, 0.02);
});

test('aggregates every session in one pass over trace records', () => {
  const startedMilliseconds = Date.parse('2026-08-05T10:00:00Z');
  const nanos = (offsetSeconds: number) => String(BigInt(startedMilliseconds + offsetSeconds * 1000) * 1_000_000n);
  const records = ['session-a', 'session-b'].map((sessionId, index) => ({
    resourceSpans: [{
      resource: { attributes: [attribute('service.name', 'copilot-chat'), attribute('session.id', sessionId)] },
      scopeSpans: [{ spans: [{
        traceId: `trace-${index}`,
        spanId: `span-${index}`,
        name: 'chat model',
        startTimeUnixNano: nanos(index * 10),
        endTimeUnixNano: nanos(index * 10 + 1),
        attributes: [integerAttribute('copilot_chat.copilot_usage_nano_aiu', 1_000_000_000)]
      }] }]
    }]
  }));
  let iterations = 0;
  const singleUseRecords = {
    *[Symbol.iterator]() {
      iterations += 1;
      if (iterations > 1) throw new Error('trace records were scanned more than once');
      yield* records;
    }
  };

  const evidence = aggregateTraceSessionEvidence(
    singleUseRecords,
    { planning: ['todo'], research: ['read'], coding: ['apply_patch'], validation: ['run_in_terminal'] }
  );

  assert.equal(iterations, 1);
  assert.deepEqual(evidence.map((entry) => entry.session.sessionId), ['session-a', 'session-b']);
  assert.deepEqual(evidence.map((entry) => entry.usage.aiCredits), [1, 1]);
});

test('persists accumulated session evidence and updates only changed sessions', () => {
  const startedMilliseconds = Date.parse('2026-08-05T10:00:00Z');
  const nanos = (offsetSeconds: number) => String(BigInt(startedMilliseconds + offsetSeconds * 1000) * 1_000_000n);
  const record = (sessionId: string, spanId: string, offsetSeconds: number) => ({
    resourceSpans: [{
      resource: { attributes: [attribute('service.name', 'copilot-chat'), attribute('session.id', sessionId)] },
      scopeSpans: [{ spans: [{
        traceId: `trace-${spanId}`,
        spanId,
        name: 'chat model',
        startTimeUnixNano: nanos(offsetSeconds),
        endTimeUnixNano: nanos(offsetSeconds + 1),
        attributes: [integerAttribute('copilot_chat.copilot_usage_nano_aiu', 1_000_000_000)]
      }] }]
    }]
  });
  const patterns = { planning: ['todo'], research: ['read'], coding: ['apply_patch'], validation: ['run_in_terminal'] };
  const accumulator = new TraceEvidenceAccumulator();
  assert.deepEqual(
    [...accumulator.consume([record('session-a', 'a-1', 0), record('session-b', 'b-1', 10)])].sort(),
    ['session-a', 'session-b']
  );

  const resumed = new TraceEvidenceAccumulator(JSON.parse(JSON.stringify(accumulator.serialize())));
  assert.deepEqual(
    [...resumed.consume([record('session-a', 'a-1', 0), record('session-a', 'a-2', 20)])],
    ['session-a']
  );
  const changed = resumed.evidence(patterns, new Set(['session-a']));
  assert.equal(changed.length, 1);
  assert.equal(changed[0]?.session.sessionId, 'session-a');
  assert.equal(changed[0]?.usage.aiCredits, 2);
  assert.deepEqual(resumed.sessions().map((session) => session.sessionId), ['session-a', 'session-b']);
});

test('reconstructs one session across active and rotated trace archives', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'copilot-value-archive-'));
  try {
    const startedMilliseconds = Date.parse('2026-08-05T10:00:00Z');
    const nanos = (offsetSeconds: number) => String(
      BigInt(startedMilliseconds + offsetSeconds * 1000) * 1_000_000n
    );
    const record = (spanId: string, offsetSeconds: number, credits: number) => ({
      resourceSpans: [{
        resource: { attributes: [
          attribute('service.name', 'copilot-chat'),
          attribute('session.id', 'session-across-rotation')
        ] },
        scopeSpans: [{ spans: [{
          traceId: `trace-${spanId}`,
          spanId,
          name: 'chat model',
          startTimeUnixNano: nanos(offsetSeconds),
          endTimeUnixNano: nanos(offsetSeconds + 1),
          attributes: [
            attribute('gen_ai.conversation.id', 'conversation-across-rotation'),
            attribute('gen_ai.request.model', 'model'),
            integerAttribute('copilot_chat.copilot_usage_nano_aiu', credits)
          ]
        }] }]
      }]
    });
    const activePath = path.join(root, 'traces.json');
    await writeFile(
      path.join(root, 'traces-2026-08-05T10-01-00-size.json'),
      `${JSON.stringify(record('early', 0, 1_000_000_000))}\n`
    );
    await writeFile(
      activePath,
      `${JSON.stringify(record('late', 60, 2_000_000_000))}\n`
    );

    const evidence = await collectTraceSessionEvidence(
      activePath,
      { planning: ['todo'], research: ['read'], coding: ['apply_patch'], validation: ['run_in_terminal'] }
    );

    assert.equal(evidence.length, 1);
    assert.equal(evidence[0]?.session.startedAt, '2026-08-05T10:00:00.000Z');
    assert.equal(evidence[0]?.session.endedAt, '2026-08-05T10:01:01.000Z');
    assert.equal(evidence[0]?.usage.chatSpans, 2);
    assert.equal(evidence[0]?.usage.aiCredits, 3);
    assert.equal(
      Object.values(evidence[0]?.usage.phases ?? {}).reduce(
        (total, phase) => total + phase.allocatedSeconds,
        0
      ),
      61
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('retries when the trace archive changes during a read', async () => {
  const stableSnapshot = [{ path: 'traces.json', modifiedNanoseconds: 2n, size: 20 }];
  const snapshots = [
    [{ path: 'traces.json', modifiedNanoseconds: 1n, size: 10 }],
    stableSnapshot,
    stableSnapshot,
    stableSnapshot
  ];
  const reads = [['partial'], ['complete']];

  const result = await readStableTraceArchive(
    async () => snapshots.shift() ?? stableSnapshot,
    async () => reads.shift() ?? ['complete']
  );

  assert.deepEqual(result, ['complete']);
  assert.equal(snapshots.length, 0);
  assert.equal(reads.length, 0);
});

test('streams complete JSONL records while ignoring only an incomplete final tail', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'copilot-value-jsonl-'));
  try {
    const tracePath = path.join(root, 'traces.json');
    await writeFile(tracePath, '{"value":1}\n{"partial":');
    const values = await readStableJsonLines<number[]>(
      tracePath,
      () => [],
      (result, record) => result.push((record as { value: number }).value)
    );
    assert.deepEqual(values, [1]);

    await writeFile(tracePath, '{"value":1}\nnot-json\n{"value":2}\n');
    await assert.rejects(
      readStableJsonLines(tracePath, () => [] as unknown[], (result, record) => result.push(record)),
      SyntaxError
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('reads only appended trace records while replaying rotation safely', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'copilot-value-cursor-'));
  try {
    const tracePath = path.join(root, 'traces.json');
    await writeFile(tracePath, '{"value":1}\n');

    const first = await readIncrementalJsonLines<{ value: number }>(tracePath);
    assert.deepEqual(first.records.map((record) => record.value), [1]);

    const idle = await readIncrementalJsonLines<{ value: number }>(tracePath, first.cursor);
    assert.deepEqual(idle.records, []);

    await writeFile(tracePath, '{"value":1}\n{"value":2}\n{"partial":');
    const appended = await readIncrementalJsonLines<{ value: number }>(tracePath, idle.cursor);
    assert.deepEqual(appended.records.map((record) => record.value), [2]);

    await writeFile(tracePath, '{"value":1}\n{"value":2}\n{"value":3}\n');
    const completed = await readIncrementalJsonLines<{ value: number }>(tracePath, appended.cursor);
    assert.deepEqual(completed.records.map((record) => record.value), [3]);

    await rename(tracePath, path.join(root, 'traces-rotation.json'));
    await writeFile(tracePath, '{"value":4}\n');
    const rotated = await readIncrementalJsonLines<{ value: number }>(tracePath, completed.cursor);
    assert.deepEqual(rotated.records.map((record) => record.value), [4]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('counts authored source while excluding dependencies, build output, and lock files', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'copilot-value-source-'));
  try {
    await mkdir(path.join(root, 'src'));
    await mkdir(path.join(root, 'node_modules'));
    await mkdir(path.join(root, 'dist'));
    await writeFile(path.join(root, 'src', 'App.tsx'), 'const app = true;\n');
    await writeFile(path.join(root, 'index.html'), '<main></main>\n');
    await writeFile(path.join(root, 'node_modules', 'ignored.js'), 'x'.repeat(100));
    await writeFile(path.join(root, 'dist', 'ignored.js'), 'x'.repeat(100));
    await writeFile(path.join(root, 'package-lock.json'), 'x'.repeat(100));

    const stats = await collectSourceStats([root]);
    assert.equal(stats.files, 2);
    assert.equal(stats.characters, 32);
    assert.equal(stats.lines, 4);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});