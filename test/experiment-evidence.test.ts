import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  aggregateTraceSessionEvidence,
  collectSourceStats,
  collectTraceSessionEvidence,
  discoverTraceSessions,
  readStableTraceArchive,
  TraceEvidenceAccumulator,
  summarizeTraceRecords
} from '../src/experiment-evidence.js';
import { readIncrementalJsonLines, readStableJsonLines } from '../src/trace-archive.js';

type Attribute = { key: string; value: Record<string, unknown> };
type FixtureSpan = { traceId: string; spanId: string; parentSpanId?: string; name: string; attributes: Attribute[] };
type FixturePayload = {
  resourceSpans: Array<{
    resource: { attributes: Attribute[] };
    scopeSpans: Array<{ scope: { name: string }; spans: FixtureSpan[] }>;
  }>;
};
type ExpectedSession = {
  chatSpans: number; toolSpans: number; nanoAiu: number; inputTokens: number; cacheReadTokens: number;
  outputTokens: number; reasoningTokens: number; surface: string; mode: string; startedAt: string; endedAt: string;
};

const runtimePatterns = {
  planning: ['ask_user', 'exit_plan_mode', 'todo'],
  research: ['view', 'rg', 'grep', 'glob', 'fetch', 'search'],
  coding: ['edit', 'create', 'apply_patch'],
  validation: ['powershell', 'bash', 'shell', 'test']
};

function attribute(key: string, value: string) {
  return { key, value: { stringValue: value } };
}

function integerAttribute(key: string, value: number) {
  return { key, value: { intValue: String(value) } };
}

function flatten(attributes: Attribute[]): Record<string, unknown> {
  return Object.fromEntries(attributes.map((item) => [item.key, Object.values(item.value)[0]]));
}

function stampedResource(sessionKey: string, surface = 'cli', mode = 'interactive') {
  return {
    attributes: [
      attribute('service.name', 'github-copilot'),
      attribute('algalon.session.key', sessionKey),
      attribute('algalon.source.surface', surface),
      attribute('algalon.session.mode', mode)
    ]
  };
}

/** Mirrors FastAPI ingestion: accept runtime GenAI spans, resolve the conversation, and stamp one span per record. */
function stampFixture(payloads: FixturePayload[]) {
  const sessionBySpan = new Map<string, string>();
  const records: unknown[] = [];
  for (const payload of payloads) {
    const pending: Array<{ resource: Record<string, unknown>; span: FixtureSpan }> = [];
    for (const resourceSpan of payload.resourceSpans) {
      const resource = flatten(resourceSpan.resource.attributes);
      for (const scopeSpan of resourceSpan.scopeSpans) {
        if (resource['service.name'] !== 'github-copilot' && scopeSpan.scope.name !== 'github.copilot') continue;
        for (const span of scopeSpan.spans) {
          const operation = flatten(span.attributes)['gen_ai.operation.name'];
          if (operation !== 'chat' && operation !== 'execute_tool' && operation !== 'invoke_agent') continue;
          pending.push({ resource, span });
        }
      }
    }
    for (const { span } of pending) {
      const conversation = flatten(span.attributes)['gen_ai.conversation.id'];
      if (typeof conversation !== 'string') continue;
      sessionBySpan.set(`${span.traceId}|${span.spanId}`, conversation);
      if (span.parentSpanId) sessionBySpan.set(`${span.traceId}|${span.parentSpanId}`, conversation);
    }
    for (const { resource, span } of pending) {
      const sessionKey = flatten(span.attributes)['gen_ai.conversation.id'] as string | undefined ??
        sessionBySpan.get(`${span.traceId}|${span.parentSpanId}`);
      assert.ok(sessionKey, `span ${span.spanId} must resolve to a session`);
      const surface = typeof resource['algalon.surface'] === 'string' ? resource['algalon.surface'] as string
        : resource['service.namespace'] === 'vscode.agent-host' ? 'vscode' : 'cli';
      const mode = resource['algalon.session.mode'] === 'automated' ? 'automated' : 'interactive';
      records.push({ resourceSpans: [{ resource: stampedResource(sessionKey, surface, mode), scopeSpans: [{ spans: [span] }] }] });
    }
  }
  return records;
}

