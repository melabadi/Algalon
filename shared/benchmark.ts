export type BenchmarkScenarioName = 'pessimistic' | 'base' | 'optimistic';
export type BenchmarkPhase = 'planning' | 'research' | 'coding' | 'validation' | 'unclassified';

export const benchmarkPhases: BenchmarkPhase[] = [
  'planning', 'research', 'coding', 'validation', 'unclassified'
];

/** Bumped whenever the ROI arithmetic changes; results from different versions must never be aggregated. */
export const benchmarkFormulaVersion = 2;

export const defaultCapacityRealizationBand = [0.25, 0.5, 0.75];

export interface BenchmarkPhaseMeasurement {
  measuredAiSeconds: number;
  measuredAiMinutes: number;
}

export interface BenchmarkPhaseScenarioValue extends BenchmarkPhaseMeasurement {
  timeReduction: number;
  estimatedManualMinutes: number;
  estimatedMinutesSaved: number;
}

export interface BenchmarkCapacityPoint {
  capacityRealization: number;
  estimatedBenefitUsd: number;
  netValueUsd: number;
  roi: number | null;
  breakEvenManualMinutes: number;
}

export interface BenchmarkScenarioValue {
  taskTimeReduction: number;
  estimatedManualMinutes: number;
  estimatedMinutesSaved: number;
  estimatedManualLaborCostUsd: number;
  estimatedAiAssistedLaborCostUsd: number;
  estimatedAiAssistedTotalCostUsd: number;
  estimatedGrossCostSavingsUsd: number;
  modeledDeliveryCostReduction: number | null;
  estimatedBenefitUsd: number;
  netValueUsd: number;
  roi: number | null;
  breakEvenManualMinutes: number;
  capacityBand: BenchmarkCapacityPoint[];
  phases: Record<BenchmarkPhase, BenchmarkPhaseScenarioValue>;
}

export interface BenchmarkResult {
  formulaVersion: number;
  measuredAiMinutes: number;
  aiCostUsd: number;
  qualityFactor: number;
  retainedSourceCharacters: number;
  typingEquivalentMinutes: number;
  phases: Record<BenchmarkPhase, BenchmarkPhaseMeasurement>;
  scenarios: Record<BenchmarkScenarioName, BenchmarkScenarioValue>;
}

export interface TokenManualAssumption {
  relevantTokenFraction: number;
  tokensPerMinute: number;
  interactionMinutesPerTool: number;
  /** Share of reasoning tokens treated as reviewable content; absent means none. */
  reasoningTokenWeight?: number;
}

export interface CodingManualAssumption {
  manualEntryFraction: number;
  wordsPerMinute: number;
}

export interface MechanisticScenarioConfig {
  planning: TokenManualAssumption;
  research: TokenManualAssumption;
  coding: CodingManualAssumption;
  validation: TokenManualAssumption;
  unclassifiedManualMultiplier: number;
}

export interface MechanisticBenchmarkConfig {
  loadedHourlyRateUsd: number;
  capacityRealization: number;
  capacityRealizationBand?: number[];
  typingWordsPerMinute: number;
  charactersPerWord: number;
  scenarios: Record<BenchmarkScenarioName, MechanisticScenarioConfig>;
}

export interface MechanisticPhaseInput {
  measuredAiSeconds: number;
  uncachedInputTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  toolCalls: number;
  toolActiveSeconds: number;
}

export interface MechanisticBenchmarkInput {
  phases: Record<BenchmarkPhase, MechanisticPhaseInput>;
  aiCostUsd: number;
  qualityFactor: number;
  retainedSourceCharacters: number;
}

export function conservativeQualityFactor(...factors: Array<number | null>): number {
  const observed = factors.filter((factor): factor is number => factor !== null);
  return observed.length > 0 ? Math.min(...observed) : 1;
}

