import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  canonicalizeUsageCosts,
  earliestSessionStart,
  evaluateContinuousWindow,
  evaluateOtelOnlyWindow,
  migrationUsageFromRefreshedEvidence,
  modelConfigurationSignature,
  publishAndCommitSession,
  refreshArtifactInboxCursor,
  SESSION_CALCULATION_VERSION,
  selectSessionSnapshots,
  sessionCoverageRegressed,
  sessionNeedsEvaluation,
  sessionStateForObservedBounds,
  sessionExperimentName,
  type ContinuousWindowDependencies
} from '../src/continuous.js';
import { otelSnapshotSchema, valueConfigSchema } from '../src/schema.js';
import type { ExperimentUsage, TraceSessionEvidence } from '../src/experiment-evidence.js';
import type { OtelSourceDelta, SourceSnapshot } from '../src/source-delta.js';

const phases: ExperimentUsage['phases'] = {
  planning: { activeSeconds: 0, allocatedSeconds: 0, toolActiveSeconds: 0, toolCalls: 0, modelSpans: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0 },
  research: { activeSeconds: 0, allocatedSeconds: 0, toolActiveSeconds: 0, toolCalls: 0, modelSpans: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0 },
  coding: { activeSeconds: 10, allocatedSeconds: 30, toolActiveSeconds: 1, toolCalls: 1, modelSpans: 1, uncachedInputTokens: 10, outputTokens: 20, reasoningTokens: 5 },
  validation: { activeSeconds: 10, allocatedSeconds: 30, toolActiveSeconds: 5, toolCalls: 1, modelSpans: 1, uncachedInputTokens: 10, outputTokens: 20, reasoningTokens: 5 },
  unclassified: { activeSeconds: 0, allocatedSeconds: 0, toolActiveSeconds: 0, toolCalls: 0, modelSpans: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0 }
};

test('recalculates a completed session when the calculation contract changes', () => {
  const endedAt = '2026-08-05T12:00:00Z';
  assert.equal(sessionNeedsEvaluation(endedAt, undefined, endedAt), true);
  assert.equal(sessionNeedsEvaluation(endedAt, SESSION_CALCULATION_VERSION - 1, endedAt), true);
  assert.equal(sessionNeedsEvaluation(endedAt, SESSION_CALCULATION_VERSION, endedAt, 'usage', 'usage'), false);
  assert.equal(sessionNeedsEvaluation(endedAt, SESSION_CALCULATION_VERSION, endedAt, 'old', 'new'), true);
  assert.equal(sessionNeedsEvaluation('2026-08-05T11:59:00Z', SESSION_CALCULATION_VERSION, endedAt), true);
  assert.equal(sessionNeedsEvaluation(
    endedAt, SESSION_CALCULATION_VERSION, endedAt, 'usage', 'usage', 'old-model', 'new-model'
  ), true);
  assert.equal(sessionNeedsEvaluation(
    endedAt, SESSION_CALCULATION_VERSION, endedAt, 'usage', 'usage', 'model', 'model'
  ), false);
  assert.equal(sessionNeedsEvaluation(
    endedAt, SESSION_CALCULATION_VERSION, endedAt, 'usage', 'usage', 'model', 'model', true
  ), true);
});

test('rejects retained evidence that starts after the tracked session', () => {
  const trackedStart = '2026-08-05T10:00:00Z';
  assert.equal(sessionCoverageRegressed(trackedStart, '2026-08-05T10:00:01Z'), true);
  assert.equal(sessionCoverageRegressed(trackedStart, trackedStart), false);
  assert.equal(sessionCoverageRegressed(trackedStart, '2026-08-05T09:59:59Z'), false);
  assert.throws(() => sessionCoverageRegressed('invalid', trackedStart), /valid timestamps/);
});

test('expands a tracked session start when late evidence predates discovery', () => {
  const trackedStart = '2026-08-05T10:00:00Z';
  assert.equal(earliestSessionStart(trackedStart, '2026-08-05T09:59:59Z'), '2026-08-05T09:59:59Z');
  assert.equal(earliestSessionStart(trackedStart, trackedStart), trackedStart);
  assert.equal(earliestSessionStart(trackedStart, '2026-08-05T10:00:01Z'), trackedStart);
  assert.throws(() => earliestSessionStart(trackedStart, 'invalid'), /valid timestamps/);
});

