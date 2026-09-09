import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { calculateMechanisticBenchmark } from '../src/benchmark.js';
import { calculateValue } from '../src/model.js';
import { buildExperimentMetricPayload, buildMetricPayload } from '../src/metrics.js';
import { otelSnapshotSchema, valueConfigSchema } from '../src/schema.js';

test('publishes evidence-labeled OTel value metrics without GitHub identity or token data', async () => {
  const snapshot = otelSnapshotSchema.parse(JSON.parse(await readFile('test/fixtures/otel-snapshot.json', 'utf8')));
  const config = valueConfigSchema.parse(JSON.parse(await readFile('test/fixtures/value-model.json', 'utf8')));
  const payload = buildMetricPayload(calculateValue(snapshot, config), config);

  assert.match(payload, /copilot_value_variable_cost_usd\{evidence="allocated",source="manual_config"\}/);
  assert.match(payload, /copilot_value_activity_count\{activity="successful_tool_call",category="coding_proxy",evidence="proxy"\}/);
  assert.match(payload, /copilot_value_activity_count\{activity="successful_tool_call",category="research_proxy",evidence="proxy"\}/);
  assert.match(payload, /copilot_value_estimated_roi_ratio\{evidence="estimated",scenario="base"\}/);
  assert.match(payload, /copilot_value_source_info\{source="local_otel"\}/);
  assert.doesNotMatch(payload, /githubLogin|enterprise|GH_ENTERPRISE_TOKEN|netAmount/i);
});

