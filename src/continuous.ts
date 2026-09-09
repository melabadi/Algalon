import { createHash } from 'node:crypto';
import { calculateMechanisticBenchmark, conservativeQualityFactor, type BenchmarkResult } from './benchmark.js';
import {
  collectTraceUsage,
  reuseDirectTurnUsage,
  type ExperimentUsage,
  type TraceSessionEvidence
} from './experiment-evidence.js';
import { buildExperimentMetricPayload, publishMetrics } from './metrics.js';
import { collectOtelSnapshot } from './otel.js';
import type { OtelSnapshot, ValueConfig } from './schema.js';
import {
  calculateSourceDelta,
  collectOtelSourceDelta,
  exportSourceDeltaToOtel,
  type OtelSourceDelta,
  type SourceDelta,
  type SourceSnapshot
} from './source-delta.js';

export const SESSION_CALCULATION_VERSION = 8;

export function modelConfigurationSignature(config: ValueConfig): string {
  return createHash('sha256').update(JSON.stringify({
    acknowledgedAssumptions: config.benchmark.acknowledgedAssumptions,
    maxIdleGapSeconds: config.benchmark.maxIdleGapSeconds,
    phaseToolPatterns: config.benchmark.phaseToolPatterns,
    ...benchmarkConfigFrom(config)
  })).digest('hex');
}

export function sessionCoverageRegressed(trackedStartedAt: string, observedStartedAt: string): boolean {
  const trackedMilliseconds = Date.parse(trackedStartedAt);
  const observedMilliseconds = Date.parse(observedStartedAt);
  if (!Number.isFinite(trackedMilliseconds) || !Number.isFinite(observedMilliseconds)) {
    throw new Error('Session coverage requires valid timestamps.');
  }
  return observedMilliseconds > trackedMilliseconds;
}

export function earliestSessionStart(trackedStartedAt: string, observedStartedAt: string): string {
  const trackedMilliseconds = Date.parse(trackedStartedAt);
  const observedMilliseconds = Date.parse(observedStartedAt);
  if (!Number.isFinite(trackedMilliseconds) || !Number.isFinite(observedMilliseconds)) {
    throw new Error('Session coverage requires valid timestamps.');
  }
  return observedMilliseconds < trackedMilliseconds ? observedStartedAt : trackedStartedAt;
}

export function sessionStateForObservedBounds<T extends {
  startedAt: string;
  publishedThrough?: string;
  directLogSignature?: string | null;
  directUsage?: ExperimentUsage;
}>(tracked: T, observedStartedAt: string, observedEndedAt: string): {
  candidate: T;
  boundsExpanded: boolean;
} {
  const startedAt = earliestSessionStart(tracked.startedAt, observedStartedAt);
  const observedEndMilliseconds = Date.parse(observedEndedAt);
  const publishedEndMilliseconds = tracked.publishedThrough === undefined
    ? observedEndMilliseconds
    : Date.parse(tracked.publishedThrough);
  if (!Number.isFinite(observedEndMilliseconds) || !Number.isFinite(publishedEndMilliseconds)) {
    throw new Error('Session coverage requires valid timestamps.');
  }
  const candidate = { ...tracked, startedAt };
  const boundsExpanded = startedAt !== tracked.startedAt ||
    observedEndMilliseconds > publishedEndMilliseconds;
  if (boundsExpanded) {
    delete candidate.directLogSignature;
    delete candidate.directUsage;
  }
  return { candidate, boundsExpanded };
}

export async function publishAndCommitSession<T, R>(
  tracked: Map<string, T>,
  sessionId: string,
  candidate: T,
  publish: (candidate: T) => Promise<R>
): Promise<R> {
  const result = await publish(candidate);
  tracked.set(sessionId, candidate);
  return result;
}

export function sessionNeedsEvaluation(
  publishedThrough: string | undefined,
  calculationVersion: number | undefined,
  endedAt: string,
  publishedUsageSignature?: string,
  usageSignature?: string,
  publishedModelConfigurationSignature?: string,
  currentModelConfigurationSignature?: string,
  sessionStartExpanded = false
): boolean {
  return sessionStartExpanded || publishedThrough !== endedAt ||
    calculationVersion !== SESSION_CALCULATION_VERSION ||
    publishedUsageSignature !== usageSignature ||
    publishedModelConfigurationSignature !== currentModelConfigurationSignature;
}