test('expands session bounds without mutating the tracked session until publication commits', async () => {
  const tracked = {
    startedAt: '2026-08-05T10:00:00Z',
    publishedThrough: '2026-08-05T10:01:00Z'
  };
  const startExpanded = sessionStateForObservedBounds(
    tracked, '2026-08-05T09:59:59Z', tracked.publishedThrough
  );
  assert.equal(startExpanded.boundsExpanded, true);
  assert.equal(startExpanded.candidate.startedAt, '2026-08-05T09:59:59Z');

  const endExpanded = sessionStateForObservedBounds(
    tracked, tracked.startedAt, '2026-08-05T10:01:01Z'
  );
  assert.equal(endExpanded.boundsExpanded, true);
  assert.equal(tracked.startedAt, '2026-08-05T10:00:00Z');

  const unchanged = sessionStateForObservedBounds(
    tracked, tracked.startedAt, tracked.publishedThrough
  );
  assert.equal(unchanged.boundsExpanded, false);
  assert.equal(unchanged.candidate.startedAt, tracked.startedAt);

  const sessions = new Map([['private', tracked]]);
  await assert.rejects(
    publishAndCommitSession(sessions, 'private', startExpanded.candidate, async () => {
      throw new Error('publish failed');
    }),
    /publish failed/
  );
  assert.equal(sessions.get('private'), tracked);
  await publishAndCommitSession(sessions, 'private', startExpanded.candidate, async () => 'published');
  assert.equal(sessions.get('private'), startExpanded.candidate);
});

test('creates stable readable labels without exposing the raw OTel session id', () => {
  const label = sessionExperimentName('private-session-id', '2026-08-05T10:11:12.000Z');
  assert.match(label, /^session-20260805-101112-[a-f0-9]{10}$/);
  assert.doesNotMatch(label, /private-session-id/);
  assert.equal(label, sessionExperimentName('private-session-id', '2026-08-05T10:11:12.000Z'));
});

function usage(aiCostUsd: number): ExperimentUsage {
  return {
    source: 'otel_traces',
    chatSpans: aiCostUsd > 0 ? 2 : 0,
    inputTokens: 20,
    cacheReadTokens: 0,
    uncachedInputTokens: 20,
    outputTokens: 40,
    reasoningTokens: 10,
    aiCredits: aiCostUsd * 100,
    aiCostUsd,
    models: [],
    phases,
    elapsedSeconds: 60,
    engagedSeconds: 60,
    activeSeconds: 20,
    activityDensity: 20 / 60
  };
}

test('continuous windows advance source evidence but publish ROI only when AI cost exists', async () => {
  const config = valueConfigSchema.parse(JSON.parse(await readFile('test/fixtures/value-model.json', 'utf8')));
  const observed = otelSnapshotSchema.parse(JSON.parse(await readFile('test/fixtures/otel-snapshot.json', 'utf8')));
  const baseline: SourceSnapshot = {
    capturedAt: '2026-08-05T10:00:00Z', roots: ['fixture'], files: { '0:app.ts': 'const x = 1;\n' }
  };
  const current: SourceSnapshot = {
    capturedAt: '2026-08-05T10:01:00Z', roots: ['fixture'], files: { '0:app.ts': 'const x = 2;\n' }
  };
  const source: OtelSourceDelta = {
    source: 'otel_source_delta', filesBefore: 1, filesAfter: 1, filesAdded: 0, filesModified: 1,
    filesDeleted: 0, filesRenamed: 0, filesUnchanged: 0, charactersBefore: 13, charactersAfter: 13,
    charactersAdded: 1, charactersRemoved: 1, linesAdded: 1, linesRemoved: 1
  };
  let currentUsage = usage(0);
  let exports = 0;
  const payloads: string[] = [];
  const dependencies: ContinuousWindowDependencies = {
    exportSourceDelta: async () => { exports += 1; },
    collectSourceDelta: async () => source,
    collectSnapshot: async () => observed,
    collectUsage: async () => currentUsage,
    publish: async (payload: string) => { payloads.push(payload); }
  };

  const idle = await evaluateContinuousWindow({
    experiment: 'continuous', baseline, current, startedAt: baseline.capturedAt,
    completedAt: current.capturedAt, config
  }, dependencies);
  assert.equal(idle.status, 'no_ai_usage');
  assert.equal(idle.nextBaseline, current);
  assert.equal(exports, 1);
  assert.equal(payloads.length, 0);

  currentUsage = usage(0.25);
  const measured = await evaluateContinuousWindow({
    experiment: 'continuous', baseline, current, startedAt: baseline.capturedAt,
    completedAt: current.capturedAt, config
  }, dependencies);
  assert.equal(measured.status, 'published');
  assert.equal(measured.benchmark?.retainedSourceCharacters, 1);
  assert.equal(exports, 2);
  assert.equal(payloads.length, 1);
  assert.match(payloads[0] ?? '', /experiment="continuous"/);
});