function validateTokenAssumption(name: string, assumption: TokenManualAssumption): void {
  if (assumption.relevantTokenFraction < 0 || assumption.relevantTokenFraction > 1) {
    throw new Error(`Benchmark '${name}' relevant token fraction must be between 0 and 1.`);
  }
  if (assumption.tokensPerMinute <= 0 || assumption.interactionMinutesPerTool < 0) {
    throw new Error(`Benchmark '${name}' token rate must be positive and interaction time nonnegative.`);
  }
  const reasoningWeight = assumption.reasoningTokenWeight ?? 0;
  if (reasoningWeight < 0 || reasoningWeight > 1) {
    throw new Error(`Benchmark '${name}' reasoning token weight must be between 0 and 1.`);
  }
}

function tokenManualMinutes(
  reviewableTokens: number,
  reasoningTokens: number,
  toolCalls: number,
  assumption: TokenManualAssumption
): number {
  const weightedTokens = reviewableTokens + reasoningTokens * (assumption.reasoningTokenWeight ?? 0);
  return weightedTokens * assumption.relevantTokenFraction / assumption.tokensPerMinute +
    toolCalls * assumption.interactionMinutesPerTool;
}

function assertScenarioOrder(
  name: string,
  pessimistic: number,
  base: number,
  optimistic: number,
  direction: 'ascending' | 'descending'
): void {
  const ordered = direction === 'ascending'
    ? pessimistic <= base && base <= optimistic
    : pessimistic >= base && base >= optimistic;
  if (!ordered) {
    const operator = direction === 'ascending' ? '<=' : '>=';
    throw new Error(`Benchmark scenario assumption '${name}' must satisfy pessimistic ${operator} base ${operator} optimistic.`);
  }
}

export function validateMechanisticScenarioOrdering(
  scenarios: Record<BenchmarkScenarioName, MechanisticScenarioConfig>
): void {
  const tokenPhases: Array<'planning' | 'research' | 'validation'> = [
    'planning', 'research', 'validation'
  ];
  for (const phase of tokenPhases) {
    assertScenarioOrder(
      `${phase}.relevantTokenFraction`,
      scenarios.pessimistic[phase].relevantTokenFraction,
      scenarios.base[phase].relevantTokenFraction,
      scenarios.optimistic[phase].relevantTokenFraction,
      'ascending'
    );
    assertScenarioOrder(
      `${phase}.tokensPerMinute`,
      scenarios.pessimistic[phase].tokensPerMinute,
      scenarios.base[phase].tokensPerMinute,
      scenarios.optimistic[phase].tokensPerMinute,
      'descending'
    );
    assertScenarioOrder(
      `${phase}.interactionMinutesPerTool`,
      scenarios.pessimistic[phase].interactionMinutesPerTool,
      scenarios.base[phase].interactionMinutesPerTool,
      scenarios.optimistic[phase].interactionMinutesPerTool,
      'ascending'
    );
    assertScenarioOrder(
      `${phase}.reasoningTokenWeight`,
      scenarios.pessimistic[phase].reasoningTokenWeight ?? 0,
      scenarios.base[phase].reasoningTokenWeight ?? 0,
      scenarios.optimistic[phase].reasoningTokenWeight ?? 0,
      'ascending'
    );
  }
  assertScenarioOrder(
    'coding.manualEntryFraction',
    scenarios.pessimistic.coding.manualEntryFraction,
    scenarios.base.coding.manualEntryFraction,
    scenarios.optimistic.coding.manualEntryFraction,
    'ascending'
  );
  assertScenarioOrder(
    'coding.wordsPerMinute',
    scenarios.pessimistic.coding.wordsPerMinute,
    scenarios.base.coding.wordsPerMinute,
    scenarios.optimistic.coding.wordsPerMinute,
    'descending'
  );
  assertScenarioOrder(
    'unclassifiedManualMultiplier',
    scenarios.pessimistic.unclassifiedManualMultiplier,
    scenarios.base.unclassifiedManualMultiplier,
    scenarios.optimistic.unclassifiedManualMultiplier,
    'ascending'
  );
}