test('maps the shared Copilot runtime contract fixture into session evidence', async () => {
  const fixture = JSON.parse(await readFile(path.join('test', 'fixtures', 'copilot-runtime-otlp.json'), 'utf8')) as {
    payloads: FixturePayload[];
    expected: { sessions: Record<string, ExpectedSession>; acceptedSpans: number };
  };
  const records = stampFixture(fixture.payloads);
  assert.equal(records.length, fixture.expected.acceptedSpans);

  const evidence = aggregateTraceSessionEvidence(records, runtimePatterns);
  assert.deepEqual(
    evidence.map((entry) => entry.session.sessionId).sort(),
    Object.keys(fixture.expected.sessions).sort()
  );
  for (const entry of evidence) {
    const expected = fixture.expected.sessions[entry.session.sessionId] as ExpectedSession;
    assert.equal(entry.session.surface, expected.surface);
    assert.equal(entry.session.mode, expected.mode);
    assert.equal(entry.session.startedAt, expected.startedAt);
    assert.equal(entry.session.endedAt, expected.endedAt);
    assert.equal(entry.usage.chatSpans, expected.chatSpans);
    assert.equal(entry.usage.inputTokens, expected.inputTokens);
    assert.equal(entry.usage.cacheReadTokens, expected.cacheReadTokens);
    assert.equal(entry.usage.outputTokens, expected.outputTokens);
    assert.equal(entry.usage.reasoningTokens, expected.reasoningTokens);
    assert.ok(Math.abs(entry.usage.aiCredits - expected.nanoAiu / 1e9) < 1e-12);
    assert.ok(Math.abs(entry.usage.aiCostUsd - expected.nanoAiu / 1e11) < 1e-12);
  }

  const cli = evidence.find((entry) => entry.session.sessionId === 'cli-session-0001');
  assert.ok(cli);
  assert.equal(cli.usage.phases.research.toolCalls, 4);
  assert.equal(cli.usage.phases.validation.toolCalls, 2);
  assert.equal(cli.usage.phases.coding.toolCalls, 1);
  assert.equal(cli.usage.phases.unclassified.toolCalls, 0, 'the task delegation span is a container, not a tool call');
  const vscode = evidence.find((entry) => entry.session.sessionId === 'vscode-session-0001');
  assert.equal(vscode?.usage.phases.coding.toolCalls, 1, 'tool spans without a conversation id join their parent');
});