export function migrationUsageFromRefreshedEvidence(
  _retainedUsage: ExperimentUsage,
  refreshed: TraceSessionEvidence | undefined,
  trackedStartedAt: string,
  completedAt: string
): ExperimentUsage | null {
  if (!refreshed) return null;
  const trackedStart = Date.parse(trackedStartedAt);
  const completed = Date.parse(completedAt);
  const observedStart = Date.parse(refreshed.session.startedAt);
  const observedEnd = Date.parse(refreshed.session.endedAt);
  if (
    ![trackedStart, completed, observedStart, observedEnd].every(Number.isFinite)
    || observedStart > trackedStart
    || observedEnd < completed
  ) return null;
  return canonicalizeUsageCosts(refreshed.usage);
}

export function canonicalizeUsageCosts(usage: ExperimentUsage): ExperimentUsage {
  return {
    ...usage,
    aiCostUsd: usage.aiCredits / 100,
    models: usage.models.map((model) => ({
      ...model,
      aiCostUsd: model.aiCredits / 100
    }))
  };
}

export interface ContinuousWindowInput {
  experiment: string;
  sessionId?: string;
  usage?: ExperimentUsage;
  baseline: SourceSnapshot;
  current: SourceSnapshot;
  startedAt: string;
  completedAt: string;
  config: ValueConfig;
}

export interface ContinuousWindowDependencies {
  exportSourceDelta: (
    config: ValueConfig,
    experiment: string,
    startedAt: string,
    completedAt: string,
    delta: SourceDelta
  ) => Promise<void>;
  collectSourceDelta: (
    config: ValueConfig,
    experiment: string,
    startedAt: string,
    completedAt: string
  ) => Promise<OtelSourceDelta>;
  collectSnapshot: (
    config: ValueConfig,
    startedAt: string,
    completedAt: string
  ) => Promise<OtelSnapshot>;
  collectUsage: (
    config: ValueConfig,
    startedAt: string,
    completedAt: string,
    sessionId?: string
  ) => Promise<ExperimentUsage>;
  publish: (payload: string, config: ValueConfig) => Promise<void>;
  stage?: (stage: string) => void;
}

export interface ContinuousWindowResult {
  status: 'no_ai_usage' | 'insufficient_phase_evidence' | 'published';
  nextBaseline: SourceSnapshot;
  source: OtelSourceDelta;
  usage: ExperimentUsage;
  benchmark: BenchmarkResult | null;
}

function benchmarkPhaseInput(usage: ExperimentUsage): Parameters<typeof calculateMechanisticBenchmark>[0]['phases'] {
  return Object.fromEntries(Object.entries(usage.phases).map(([phase, evidence]) => [phase, {
    measuredAiSeconds: evidence.allocatedSeconds,
    uncachedInputTokens: evidence.uncachedInputTokens,
    outputTokens: evidence.outputTokens,
    reasoningTokens: evidence.reasoningTokens,
    toolCalls: evidence.toolCalls,
    toolActiveSeconds: evidence.toolActiveSeconds
  }])) as Parameters<typeof calculateMechanisticBenchmark>[0]['phases'];
}

function benchmarkConfigFrom(config: ValueConfig): Parameters<typeof calculateMechanisticBenchmark>[1] {
  return {
    loadedHourlyRateUsd: config.loadedHourlyRateUsd,
    capacityRealization: config.benchmark.capacityRealization,
    capacityRealizationBand: config.benchmark.capacityRealizationBand,
    typingWordsPerMinute: config.benchmark.typingWordsPerMinute,
    charactersPerWord: config.benchmark.charactersPerWord,
    scenarios: config.benchmark.scenarios
  };
}

/** No allocated phase time means no observed activity to value, so no ROI is modeled. */
function hasAllocatedPhaseTime(usage: ExperimentUsage): boolean {
  return Object.values(usage.phases).some((evidence) => evidence.allocatedSeconds > 0);
}

