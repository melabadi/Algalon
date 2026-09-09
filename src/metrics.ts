import type { Scenario, ValueConfig } from './schema.js';
import type { BenchmarkResult } from './benchmark.js';
import type { ExperimentUsage } from './experiment-evidence.js';
import type { ValueResult } from './model.js';
import type { OtelSnapshot } from './schema.js';
import type { OtelSourceDelta } from './source-delta.js';

interface MetricSample {
  name: string;
  value: number;
  labels?: Record<string, string>;
}

function escapeLabel(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/"/g, '\\"');
}

function metricLine(sample: MetricSample, timestampMs: number): string {
  const labels = sample.labels && Object.keys(sample.labels).length > 0
    ? `{${Object.entries(sample.labels).sort(([left], [right]) => left.localeCompare(right)).map(([key, value]) => `${key}="${escapeLabel(value)}"`).join(',')}}`
    : '';
  return `${sample.name}${labels} ${sample.value} ${timestampMs}`;
}

function scenarioAssumptionSamples(name: string, scenario: Scenario): MetricSample[] {
  return [
    ['accepted_edit', scenario.minutesPerAcceptedEdit],
    ['agent_edit_loc', scenario.minutesPerAgentEditLoc],
    ['coding_tool_call', scenario.minutesPerCodingToolCall],
    ['research_tool_call', scenario.minutesPerResearchToolCall],
    ['planning_tool_call', scenario.minutesPerPlanningToolCall]
  ].map(([driver, value]) => ({
    name: 'copilot_value_assumption_minutes_per_unit',
    value: value as number,
    labels: { scenario: name, driver: driver as string, evidence: 'estimated' }
  }));
}

export function buildMetricPayload(result: ValueResult, config: ValueConfig): string {
  const samples: MetricSample[] = [
    { name: 'copilot_value_cash_cost_usd', value: result.costs.cashCostUsd, labels: { evidence: 'allocated' } },
    { name: 'copilot_value_seat_cost_usd', value: result.costs.seatAllocatedUsd, labels: { evidence: 'allocated' } },
    { name: 'copilot_value_enablement_cost_usd', value: result.costs.enablementAllocatedUsd, labels: { evidence: 'allocated' } },
    { name: 'copilot_value_variable_cost_usd', value: result.costs.variableAllocatedUsd, labels: { evidence: 'allocated', source: 'manual_config' } },
    { name: 'copilot_value_break_even_minutes', value: result.costs.breakEvenMinutes, labels: { evidence: 'derived' } },
    { name: 'copilot_value_loaded_hourly_rate_usd', value: config.loadedHourlyRateUsd, labels: { evidence: 'allocated' } },
    { name: 'copilot_value_active_day', value: result.observed.activeDay, labels: { evidence: 'observed' } },
    { name: 'copilot_value_activity_count', value: result.observed.sessions, labels: { category: 'usage', activity: 'session', evidence: 'observed' } },
    { name: 'copilot_value_activity_count', value: result.observed.agentInvocations, labels: { category: 'usage', activity: 'agent_invocation', evidence: 'observed' } },
    { name: 'copilot_value_activity_count', value: result.observed.agentTurns, labels: { category: 'usage', activity: 'agent_turn', evidence: 'observed' } },
    { name: 'copilot_value_activity_count', value: result.observed.acceptedEditDecisions, labels: { category: 'coding', activity: 'accepted_edit', evidence: 'observed' } },
    { name: 'copilot_value_activity_count', value: result.observed.rejectedEditDecisions, labels: { category: 'coding', activity: 'rejected_edit', evidence: 'observed' } },
    { name: 'copilot_value_loc_count', value: result.observed.agentEditLoc, labels: { category: 'coding', activity: 'agent_edit', evidence: 'observed' } },
    { name: 'copilot_value_activity_count', value: result.observed.codingToolCalls, labels: { category: 'coding_proxy', activity: 'successful_tool_call', evidence: 'proxy' } },
    { name: 'copilot_value_activity_count', value: result.observed.researchToolCalls, labels: { category: 'research_proxy', activity: 'successful_tool_call', evidence: 'proxy' } },
    { name: 'copilot_value_activity_count', value: result.observed.planningToolCalls, labels: { category: 'planning_proxy', activity: 'successful_tool_call', evidence: 'proxy' } },
    { name: 'copilot_value_activity_count', value: result.observed.unmappedToolCalls, labels: { category: 'unmapped', activity: 'successful_tool_call', evidence: 'observed' } },
    { name: 'copilot_value_activity_count', value: result.observed.appliedUserActions, labels: { category: 'engagement', activity: 'apply_insert_copy', evidence: 'observed' } },
    { name: 'copilot_value_assumptions_acknowledged', value: result.metadata.assumptionsAcknowledged ? 1 : 0 },
    { name: 'copilot_value_source_info', value: 1, labels: { source: result.metadata.source } }
  ];

  if (result.observed.editSurvivalFourGram !== null) {
    samples.push({ name: 'copilot_value_edit_survival_ratio', value: result.observed.editSurvivalFourGram, labels: { method: 'four_gram', evidence: 'observed' } });
  }
  if (result.observed.editSurvivalNoRevert !== null) {
    samples.push({ name: 'copilot_value_edit_survival_ratio', value: result.observed.editSurvivalNoRevert, labels: { method: 'no_revert', evidence: 'observed' } });
  }

  samples.push({
    name: 'copilot_value_calculation_timestamp_seconds',
    value: Math.floor(Date.now() / 1000),
    labels: { source: 'local_otel', measurement_day: result.measurementDay }
  });

  if (result.scenarios) {
    for (const scenarioName of ['pessimistic', 'base', 'optimistic'] as const) {
      const scenario = result.scenarios[scenarioName];
      samples.push(
        { name: 'copilot_value_estimated_minutes_saved', value: scenario.estimatedMinutesSaved, labels: { scenario: scenarioName, evidence: 'estimated' } },
        { name: 'copilot_value_estimated_hours_saved', value: scenario.estimatedHoursSaved, labels: { scenario: scenarioName, evidence: 'estimated' } },
        { name: 'copilot_value_estimated_benefit_usd', value: scenario.estimatedBenefitUsd, labels: { scenario: scenarioName, evidence: 'estimated' } },
        { name: 'copilot_value_estimated_net_value_usd', value: scenario.netValueUsd, labels: { scenario: scenarioName, evidence: 'estimated' } },
        { name: 'copilot_value_capacity_realization_ratio', value: config.scenarios[scenarioName].capacityRealization, labels: { scenario: scenarioName, evidence: 'estimated' } }
      );
      if (scenario.roi !== null) {
        samples.push({ name: 'copilot_value_estimated_roi_ratio', value: scenario.roi, labels: { scenario: scenarioName, evidence: 'estimated' } });
      }
      samples.push(...scenarioAssumptionSamples(scenarioName, config.scenarios[scenarioName]));
    }
  }

  const timestampMs = Date.parse(`${result.measurementDay}T00:00:00Z`);
  return `${samples.map((sample) => metricLine(sample, timestampMs)).join('\n')}\n`;
}