test('selects the latest pre-session and earliest post-session source snapshots', () => {
  const snapshot = (capturedAt: string): SourceSnapshot => ({ capturedAt, roots: ['fixture'], files: {} });
  const selected = selectSessionSnapshots([
    snapshot('2026-08-05T10:00:00Z'),
    snapshot('2026-08-05T10:00:05Z'),
    snapshot('2026-08-05T10:00:10Z'),
    snapshot('2026-08-05T10:00:15Z')
  ], '2026-08-05T10:00:07Z', '2026-08-05T10:00:12Z');
  assert.equal(selected?.baseline.capturedAt, '2026-08-05T10:00:05Z');
  assert.equal(selected?.current.capturedAt, '2026-08-05T10:00:15Z');
  assert.equal(selectSessionSnapshots(
    [snapshot('2026-08-05T10:00:10Z')],
    '2026-08-05T10:00:07Z',
    '2026-08-05T10:00:12Z'
  ), null);
});

test('publishes repository-agnostic OTel sessions with conservative source evidence', async () => {
  const config = valueConfigSchema.parse(JSON.parse(await readFile('test/fixtures/value-model.json', 'utf8')));
  const payloads: string[] = [];
  const result = await evaluateOtelOnlyWindow({
    experiment: 'cross-repo',
    usage: usage(0.25),
    startedAt: '2026-08-05T10:00:00Z',
    completedAt: '2026-08-05T10:01:00Z',
    config
  }, {
    publish: async (payload) => { payloads.push(payload); }
  });

  assert.equal(result.status, 'published');
  assert.equal(result.source.source, 'otel_only');
  assert.equal(result.source.charactersAdded, 0);
  assert.equal(result.benchmark?.retainedSourceCharacters, 0);
  assert.equal(payloads.length, 1);
  assert.match(payloads[0] ?? '', /copilot_value_experiment_source_evidence_complete\{evidence="otel_only",experiment="cross-repo"\} 0/);
  assert.match(payloads[0] ?? '', /copilot_value_experiment_activity_count\{category="coding_proxy",evidence="proxy",experiment="cross-repo"\} 1/);

  const noCost = await evaluateOtelOnlyWindow({
    experiment: 'cross-repo-no-cost',
    usage: usage(0),
    startedAt: '2026-08-05T11:00:00Z',
    completedAt: '2026-08-05T11:01:00Z',
    config
  }, {
    publish: async (payload) => { payloads.push(payload); }
  });
  assert.equal(noCost.status, 'no_ai_usage');
  assert.equal(noCost.benchmark, null);
  assert.equal(payloads.length, 2);
  assert.match(payloads[1] ?? '', /copilot_value_experiment_ai_cost_usd\{evidence="observed_ai_credits",experiment="cross-repo-no-cost",usage_source="otel_traces"\} 0/);
});

test('keeps observed usage but models no developer value for explicitly automated sessions', async () => {
  const config = valueConfigSchema.parse(JSON.parse(await readFile('test/fixtures/value-model.json', 'utf8')));
  const payloads: string[] = [];
  const automated = await evaluateOtelOnlyWindow({
    experiment: 'sdk-automation',
    usage: usage(0.25),
    startedAt: '2026-08-05T10:00:00Z',
    completedAt: '2026-08-05T10:01:00Z',
    config,
    surface: 'sdk',
    mode: 'automated'
  }, {
    publish: async (payload) => { payloads.push(payload); }
  });

  assert.equal(automated.status, 'excluded_automated');
  assert.equal(automated.exclusionReason, 'automated_session');
  assert.equal(automated.benchmark, null);
  assert.equal(automated.usage.aiCostUsd, 0.25);
  assert.match(payloads[0] ?? '', /copilot_value_experiment_info\{experiment="sdk-automation",session_mode="automated",source="local_otel",surface="sdk"\} 1/);
  assert.match(payloads[0] ?? '', /copilot_value_experiment_ai_cost_usd\{evidence="observed_ai_credits",experiment="sdk-automation",usage_source="otel_traces"\} 0\.25/);
  assert.doesNotMatch(payloads[0] ?? '', /copilot_value_experiment_scenario/);

  const interactive = await evaluateOtelOnlyWindow({
    experiment: 'cli-interactive',
    usage: usage(0.25),
    startedAt: '2026-08-05T10:00:00Z',
    completedAt: '2026-08-05T10:01:00Z',
    config,
    surface: 'cli',
    mode: 'interactive'
  }, {
    publish: async (payload) => { payloads.push(payload); }
  });
  assert.equal(interactive.status, 'published');
  assert.equal(interactive.exclusionReason, undefined);
  assert.notEqual(interactive.benchmark, null);
});

test('changes the session model signature when active assumptions change', async () => {
  const config = valueConfigSchema.parse(
    JSON.parse(await readFile('test/fixtures/value-model.json', 'utf8'))
  );
  const unchanged = valueConfigSchema.parse(structuredClone(config));
  const changed = valueConfigSchema.parse({
    ...structuredClone(config),
    loadedHourlyRateUsd: config.loadedHourlyRateUsd + 1
  });

  assert.equal(modelConfigurationSignature(config), modelConfigurationSignature(unchanged));
  assert.notEqual(modelConfigurationSignature(config), modelConfigurationSignature(changed));
});