const defaultDependencies: ContinuousWindowDependencies = {
  exportSourceDelta: async (config, experiment, startedAt, completedAt, delta) => {
    await exportSourceDeltaToOtel(
      config.otelHttpEndpoint,
      config.victoriaMetricsUrl,
      experiment,
      startedAt,
      completedAt,
      delta
    );
  },
  collectSourceDelta: (config, experiment, startedAt, completedAt) => collectOtelSourceDelta(
    config.victoriaMetricsUrl,
    experiment,
    { startedAt, completedAt }
  ),
  collectSnapshot: (config, startedAt, completedAt) => collectOtelSnapshot(
    startedAt.slice(0, 10),
    {
      victoriaMetricsUrl: config.victoriaMetricsUrl,
      startTime: startedAt,
      codingToolPatterns: config.codingToolPatterns,
      researchToolPatterns: config.researchToolPatterns,
      planningToolPatterns: config.planningToolPatterns,
      acceptedDecisionValues: config.acceptedDecisionValues,
      rejectedDecisionValues: config.rejectedDecisionValues
    },
    new Date(completedAt)
  ),
  collectUsage: (config, startedAt, completedAt, sessionId) => collectTraceUsage(
    config.traceArchivePath,
    new Date(startedAt),
    new Date(completedAt),
    config.benchmark.phaseToolPatterns,
    sessionId,
    config.benchmark.maxIdleGapSeconds
  ),
  publish: (payload, config) => publishMetrics(payload, config.victoriaMetricsUrl)
};

export function sessionExperimentName(sessionId: string, startedAt: string): string {
  const timestamp = new Date(startedAt).toISOString()
    .replace(/[-:]/g, '')
    .replace('T', '-')
    .slice(0, 15);
  const digest = createHash('sha256').update(sessionId).digest('hex').slice(0, 10);
  return `session-${timestamp}-${digest}`;
}

export function selectSessionSnapshots(
  snapshots: SourceSnapshot[],
  startedAt: string,
  endedAt: string
): { baseline: SourceSnapshot; current: SourceSnapshot } | null {
  const startMilliseconds = Date.parse(startedAt);
  const endMilliseconds = Date.parse(endedAt);
  const baseline = snapshots
    .filter((snapshot) => Date.parse(snapshot.capturedAt) <= startMilliseconds)
    .sort((left, right) => right.capturedAt.localeCompare(left.capturedAt))[0];
  const current = snapshots
    .filter((snapshot) => Date.parse(snapshot.capturedAt) >= endMilliseconds)
    .sort((left, right) => left.capturedAt.localeCompare(right.capturedAt))[0];
  return baseline && current ? { baseline, current } : null;
}

export async function evaluateContinuousWindow(
  input: ContinuousWindowInput,
  dependencies: ContinuousWindowDependencies = defaultDependencies
): Promise<ContinuousWindowResult> {
  const completedAt = new Date(input.completedAt);
  if (!Number.isFinite(completedAt.getTime()) || Date.parse(input.startedAt) >= completedAt.getTime()) {
    throw new Error('Continuous measurement window is invalid.');
  }
  const delta = calculateSourceDelta(input.baseline, input.current);
  dependencies.stage?.('source_export_started');
  await dependencies.exportSourceDelta(
    input.config,
    input.experiment,
    input.startedAt,
    input.completedAt,
    delta
  );
  dependencies.stage?.('source_export_completed');
  dependencies.stage?.('evidence_collection_started');
  const [source, observed, usage] = await Promise.all([
    dependencies.collectSourceDelta(
      input.config,
      input.experiment,
      input.startedAt,
      input.completedAt
    ),
    dependencies.collectSnapshot(input.config, input.startedAt, input.completedAt),
    input.usage ?? dependencies.collectUsage(input.config, input.startedAt, input.completedAt, input.sessionId)
  ]);
  dependencies.stage?.('evidence_collection_completed');

  if (usage.aiCostUsd <= 0) {
    return { status: 'no_ai_usage', nextBaseline: input.current, source, usage, benchmark: null };
  }
  if (!hasAllocatedPhaseTime(usage)) {
    return { status: 'insufficient_phase_evidence', nextBaseline: input.current, source, usage, benchmark: null };
  }

  const qualityFactor = conservativeQualityFactor(
    observed.editSurvivalNoRevert,
    observed.editSurvivalFourGram
  );
  const benchmark = input.config.benchmark.acknowledgedAssumptions
    ? calculateMechanisticBenchmark({
        phases: benchmarkPhaseInput(usage),
        aiCostUsd: usage.aiCostUsd,
        qualityFactor,
        retainedSourceCharacters: source.charactersAdded
      }, benchmarkConfigFrom(input.config))
    : null;
  const payload = buildExperimentMetricPayload(
    observed,
    usage,
    source,
    benchmark,
    input.experiment,
    input.startedAt,
    completedAt,
    new Date()
  );
  dependencies.stage?.('metric_publish_started');
  await dependencies.publish(payload, input.config);
  dependencies.stage?.('metric_publish_completed');
  return { status: 'published', nextBaseline: input.current, source, usage, benchmark };
}

