import type { OtelSnapshot, Scenario, ValueConfig } from './schema.js';

export interface ScenarioValue {
  estimatedMinutesSaved: number;
  estimatedHoursSaved: number;
  estimatedBenefitUsd: number;
  netValueUsd: number;
  roi: number | null;
}

export interface ValueResult {
  measurementDay: string;
  observed: OtelSnapshot;
  costs: {
    seatAllocatedUsd: number;
    enablementAllocatedUsd: number;
    variableAllocatedUsd: number;
    cashCostUsd: number;
    breakEvenMinutes: number;
  };
  scenarios: Record<'pessimistic' | 'base' | 'optimistic', ScenarioValue> | null;
  metadata: {
    source: 'local_otel';
    assumptionsAcknowledged: boolean;
  };
}

function daysInMonth(day: string): number {
  const [year, month] = day.split('-').map(Number);
  if (year === undefined || month === undefined) {
    throw new Error(`Invalid report day '${day}'.`);
  }
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function scenarioValue(
  observed: OtelSnapshot,
  scenario: Scenario,
  loadedHourlyRateUsd: number,
  cashCostUsd: number
): ScenarioValue {
  const survivalFactor = observed.editSurvivalNoRevert ?? observed.editSurvivalFourGram ?? 1;
  const acceptedEditMinutes = observed.acceptedEditDecisions * scenario.minutesPerAcceptedEdit;
  const agentLocMinutes = observed.agentEditLoc * scenario.minutesPerAgentEditLoc;
  const nativeCodingMinutes = Math.max(acceptedEditMinutes, agentLocMinutes) * survivalFactor;
  const codingToolMinutes = observed.codingToolCalls * scenario.minutesPerCodingToolCall;
  const codingMinutes = Math.max(nativeCodingMinutes, codingToolMinutes);
  const estimatedMinutesSaved = codingMinutes +
    observed.researchToolCalls * scenario.minutesPerResearchToolCall +
    observed.planningToolCalls * scenario.minutesPerPlanningToolCall;
  const estimatedHoursSaved = estimatedMinutesSaved / 60;
  const estimatedBenefitUsd = estimatedHoursSaved * loadedHourlyRateUsd * scenario.capacityRealization;
  const netValueUsd = estimatedBenefitUsd - cashCostUsd;

  return {
    estimatedMinutesSaved,
    estimatedHoursSaved,
    estimatedBenefitUsd,
    netValueUsd,
    roi: cashCostUsd > 0 ? netValueUsd / cashCostUsd : null
  };
}

export function validateScenarioOrdering(config: ValueConfig): void {
  const keys: Array<keyof Scenario> = [
    'minutesPerAcceptedEdit',
    'minutesPerAgentEditLoc',
    'minutesPerCodingToolCall',
    'minutesPerResearchToolCall',
    'minutesPerPlanningToolCall',
    'capacityRealization'
  ];

  for (const key of keys) {
    const pessimistic = config.scenarios.pessimistic[key];
    const base = config.scenarios.base[key];
    const optimistic = config.scenarios.optimistic[key];
    if (pessimistic > base || base > optimistic) {
      throw new Error(`Scenario assumption '${key}' must satisfy pessimistic <= base <= optimistic.`);
    }
  }
}

export function calculateValue(
  observed: OtelSnapshot,
  config: ValueConfig
): ValueResult {
  validateScenarioOrdering(config);
  const monthDays = daysInMonth(observed.day);
  const seatAllocatedUsd = config.monthlySeatCostUsd / monthDays;
  const enablementAllocatedUsd = config.monthlyEnablementCostUsd / monthDays;
  const variableAllocatedUsd = config.monthlyVariableCostUsd / monthDays;
  const cashCostUsd = seatAllocatedUsd + enablementAllocatedUsd + variableAllocatedUsd;
  const breakEvenMinutes = 60 * cashCostUsd / config.loadedHourlyRateUsd;

  const scenarios = config.acknowledgedAssumptions ? {
    pessimistic: scenarioValue(observed, config.scenarios.pessimistic, config.loadedHourlyRateUsd, cashCostUsd),
    base: scenarioValue(observed, config.scenarios.base, config.loadedHourlyRateUsd, cashCostUsd),
    optimistic: scenarioValue(observed, config.scenarios.optimistic, config.loadedHourlyRateUsd, cashCostUsd)
  } : null;

  return {
    measurementDay: observed.day,
    observed,
    costs: {
      seatAllocatedUsd,
      enablementAllocatedUsd,
      variableAllocatedUsd,
      cashCostUsd,
      breakEvenMinutes
    },
    scenarios,
    metadata: {
      source: 'local_otel',
      assumptionsAcknowledged: config.acknowledgedAssumptions
    }
  };
}