export function calculateMechanisticBenchmark(
  input: MechanisticBenchmarkInput,
  config: MechanisticBenchmarkConfig
): BenchmarkResult {
  const durationSeconds = benchmarkPhases.reduce(
    (total, phase) => total + input.phases[phase].measuredAiSeconds,
    0
  );
  if (durationSeconds <= 0 || input.aiCostUsd < 0 || input.retainedSourceCharacters < 0 ||
    benchmarkPhases.some((phase) => Object.values(input.phases[phase]).some((value) => value < 0))) {
    throw new Error('Benchmark duration must be positive and evidence values must be nonnegative.');
  }
  if (input.qualityFactor < 0 || input.qualityFactor > 1) {
    throw new Error('Benchmark quality factor must be between 0 and 1.');
  }
  if (config.loadedHourlyRateUsd <= 0 || config.typingWordsPerMinute <= 0 || config.charactersPerWord <= 0) {
    throw new Error('Benchmark labor and typing assumptions must be positive.');
  }
  if (config.capacityRealization <= 0 || config.capacityRealization > 1) {
    throw new Error('Benchmark capacity realization must be greater than 0 and at most 1.');
  }
  const capacityBandValues = [...new Set([
    ...(config.capacityRealizationBand ?? defaultCapacityRealizationBand),
    config.capacityRealization
  ])].sort((left, right) => left - right);
  if (capacityBandValues.some((value) => value <= 0 || value > 1)) {
    throw new Error('Benchmark capacity realization band values must be greater than 0 and at most 1.');
  }

  const scenarioNames: BenchmarkScenarioName[] = ['pessimistic', 'base', 'optimistic'];
  for (const name of scenarioNames) {
    const scenario = config.scenarios[name];
    validateTokenAssumption(`${name}.planning`, scenario.planning);
    validateTokenAssumption(`${name}.research`, scenario.research);
    validateTokenAssumption(`${name}.validation`, scenario.validation);
    if (scenario.coding.manualEntryFraction < 0 || scenario.coding.manualEntryFraction > 1) {
      throw new Error(`Benchmark '${name}' coding manual entry fraction must be between 0 and 1.`);
    }
    if (scenario.coding.wordsPerMinute <= 0 || scenario.unclassifiedManualMultiplier <= 0) {
      throw new Error(`Benchmark '${name}' coding rate and unclassified multiplier must be positive.`);
    }
  }
  validateMechanisticScenarioOrdering(config.scenarios);

  const measuredAiMinutes = durationSeconds / 60;
  const phases = Object.fromEntries(benchmarkPhases.map((phase) => [phase, {
    measuredAiSeconds: input.phases[phase].measuredAiSeconds,
    measuredAiMinutes: input.phases[phase].measuredAiSeconds / 60
  }])) as Record<BenchmarkPhase, BenchmarkPhaseMeasurement>;
  const laborCostPerMinute = config.loadedHourlyRateUsd / 60;
  const realizedValuePerAdjustedMinute = laborCostPerMinute * config.capacityRealization;
  const breakEvenAdjustedMinutes = input.aiCostUsd / realizedValuePerAdjustedMinute;

  const scenarioValues = Object.fromEntries(scenarioNames.map((name) => {
    const assumptions = config.scenarios[name];
    const manualMinutes: Record<BenchmarkPhase, number> = {
      planning: tokenManualMinutes(
        input.phases.planning.outputTokens,
        input.phases.planning.reasoningTokens,
        input.phases.planning.toolCalls,
        assumptions.planning
      ),
      research: tokenManualMinutes(
        input.phases.research.uncachedInputTokens,
        0,
        input.phases.research.toolCalls,
        assumptions.research
      ),
      coding: input.retainedSourceCharacters * assumptions.coding.manualEntryFraction /
        config.charactersPerWord / assumptions.coding.wordsPerMinute,
      validation: input.phases.validation.toolActiveSeconds / 60 + tokenManualMinutes(
        input.phases.validation.outputTokens,
        input.phases.validation.reasoningTokens,
        input.phases.validation.toolCalls,
        assumptions.validation
      ),
      unclassified: phases.unclassified.measuredAiMinutes * assumptions.unclassifiedManualMultiplier
    };
    const phaseValues = Object.fromEntries(benchmarkPhases.map((phase) => {
      const measured = phases[phase];
      const estimatedManualMinutes = manualMinutes[phase];
      const estimatedMinutesSaved = estimatedManualMinutes - measured.measuredAiMinutes;
      const value: BenchmarkPhaseScenarioValue = {
        ...measured,
        timeReduction: estimatedManualMinutes !== 0
          ? estimatedMinutesSaved / estimatedManualMinutes
          : 0,
        estimatedManualMinutes,
        estimatedMinutesSaved
      };
      return [phase, value];
    })) as Record<BenchmarkPhase, BenchmarkPhaseScenarioValue>;
    const estimatedManualMinutes = benchmarkPhases.reduce(
      (total, phase) => total + phaseValues[phase].estimatedManualMinutes,
      0
    );
    const estimatedMinutesSaved = estimatedManualMinutes - measuredAiMinutes;
    const estimatedManualLaborCostUsd = estimatedManualMinutes * laborCostPerMinute;
    const estimatedAiAssistedLaborCostUsd = measuredAiMinutes * laborCostPerMinute;
    const estimatedAiAssistedTotalCostUsd = estimatedAiAssistedLaborCostUsd + input.aiCostUsd;
    const estimatedGrossCostSavingsUsd = estimatedManualLaborCostUsd - estimatedAiAssistedTotalCostUsd;
    const estimatedBenefitUsd = estimatedMinutesSaved * realizedValuePerAdjustedMinute;
    const netValueUsd = estimatedBenefitUsd - input.aiCostUsd;
    const capacityBand = capacityBandValues.map((capacityRealization): BenchmarkCapacityPoint => {
      const realizedPerMinute = laborCostPerMinute * capacityRealization;
      const benefitUsd = estimatedMinutesSaved * realizedPerMinute;
      const netUsd = benefitUsd - input.aiCostUsd;
      return {
        capacityRealization,
        estimatedBenefitUsd: benefitUsd,
        netValueUsd: netUsd,
        roi: input.aiCostUsd > 0 ? netUsd / input.aiCostUsd : null,
        breakEvenManualMinutes: measuredAiMinutes + input.aiCostUsd / realizedPerMinute
      };
    });
    const value: BenchmarkScenarioValue = {
      taskTimeReduction: estimatedManualMinutes !== 0
        ? estimatedMinutesSaved / estimatedManualMinutes
        : 0,
      estimatedManualMinutes,
      estimatedMinutesSaved,
      estimatedManualLaborCostUsd,
      estimatedAiAssistedLaborCostUsd,
      estimatedAiAssistedTotalCostUsd,
      estimatedGrossCostSavingsUsd,
      modeledDeliveryCostReduction: estimatedManualLaborCostUsd > 0
        ? estimatedGrossCostSavingsUsd / estimatedManualLaborCostUsd
        : null,
      estimatedBenefitUsd,
      netValueUsd,
      roi: input.aiCostUsd > 0 ? netValueUsd / input.aiCostUsd : null,
      breakEvenManualMinutes: measuredAiMinutes + breakEvenAdjustedMinutes,
      capacityBand,
      phases: phaseValues
    };
    return [name, value];
  })) as Record<BenchmarkScenarioName, BenchmarkScenarioValue>;

  return {
    formulaVersion: benchmarkFormulaVersion,
    measuredAiMinutes,
    aiCostUsd: input.aiCostUsd,
    qualityFactor: input.qualityFactor,
    retainedSourceCharacters: input.retainedSourceCharacters,
    typingEquivalentMinutes: input.retainedSourceCharacters / config.charactersPerWord / config.typingWordsPerMinute,
    phases,
    scenarios: scenarioValues
  };
}