export interface OtelOnlyWindowInput {
  experiment: string;
  usage: ExperimentUsage;
  startedAt: string;
  completedAt: string;
  config: ValueConfig;
}

export interface OtelOnlyWindowDependencies {
  publish: (payload: string, config: ValueConfig) => Promise<void>;
}

const emptySource: OtelSourceDelta = {
  source: 'otel_only',
  filesBefore: 0,
  filesAfter: 0,
  filesAdded: 0,
  filesModified: 0,
  filesDeleted: 0,
  filesRenamed: 0,
  filesUnchanged: 0,
  charactersBefore: 0,
  charactersAfter: 0,
  charactersAdded: 0,
  charactersRemoved: 0,
  linesAdded: 0,
  linesRemoved: 0
};

function sessionSnapshot(usage: ExperimentUsage, startedAt: string): OtelSnapshot {
  const toolCalls = Object.values(usage.phases).reduce((total, phase) => total + phase.toolCalls, 0);
  return {
    day: startedAt.slice(0, 10),
    activeDay: 1,
    sessions: 1,
    agentInvocations: usage.chatSpans,
    agentTurns: usage.chatSpans,
    toolCalls,
    successfulToolCalls: toolCalls,
    codingToolCalls: usage.phases.coding.toolCalls,
    researchToolCalls: usage.phases.research.toolCalls,
    planningToolCalls: usage.phases.planning.toolCalls,
    unmappedToolCalls: usage.phases.unclassified.toolCalls,
    acceptedEditDecisions: 0,
    rejectedEditDecisions: 0,
    unmappedEditDecisions: 0,
    agentEditLoc: 0,
    appliedUserActions: 0,
    editSurvivalFourGram: null,
    editSurvivalNoRevert: null
  };
}

export async function evaluateOtelOnlyWindow(
  input: OtelOnlyWindowInput,
  dependencies: OtelOnlyWindowDependencies
): Promise<ContinuousWindowResult> {
  const completedAt = new Date(input.completedAt);
  if (!Number.isFinite(completedAt.getTime()) || Date.parse(input.startedAt) >= completedAt.getTime()) {
    throw new Error('OTel-only session window is invalid.');
  }
  const observed = sessionSnapshot(input.usage, input.startedAt);
  const qualityFactor = conservativeQualityFactor(
    observed.editSurvivalNoRevert,
    observed.editSurvivalFourGram
  );
  const benchmark = input.usage.aiCostUsd > 0 && hasAllocatedPhaseTime(input.usage) &&
    input.config.benchmark.acknowledgedAssumptions
    ? calculateMechanisticBenchmark({
        phases: benchmarkPhaseInput(input.usage),
        aiCostUsd: input.usage.aiCostUsd,
        qualityFactor,
        retainedSourceCharacters: 0
      }, benchmarkConfigFrom(input.config))
    : null;
  const payload = buildExperimentMetricPayload(
    observed,
    input.usage,
    emptySource,
    benchmark,
    input.experiment,
    input.startedAt,
    completedAt,
    new Date()
  );
  await dependencies.publish(payload, input.config);
  return {
    status: input.usage.aiCostUsd > 0 ? 'published' : 'no_ai_usage',
    nextBaseline: { capturedAt: input.completedAt, roots: ['otel-only'], files: {} },
    source: emptySource,
    usage: input.usage,
    benchmark
  };
}