test('publishes named experiment ROI separately from daily value metrics', async () => {
  const snapshot = otelSnapshotSchema.parse(JSON.parse(await readFile('test/fixtures/otel-snapshot.json', 'utf8')));
  const config = valueConfigSchema.parse(JSON.parse(await readFile('test/fixtures/value-model.json', 'utf8')));
  const usage = {
    source: 'otel_traces' as const,
    chatSpans: 1,
    inputTokens: 100,
    cacheReadTokens: 80,
    uncachedInputTokens: 20,
    outputTokens: 20,
    reasoningTokens: 5,
    aiCredits: 2.5,
    aiCostUsd: 0.025,
    models: [{ model: 'gpt-test', requests: 1, inputTokens: 100, cacheReadTokens: 80, uncachedInputTokens: 20, outputTokens: 20, reasoningTokens: 5, aiCredits: 2.5, aiCostUsd: 0.025 }],
    phases: {
      planning: { activeSeconds: 10, allocatedSeconds: 120, toolActiveSeconds: 1, toolCalls: 1, modelSpans: 1, uncachedInputTokens: 0, outputTokens: 100, reasoningTokens: 20 },
      research: { activeSeconds: 20, allocatedSeconds: 180, toolActiveSeconds: 2, toolCalls: 2, modelSpans: 1, uncachedInputTokens: 200, outputTokens: 20, reasoningTokens: 0 },
      coding: { activeSeconds: 30, allocatedSeconds: 900, toolActiveSeconds: 1, toolCalls: 3, modelSpans: 2, uncachedInputTokens: 20, outputTokens: 500, reasoningTokens: 50 },
      validation: { activeSeconds: 20, allocatedSeconds: 480, toolActiveSeconds: 10, toolCalls: 2, modelSpans: 1, uncachedInputTokens: 100, outputTokens: 20, reasoningTokens: 10 },
      unclassified: { activeSeconds: 10, allocatedSeconds: 120, toolActiveSeconds: 0, toolCalls: 1, modelSpans: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0 }
    },
    elapsedSeconds: 1800,
    engagedSeconds: 1800,
    activeSeconds: 90,
    activityDensity: 90 / 1800
  };
  const source = {
    source: 'otel_source_delta' as const,
    filesBefore: 1,
    filesAfter: 2,
    filesAdded: 1,
    filesModified: 1,
    filesDeleted: 0,
    filesRenamed: 0,
    filesUnchanged: 0,
    charactersBefore: 100,
    charactersAfter: 1100,
    charactersAdded: 1000,
    charactersRemoved: 0,
    linesAdded: 20,
    linesRemoved: 0
  };
  const benchmark = calculateMechanisticBenchmark({
    phases: Object.fromEntries(Object.entries(usage.phases).map(([phase, evidence]) => [phase, {
      measuredAiSeconds: evidence.allocatedSeconds,
      uncachedInputTokens: evidence.uncachedInputTokens,
      outputTokens: evidence.outputTokens,
      reasoningTokens: evidence.reasoningTokens,
      toolCalls: evidence.toolCalls,
      toolActiveSeconds: evidence.toolActiveSeconds
    }])) as Parameters<typeof calculateMechanisticBenchmark>[0]['phases'],
    aiCostUsd: usage.aiCostUsd,
    qualityFactor: 0.8,
    retainedSourceCharacters: source.charactersAdded
  }, {
    loadedHourlyRateUsd: config.loadedHourlyRateUsd,
    capacityRealization: config.benchmark.capacityRealization,
    typingWordsPerMinute: config.benchmark.typingWordsPerMinute,
    charactersPerWord: config.benchmark.charactersPerWord,
    scenarios: config.benchmark.scenarios
  });
  const payload = buildExperimentMetricPayload(
    snapshot,
    usage,
    source,
    benchmark,
    'cupcake-rebuild',
    '2026-08-04T12:00:00Z',
    new Date('2026-08-04T12:30:00Z')
  );

  assert.match(payload, /copilot_value_experiment_activity_count\{category="coding_proxy",evidence="proxy",experiment="cupcake-rebuild"\}/);
  assert.match(payload, /copilot_value_experiment_ai_cost_usd\{evidence="observed_ai_credits",experiment="cupcake-rebuild",usage_source="otel_traces"\}/);
  assert.match(payload, /copilot_value_experiment_token_count\{evidence="observed",experiment="cupcake-rebuild",token_type="cache_read"\}/);
  assert.match(payload, /copilot_value_experiment_phase_seconds\{evidence="derived",experiment="cupcake-rebuild",phase="research",time_type="allocated_engaged"\}/);
  assert.match(payload, /copilot_value_experiment_phase_token_count\{evidence="observed",experiment="cupcake-rebuild",phase="validation",token_type="uncached_input"\}/);
  assert.match(payload, /copilot_value_experiment_completion_timestamp_seconds\{experiment="cupcake-rebuild"\}/);
  assert.match(payload, /copilot_value_experiment_retained_source_characters\{evidence="otel_source_delta",experiment="cupcake-rebuild"\} 1000/);
  assert.match(payload, /copilot_value_experiment_source_evidence_complete\{evidence="otel_source_delta",experiment="cupcake-rebuild"\} 1/);
  assert.doesNotMatch(payload, /copilot_value_experiment_source_(characters|lines|files)\{/);
  assert.match(payload, /copilot_value_experiment_task_time_reduction_ratio\{evidence="derived_manual_model",experiment="cupcake-rebuild",scenario="base"\}/);
  assert.match(payload, /copilot_value_experiment_estimated_manual_labor_cost_usd\{evidence="estimated",experiment="cupcake-rebuild",scenario="base"\}/);
  assert.match(payload, /copilot_value_experiment_estimated_ai_assisted_total_cost_usd\{evidence="estimated",experiment="cupcake-rebuild",scenario="base"\}/);
  assert.match(payload, /copilot_value_experiment_estimated_gross_cost_savings_usd\{evidence="estimated",experiment="cupcake-rebuild",scenario="base"\}/);
  assert.match(payload, /copilot_value_experiment_phase_estimated_minutes_saved\{evidence="estimated",experiment="cupcake-rebuild",phase="planning",scenario="base"\}/);
  assert.doesNotMatch(payload, /quality_adjusted/);
  assert.match(payload, /copilot_value_experiment_estimated_roi_ratio\{evidence="estimated",experiment="cupcake-rebuild",scenario="base"\}/);
  assert.doesNotMatch(payload, /copilot_value_estimated_roi_ratio\{/);
});