test('counts AI credits once from chat spans and never from invoke_agent totals', () => {
  const chat = {
    traceId: 'trace-1',
    spanId: 'span-1',
    parentSpanId: 'root-1',
    name: 'chat gpt-test',
    startTimeUnixNano: '1785859260000000000',
    endTimeUnixNano: '1785859261000000000',
    attributes: [
      attribute('gen_ai.operation.name', 'chat'),
      attribute('gen_ai.request.model', 'gpt-test'),
      integerAttribute('gen_ai.usage.input_tokens', 100),
      integerAttribute('gen_ai.usage.cache_read.input_tokens', 80),
      integerAttribute('gen_ai.usage.output_tokens', 20),
      attribute('gen_ai.usage.reasoning.output_tokens', '5'),
      { key: 'github.copilot.nano_aiu', value: { doubleValue: 2_500_000_000 } }
    ]
  };
  const root = {
    traceId: 'trace-1',
    spanId: 'root-1',
    name: 'invoke_agent',
    startTimeUnixNano: '1785859259000000000',
    endTimeUnixNano: '1785859262000000000',
    attributes: [
      attribute('gen_ai.operation.name', 'invoke_agent'),
      integerAttribute('gen_ai.usage.input_tokens', 100),
      { key: 'github.copilot.nano_aiu', value: { doubleValue: 2_500_000_000 } }
    ]
  };
  const records = [{
    resourceSpans: [{ resource: stampedResource('session-1'), scopeSpans: [{ spans: [chat, chat, root] }] }]
  }];

  const usage = summarizeTraceRecords(
    records,
    new Date('2026-08-04T16:00:58Z'),
    new Date('2026-08-04T16:22:28Z'),
    runtimePatterns
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
  const tool = (id: string, toolName: string, start: number, end: number, spanName = `execute_tool ${toolName}`) =>
    span(id, spanName, start, end, [attribute('gen_ai.operation.name', 'execute_tool'), attribute('gen_ai.tool.name', toolName)]);
  const chat = (id: string, start: number, end: number) =>
    span(id, 'chat model', start, end, [attribute('gen_ai.operation.name', 'chat'), integerAttribute('github.copilot.nano_aiu', 1)]);
  const records = [{ resourceSpans: [{
    resource: stampedResource('session-phases'),
    scopeSpans: [{ spans: [
      chat('chat-research', 0, 10),
      tool('view', 'view', 10, 15),
      chat('chat-code', 20, 30),
      tool('patch', 'apply_patch', 30, 35),
      tool('test', 'powershell', 40, 50, 'execute_tool powershell pwsh.exe'),
      tool('after-end', 'powershell', 55, 80)
    ] }]
  }] }];

  const usage = summarizeTraceRecords(
    records,
    new Date('2026-08-04T16:00:00Z'),
    new Date('2026-08-04T16:01:00Z'),
    runtimePatterns
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

test('excludes delegation container tools whose nested subagent spans carry the evidence', () => {
  const startMilliseconds = Date.parse('2026-08-04T16:00:00Z');
  const nanos = (offsetSeconds: number) => String(BigInt(startMilliseconds + offsetSeconds * 1000) * 1_000_000n);
  const tool = (id: string, toolName: string, start: number, end: number) => ({
    traceId: 'trace-task', spanId: id, name: `execute_tool ${toolName}`,
    startTimeUnixNano: nanos(start), endTimeUnixNano: nanos(end),
    attributes: [attribute('gen_ai.operation.name', 'execute_tool'), attribute('gen_ai.tool.name', toolName)]
  });
  const records = [{ resourceSpans: [{
    resource: stampedResource('session-task'),
    scopeSpans: [{ spans: [tool('task', 'task', 0, 60), tool('nested-view', 'view', 10, 20), tool('wait', 'read_agent', 20, 50)] }]
  }] }];

  const usage = summarizeTraceRecords(records, new Date(startMilliseconds), new Date(startMilliseconds + 60_000), runtimePatterns);
  assert.equal(usage.phases.research.toolCalls, 1);
  assert.equal(usage.phases.unclassified.toolCalls, 0);
  assert.equal(usage.activeSeconds, 10);
  assert.equal(usage.phases.research.allocatedSeconds, 10);
});

test('excludes idle longer than the configured gap from allocated session time', () => {
  const startMilliseconds = Date.parse('2026-08-04T16:00:00Z');
  const nanos = (offsetSeconds: number) => String(BigInt(startMilliseconds + offsetSeconds * 1000) * 1_000_000n);
  const toolSpan = (id: string, start: number, end: number) => ({
    traceId: 'trace-idle',
    spanId: id,
    name: 'execute_tool bash',
    startTimeUnixNano: nanos(start),
    endTimeUnixNano: nanos(end),
    attributes: [attribute('gen_ai.operation.name', 'execute_tool'), attribute('gen_ai.tool.name', 'bash')]
  });
  const records = [{ resourceSpans: [{
    resource: stampedResource('session-idle'),
    scopeSpans: [{ spans: [toolSpan('early', 0, 60), toolSpan('late', 3_540, 3_600)] }]
  }] }];
  const window = [new Date('2026-08-04T16:00:00Z'), new Date('2026-08-04T17:00:00Z')] as const;

  const bounded = summarizeTraceRecords(records, window[0], window[1], runtimePatterns, undefined, 300);
  assert.equal(bounded.elapsedSeconds, 3_600);
  assert.equal(bounded.engagedSeconds, 120);
  assert.equal(bounded.phases.validation.allocatedSeconds, 120);

  const unbounded = summarizeTraceRecords(records, window[0], window[1], runtimePatterns, undefined, 7_200);
  assert.equal(unbounded.engagedSeconds, 3_600);
  assert.equal(unbounded.phases.validation.allocatedSeconds, 3_600);
});

test('discovers stamped runtime sessions, ignores unstamped records, and filters usage to one session', () => {
  const startMilliseconds = Date.parse('2026-08-05T10:00:00Z');
  const nanos = (offsetSeconds: number) => String(BigInt(startMilliseconds + offsetSeconds * 1000) * 1_000_000n);
  const chat = (id: string, offsetSeconds: number, credits: number) => ({
    traceId: `trace-${id}`,
    spanId: id,
    name: 'chat model',
    startTimeUnixNano: nanos(offsetSeconds),
    endTimeUnixNano: nanos(offsetSeconds + 1),
    attributes: [
      attribute('gen_ai.operation.name', 'chat'),
      attribute('gen_ai.request.model', 'model'),
      integerAttribute('gen_ai.usage.input_tokens', 100),
      integerAttribute('github.copilot.nano_aiu', credits)
    ]
  });
  const records = [
    { resourceSpans: [{ resource: stampedResource('session-a', 'cli'), scopeSpans: [{ spans: [chat('a', 1, 1_000_000_000)] }] }] },
    { resourceSpans: [{ resource: stampedResource('session-a', 'vscode'), scopeSpans: [{ spans: [chat('a2', 2, 1_000_000_000)] }] }] },
    { resourceSpans: [{ resource: stampedResource('session-b', 'sdk', 'automated'), scopeSpans: [{ spans: [chat('b', 10, 2_000_000_000)] }] }] },
    { resourceSpans: [{
      resource: { attributes: [attribute('service.name', 'copilot-chat'), attribute('session.id', 'legacy')] },
      scopeSpans: [{ spans: [chat('legacy', 20, 9_000_000_000)] }]
    }] }
  ];

  const sessions = discoverTraceSessions(records);
  assert.deepEqual(sessions, [
    {
      sessionId: 'session-a',
      surface: 'mixed',
      mode: 'interactive',
      startedAt: '2026-08-05T10:00:01.000Z',
      endedAt: '2026-08-05T10:00:03.000Z',
      aiCostUsd: 0.02
    },
    {
      sessionId: 'session-b',
      surface: 'sdk',
      mode: 'automated',
      startedAt: '2026-08-05T10:00:10.000Z',
      endedAt: '2026-08-05T10:00:11.000Z',
      aiCostUsd: 0.02
    }
  ]);

  const usage = summarizeTraceRecords(
    records,
    new Date('2026-08-05T10:00:00Z'),
    new Date('2026-08-05T10:01:00Z'),
    runtimePatterns,
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
      resource: stampedResource(sessionId),
      scopeSpans: [{ spans: [{
        traceId: `trace-${index}`,
        spanId: `span-${index}`,
        name: 'chat model',
        startTimeUnixNano: nanos(index * 10),
        endTimeUnixNano: nanos(index * 10 + 1),
        attributes: [attribute('gen_ai.operation.name', 'chat'), integerAttribute('github.copilot.nano_aiu', 1_000_000_000)]
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

  const evidence = aggregateTraceSessionEvidence(singleUseRecords, runtimePatterns);

  assert.equal(iterations, 1);
  assert.deepEqual(evidence.map((entry) => entry.session.sessionId), ['session-a', 'session-b']);
  assert.deepEqual(evidence.map((entry) => entry.usage.aiCredits), [1, 1]);
});

test('persists accumulated session evidence and updates only changed sessions', () => {
  const startedMilliseconds = Date.parse('2026-08-05T10:00:00Z');
  const nanos = (offsetSeconds: number) => String(BigInt(startedMilliseconds + offsetSeconds * 1000) * 1_000_000n);
  const record = (sessionId: string, spanId: string, offsetSeconds: number) => ({
    resourceSpans: [{
      resource: stampedResource(sessionId),
      scopeSpans: [{ spans: [{
        traceId: `trace-${spanId}`,
        spanId,
        name: 'chat model',
        startTimeUnixNano: nanos(offsetSeconds),
        endTimeUnixNano: nanos(offsetSeconds + 1),
        attributes: [attribute('gen_ai.operation.name', 'chat'), integerAttribute('github.copilot.nano_aiu', 1_000_000_000)]
      }] }]
    }]
  });
  const accumulator = new TraceEvidenceAccumulator();
  assert.deepEqual(
    [...accumulator.consume([record('session-a', 'a-1', 0), record('session-b', 'b-1', 10)])].sort(),
    ['session-a', 'session-b']
  );

  const serialized = JSON.parse(JSON.stringify(accumulator.serialize()));
  assert.equal(serialized.version, 2);
  const resumed = new TraceEvidenceAccumulator(serialized);
  assert.deepEqual(
    [...resumed.consume([record('session-a', 'a-1', 0), record('session-a', 'a-2', 20)])],
    ['session-a']
  );
  const changed = resumed.evidence(runtimePatterns, new Set(['session-a']));
  assert.equal(changed.length, 1);
  assert.equal(changed[0]?.session.sessionId, 'session-a');
  assert.equal(changed[0]?.usage.aiCredits, 2);
  assert.deepEqual(resumed.sessions().map((session) => session.sessionId), ['session-a', 'session-b']);

  const legacy = new TraceEvidenceAccumulator({ ...serialized, version: 1 });
  assert.deepEqual(legacy.sessions(), [], 'pre-runtime accumulated evidence is discarded');
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
        resource: stampedResource('session-across-rotation'),
        scopeSpans: [{ spans: [{
          traceId: `trace-${spanId}`,
          spanId,
          name: 'chat model',
          startTimeUnixNano: nanos(offsetSeconds),
          endTimeUnixNano: nanos(offsetSeconds + 1),
          attributes: [
            attribute('gen_ai.operation.name', 'chat'),
            attribute('gen_ai.request.model', 'model'),
            integerAttribute('github.copilot.nano_aiu', credits)
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

    const evidence = await collectTraceSessionEvidence(activePath, runtimePatterns);

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