test('requires refreshed phase evidence before migrating a retained session', () => {
  const retained = usage(0.25);
  retained.aiCostUsd = 99;
  retained.models = [{
    model: 'model', requests: 2, inputTokens: 20, cacheReadTokens: 0,
    uncachedInputTokens: 20, outputTokens: 40, reasoningTokens: 10,
    aiCredits: 25, aiCostUsd: 99
  }];
  const refreshed = usage(0.25);
  refreshed.phases = {
    ...refreshed.phases,
    coding: { ...refreshed.phases.coding, allocatedSeconds: 12 }
  };
  const trackedStartedAt = '2026-08-05T10:00:00Z';
  const completedAt = '2026-08-05T10:01:00Z';
  const evidence = (
    startedAt = trackedStartedAt,
    endedAt = completedAt
  ): TraceSessionEvidence => ({
    session: { sessionId: 'private', surface: 'cli', mode: 'interactive', startedAt, endedAt, aiCostUsd: 0.25 },
    usage: refreshed
  });

  assert.equal(migrationUsageFromRefreshedEvidence(
    retained, undefined, trackedStartedAt, completedAt
  ), null);
  assert.equal(migrationUsageFromRefreshedEvidence(
    retained, evidence('2026-08-05T10:00:01Z'), trackedStartedAt, completedAt
  ), null);
  const expanded = migrationUsageFromRefreshedEvidence(
    retained, evidence('2026-08-05T09:59:59Z'), trackedStartedAt, completedAt
  );
  assert.equal(expanded?.source, 'otel_traces');
  assert.equal(migrationUsageFromRefreshedEvidence(
    retained, evidence(trackedStartedAt, '2026-08-05T10:00:59Z'),
    trackedStartedAt, completedAt
  ), null);
  assert.notEqual(migrationUsageFromRefreshedEvidence(
    retained, evidence(trackedStartedAt, '2026-08-05T10:01:01Z'),
    trackedStartedAt, completedAt
  ), null);
  const migrated = migrationUsageFromRefreshedEvidence(
    retained, evidence(), trackedStartedAt, completedAt
  );
  assert.equal(migrated?.source, 'otel_traces');
  assert.equal(migrated?.phases.coding.allocatedSeconds, 12);
  assert.equal(migrated?.aiCostUsd, 0.25);
  assert.equal(migrated?.models.length, 0);
  assert.equal(canonicalizeUsageCosts(retained).models[0]?.aiCostUsd, 0.25);
});

test('advances only current artifacts to a newer inbox cursor without changing their values', async (t) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'algalon-artifact-cursor-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const artifactPath = path.join(directory, 'session-20261029-120000-0123456789.json');
  const artifact = {
    calculationVersion: SESSION_CALCULATION_VERSION,
    inboxCursor: 7,
    experiment: 'session-20261029-120000-0123456789',
    status: 'published',
    usage: { aiCostUsd: 0.25 },
    benchmark: null
  };
  await writeFile(artifactPath, `${JSON.stringify(artifact, null, 2)}\n`, 'utf8');
  const read = async () => JSON.parse(await readFile(artifactPath, 'utf8')) as Record<string, unknown>;

  assert.equal(await refreshArtifactInboxCursor(artifactPath, 12), true);
  assert.deepEqual(await read(), { ...artifact, inboxCursor: 12 });
  assert.deepEqual(Object.keys(await read()), Object.keys(artifact));
  assert.deepEqual(await readdir(directory), [path.basename(artifactPath)]);

  for (const cursor of [12, 9, undefined]) {
    assert.equal(await refreshArtifactInboxCursor(artifactPath, cursor), false);
  }
  assert.equal((await read()).inboxCursor, 12);

  const withoutCursor: Record<string, unknown> = { ...artifact };
  delete withoutCursor.inboxCursor;
  await writeFile(artifactPath, JSON.stringify(withoutCursor), 'utf8');
  assert.equal(await refreshArtifactInboxCursor(artifactPath, 3), true);
  assert.equal((await read()).inboxCursor, 3);

  for (const content of [
    JSON.stringify({ ...artifact, calculationVersion: SESSION_CALCULATION_VERSION - 1 }),
    JSON.stringify([artifact]),
    'null',
    '{"truncated":'
  ]) {
    await writeFile(artifactPath, content, 'utf8');
    assert.equal(await refreshArtifactInboxCursor(artifactPath, 99), false, content);
    assert.equal(await readFile(artifactPath, 'utf8'), content);
  }
  assert.equal(await refreshArtifactInboxCursor(path.join(directory, 'missing.json'), 99), false);
});