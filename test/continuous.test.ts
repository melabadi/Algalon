import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import {
  canonicalizeUsageCosts,
  earliestSessionStart,
  evaluateContinuousWindow,
  evaluateOtelOnlyWindow,
  migrationUsageFromRefreshedEvidence,
  modelConfigurationSignature,
  publishAndCommitSession,
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

test('invalidates cached direct usage when either session bound expands', async () => {
  const directUsage = usage(0.25);
  const tracked = {
    startedAt: '2026-08-05T10:00:00Z',
    publishedThrough: '2026-08-05T10:01:00Z',
    directLogSignature: 'old-signature',
    directUsage
  };
  const startExpanded = sessionStateForObservedBounds(
    tracked, '2026-08-05T09:59:59Z', tracked.publishedThrough
  );
  assert.equal(startExpanded.boundsExpanded, true);
  assert.equal(startExpanded.candidate.startedAt, '2026-08-05T09:59:59Z');
  assert.equal(startExpanded.candidate.directLogSignature, undefined);
  assert.equal(startExpanded.candidate.directUsage, undefined);

  const endExpanded = sessionStateForObservedBounds(
    tracked, tracked.startedAt, '2026-08-05T10:01:01Z'
  );
  assert.equal(endExpanded.boundsExpanded, true);
  assert.equal(endExpanded.candidate.directLogSignature, undefined);
  assert.equal(endExpanded.candidate.directUsage, undefined);
  assert.equal(tracked.startedAt, '2026-08-05T10:00:00Z');
  assert.equal(tracked.directLogSignature, 'old-signature');
  assert.equal(tracked.directUsage, directUsage);

  const unchanged = sessionStateForObservedBounds(
    tracked, tracked.startedAt, tracked.publishedThrough
  );
  assert.equal(unchanged.boundsExpanded, false);
  assert.equal(unchanged.candidate.directLogSignature, 'old-signature');
  assert.equal(unchanged.candidate.directUsage, directUsage);

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
  retained.source = 'copilot_turn_log';
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
    session: { sessionId: 'private', startedAt, endedAt, aiCostUsd: 0.25 },
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