export async function publishMetrics(payload: string, victoriaMetricsUrl: string): Promise<void> {
  const endpoint = new URL('/api/v1/import/prometheus', victoriaMetricsUrl);
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    body: payload,
    signal: AbortSignal.timeout(10_000)
  });
  if (!response.ok) {
    throw new Error(`VictoriaMetrics import failed (${response.status}): ${(await response.text()).slice(0, 500)}`);
  }
}

export function buildExperimentMetricPayload(
  observed: OtelSnapshot,
  usage: ExperimentUsage,
  source: OtelSourceDelta,
  benchmark: BenchmarkResult | null,
  experiment: string,
  startedAt: string,
  completedAt: Date,
  calculatedAt = completedAt
): string {
  const labels = { experiment };
  const samples: MetricSample[] = [
    { name: 'copilot_value_experiment_info', value: 1, labels: { ...labels, source: 'local_otel' } },
    { name: 'copilot_value_experiment_start_timestamp_seconds', value: Math.floor(Date.parse(startedAt) / 1000), labels },
    { name: 'copilot_value_experiment_completion_timestamp_seconds', value: Math.floor(completedAt.getTime() / 1000), labels },
    { name: 'copilot_value_experiment_calculation_timestamp_seconds', value: Math.floor(calculatedAt.getTime() / 1000), labels },
    { name: 'copilot_value_experiment_duration_seconds', value: Math.max(0, (completedAt.getTime() - Date.parse(startedAt)) / 1000), labels },
    { name: 'copilot_value_experiment_ai_cost_usd', value: usage.aiCostUsd, labels: { ...labels, evidence: 'observed_ai_credits', usage_source: usage.source } },
    { name: 'copilot_value_experiment_ai_credits', value: usage.aiCredits, labels: { ...labels, evidence: 'observed', usage_source: usage.source } },
    { name: 'copilot_value_experiment_token_count', value: usage.inputTokens, labels: { ...labels, token_type: 'input', evidence: 'observed' } },
    { name: 'copilot_value_experiment_token_count', value: usage.cacheReadTokens, labels: { ...labels, token_type: 'cache_read', evidence: 'observed' } },
    { name: 'copilot_value_experiment_token_count', value: usage.uncachedInputTokens, labels: { ...labels, token_type: 'uncached_input', evidence: 'derived' } },
    { name: 'copilot_value_experiment_token_count', value: usage.outputTokens, labels: { ...labels, token_type: 'output', evidence: 'observed' } },
    { name: 'copilot_value_experiment_token_count', value: usage.reasoningTokens, labels: { ...labels, token_type: 'reasoning', evidence: 'observed' } },
    { name: 'copilot_value_experiment_retained_source_characters', value: source.charactersAdded, labels: { ...labels, evidence: source.source } },
    { name: 'copilot_value_experiment_source_evidence_complete', value: source.source === 'otel_source_delta' ? 1 : 0, labels: { ...labels, evidence: source.source } },
    { name: 'copilot_value_experiment_activity_count', value: observed.codingToolCalls, labels: { ...labels, category: 'coding_proxy', evidence: 'proxy' } },
    { name: 'copilot_value_experiment_activity_count', value: observed.researchToolCalls, labels: { ...labels, category: 'research_proxy', evidence: 'proxy' } },
    { name: 'copilot_value_experiment_activity_count', value: observed.planningToolCalls, labels: { ...labels, category: 'planning_proxy', evidence: 'proxy' } },
    { name: 'copilot_value_experiment_activity_count', value: observed.acceptedEditDecisions, labels: { ...labels, category: 'accepted_edit', evidence: 'observed' } },
    { name: 'copilot_value_experiment_loc_count', value: observed.agentEditLoc, labels: { ...labels, evidence: 'observed' } },
    { name: 'copilot_value_experiment_session_seconds', value: usage.elapsedSeconds, labels: { ...labels, time_type: 'elapsed_window', evidence: 'observed' } },
    { name: 'copilot_value_experiment_session_seconds', value: usage.activeSeconds, labels: { ...labels, time_type: 'active_span_union', evidence: 'observed' } },
    { name: 'copilot_value_experiment_session_seconds', value: usage.engagedSeconds, labels: { ...labels, time_type: 'engaged_gap_bounded', evidence: 'derived' } },
    { name: 'copilot_value_experiment_activity_density_ratio', value: usage.activityDensity, labels: { ...labels, evidence: 'derived' } }
  ];

  for (const model of usage.models) {
    const modelLabels = { ...labels, model: model.model };
    samples.push(
      { name: 'copilot_value_experiment_model_request_count', value: model.requests, labels: { ...modelLabels, evidence: 'observed' } },
      { name: 'copilot_value_experiment_model_ai_cost_usd', value: model.aiCostUsd, labels: { ...modelLabels, evidence: 'observed_ai_credits', usage_source: usage.source } }
    );
  }

  for (const [phase, evidence] of Object.entries(usage.phases)) {
    const phaseLabels = { ...labels, phase };
    samples.push(
      { name: 'copilot_value_experiment_phase_seconds', value: evidence.activeSeconds, labels: { ...phaseLabels, time_type: 'active_span', evidence: 'observed' } },
      { name: 'copilot_value_experiment_phase_seconds', value: evidence.allocatedSeconds, labels: { ...phaseLabels, time_type: 'allocated_engaged', evidence: 'derived' } },
      { name: 'copilot_value_experiment_phase_tool_runtime_seconds', value: evidence.toolActiveSeconds, labels: { ...phaseLabels, evidence: 'observed' } },
      { name: 'copilot_value_experiment_phase_tool_calls', value: evidence.toolCalls, labels: { ...phaseLabels, evidence: 'observed' } },
      { name: 'copilot_value_experiment_phase_model_spans', value: evidence.modelSpans, labels: { ...phaseLabels, evidence: 'observed' } },
      { name: 'copilot_value_experiment_phase_token_count', value: evidence.uncachedInputTokens, labels: { ...phaseLabels, token_type: 'uncached_input', evidence: 'observed' } },
      { name: 'copilot_value_experiment_phase_token_count', value: evidence.outputTokens, labels: { ...phaseLabels, token_type: 'output', evidence: 'observed' } },
      { name: 'copilot_value_experiment_phase_token_count', value: evidence.reasoningTokens, labels: { ...phaseLabels, token_type: 'reasoning', evidence: 'observed' } }
    );
  }

  if (benchmark) {
    samples.push(
      { name: 'copilot_value_experiment_formula_version', value: benchmark.formulaVersion, labels: { ...labels, evidence: 'derived' } },
      { name: 'copilot_value_experiment_quality_ratio', value: benchmark.qualityFactor, labels: { ...labels, evidence: 'observed' } },
      { name: 'copilot_value_experiment_typing_equivalent_minutes', value: benchmark.typingEquivalentMinutes, labels: { ...labels, evidence: 'secondary_check' } }
    );
    for (const scenarioName of ['pessimistic', 'base', 'optimistic'] as const) {
      const scenario = benchmark.scenarios[scenarioName];
      const scenarioLabels = { ...labels, scenario: scenarioName, evidence: 'estimated' };
      samples.push(
        { name: 'copilot_value_experiment_task_time_reduction_ratio', value: scenario.taskTimeReduction, labels: { ...labels, scenario: scenarioName, evidence: 'derived_manual_model' } },
        { name: 'copilot_value_experiment_estimated_manual_minutes', value: scenario.estimatedManualMinutes, labels: scenarioLabels },
        { name: 'copilot_value_experiment_estimated_minutes_saved', value: scenario.estimatedMinutesSaved, labels: scenarioLabels },
        { name: 'copilot_value_experiment_estimated_manual_labor_cost_usd', value: scenario.estimatedManualLaborCostUsd, labels: scenarioLabels },
        { name: 'copilot_value_experiment_estimated_ai_assisted_labor_cost_usd', value: scenario.estimatedAiAssistedLaborCostUsd, labels: scenarioLabels },
        { name: 'copilot_value_experiment_estimated_ai_assisted_total_cost_usd', value: scenario.estimatedAiAssistedTotalCostUsd, labels: scenarioLabels },
        { name: 'copilot_value_experiment_estimated_gross_cost_savings_usd', value: scenario.estimatedGrossCostSavingsUsd, labels: scenarioLabels },
        { name: 'copilot_value_experiment_estimated_benefit_usd', value: scenario.estimatedBenefitUsd, labels: scenarioLabels },
        { name: 'copilot_value_experiment_estimated_net_value_usd', value: scenario.netValueUsd, labels: scenarioLabels },
        { name: 'copilot_value_experiment_break_even_manual_minutes', value: scenario.breakEvenManualMinutes, labels: scenarioLabels }
      );
      if (scenario.roi !== null) {
        samples.push({ name: 'copilot_value_experiment_estimated_roi_ratio', value: scenario.roi, labels: scenarioLabels });
      }
      if (scenario.modeledDeliveryCostReduction !== null) {
        samples.push({
          name: 'copilot_value_experiment_modeled_delivery_cost_reduction_ratio',
          value: scenario.modeledDeliveryCostReduction,
          labels: { ...labels, scenario: scenarioName, evidence: 'derived_manual_model' }
        });
      }
      for (const point of scenario.capacityBand) {
        const bandLabels = {
          ...labels,
          scenario: scenarioName,
          capacity_realization: point.capacityRealization.toFixed(2),
          evidence: 'estimated'
        };
        samples.push(
          { name: 'copilot_value_experiment_capacity_band_net_value_usd', value: point.netValueUsd, labels: bandLabels },
          { name: 'copilot_value_experiment_capacity_band_break_even_manual_minutes', value: point.breakEvenManualMinutes, labels: bandLabels }
        );
        if (point.roi !== null) {
          samples.push({ name: 'copilot_value_experiment_capacity_band_roi_ratio', value: point.roi, labels: bandLabels });
        }
      }
      for (const [phase, phaseValue] of Object.entries(scenario.phases)) {
        const phaseLabels = { ...labels, scenario: scenarioName, phase };
        samples.push(
          { name: 'copilot_value_experiment_phase_time_reduction_ratio', value: phaseValue.timeReduction, labels: { ...phaseLabels, evidence: 'derived_manual_model' } },
          { name: 'copilot_value_experiment_phase_estimated_manual_minutes', value: phaseValue.estimatedManualMinutes, labels: { ...phaseLabels, evidence: 'estimated' } },
          { name: 'copilot_value_experiment_phase_estimated_minutes_saved', value: phaseValue.estimatedMinutesSaved, labels: { ...phaseLabels, evidence: 'estimated' } }
        );
      }
    }
  }

  return `${samples.map((sample) => metricLine(sample, calculatedAt.getTime())).join('\n')}\n`;
}