import assert from 'node:assert/strict';
import test from 'node:test';
import { calculateMechanisticBenchmark, conservativeQualityFactor, validateMechanisticScenarioOrdering } from '../src/benchmark.js';

test('uses the lower available survival signal as a conservative quality factor', () => {
  assert.equal(conservativeQualityFactor(1, 0.9288782765855073), 0.9288782765855073);
  assert.equal(conservativeQualityFactor(null, 0.8), 0.8);
  assert.equal(conservativeQualityFactor(null, null), 1);
});

test('calculates manual phase time without double-discounting retained source', () => {
  const result = calculateMechanisticBenchmark({
    phases: {
      planning: { measuredAiSeconds: 120, uncachedInputTokens: 0, outputTokens: 600, reasoningTokens: 300, toolCalls: 2, toolActiveSeconds: 10 },
      research: { measuredAiSeconds: 180, uncachedInputTokens: 1200, outputTokens: 0, reasoningTokens: 0, toolCalls: 3, toolActiveSeconds: 20 },
      coding: { measuredAiSeconds: 600, uncachedInputTokens: 0, outputTokens: 3000, reasoningTokens: 500, toolCalls: 2, toolActiveSeconds: 1 },
      validation: { measuredAiSeconds: 300, uncachedInputTokens: 600, outputTokens: 600, reasoningTokens: 0, toolCalls: 4, toolActiveSeconds: 120 },
      unclassified: { measuredAiSeconds: 88.694, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 }
    },
    aiCostUsd: 11.58015725,
    qualityFactor: 0.9288782765855073,
    retainedSourceCharacters: 34_513
  }, {
    loadedHourlyRateUsd: 120,
    capacityRealization: 0.5,
    typingWordsPerMinute: 40,
    charactersPerWord: 5,
    scenarios: {
      pessimistic: {
        planning: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.1 },
        research: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.1 },
        coding: { manualEntryFraction: 0.25, wordsPerMinute: 60 },
        validation: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.1 },
        unclassifiedManualMultiplier: 1
      },
      base: {
        planning: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
        research: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
        coding: { manualEntryFraction: 0.5, wordsPerMinute: 40 },
        validation: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
        unclassifiedManualMultiplier: 1.25
      },
      optimistic: {
        planning: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5 },
        research: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5 },
        coding: { manualEntryFraction: 1, wordsPerMinute: 25 },
        validation: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5 },
        unclassifiedManualMultiplier: 1.5
      }
    }
  });

  assert.ok(Math.abs(result.measuredAiMinutes - 21.478233333333333) < 1e-12);
  assert.equal(result.formulaVersion, 2);
  assert.equal(result.phases.coding.measuredAiSeconds, 600);
  assert.equal(result.scenarios.base.phases.planning.estimatedManualMinutes, 0.875);
  assert.equal(result.scenarios.base.phases.research.estimatedManualMinutes, 1.5);
  assert.equal(result.scenarios.base.phases.coding.estimatedManualMinutes, 86.2825);
  assert.equal(result.scenarios.base.phases.validation.estimatedManualMinutes, 3.375);
  assert.equal(result.scenarios.base.phases.coding.estimatedMinutesSaved, 86.2825 - 10);
  assert.equal(
    result.scenarios.base.estimatedManualLaborCostUsd,
    result.scenarios.base.estimatedManualMinutes * 120 / 60
  );
  assert.equal(
    result.scenarios.base.estimatedAiAssistedLaborCostUsd,
    result.measuredAiMinutes * 120 / 60
  );
  assert.equal(
    result.scenarios.base.estimatedAiAssistedTotalCostUsd,
    result.scenarios.base.estimatedAiAssistedLaborCostUsd + result.aiCostUsd
  );
  assert.equal(
    result.scenarios.base.estimatedGrossCostSavingsUsd,
    result.scenarios.base.estimatedManualLaborCostUsd - result.scenarios.base.estimatedAiAssistedTotalCostUsd
  );
  assert.equal(result.qualityFactor, 0.9288782765855073);
  assert.ok((result.scenarios.pessimistic.roi ?? 0) < (result.scenarios.base.roi ?? 0));
  assert.ok((result.scenarios.base.roi ?? 0) < (result.scenarios.optimistic.roi ?? 0));
  assert.equal(result.typingEquivalentMinutes, 172.565);
});

test('reproduces the documented current-formula worked example', () => {
  const wallClockSeconds = 21.178;
  const activeSeconds = {
    coding: 2.961,
    validation: 9.205,
    unclassified: 2.165
  };
  const totalActiveSeconds = Object.values(activeSeconds).reduce((total, value) => total + value, 0);
  const allocatedSeconds = (phase: keyof typeof activeSeconds) =>
    wallClockSeconds * activeSeconds[phase] / totalActiveSeconds;
  const result = calculateMechanisticBenchmark({
    phases: {
      planning: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      research: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      coding: { measuredAiSeconds: allocatedSeconds('coding'), uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      validation: { measuredAiSeconds: allocatedSeconds('validation'), uncachedInputTokens: 0, outputTokens: 101, reasoningTokens: 22, toolCalls: 1, toolActiveSeconds: 6.484 },
      unclassified: { measuredAiSeconds: allocatedSeconds('unclassified'), uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 }
    },
    aiCostUsd: 0.157581,
    qualityFactor: 1,
    retainedSourceCharacters: 32
  }, {
    loadedHourlyRateUsd: 92,
    capacityRealization: 0.5,
    typingWordsPerMinute: 52,
    charactersPerWord: 5,
    scenarios: {
      pessimistic: {
        planning: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.12 },
        research: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.12 },
        coding: { manualEntryFraction: 0.25, wordsPerMinute: 60 },
        validation: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.12 },
        unclassifiedManualMultiplier: 1
      },
      base: {
        planning: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
        research: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
        coding: { manualEntryFraction: 0.5, wordsPerMinute: 40 },
        validation: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
        unclassifiedManualMultiplier: 1.25
      },
      optimistic: {
        planning: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5 },
        research: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5 },
        coding: { manualEntryFraction: 1, wordsPerMinute: 25 },
        validation: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5 },
        unclassifiedManualMultiplier: 1.5
      }
    }
  });
  const base = result.scenarios.base;

  assert.ok(Math.abs(result.measuredAiMinutes - 0.352967) < 0.000001);
  assert.ok(Math.abs(result.phases.coding.measuredAiMinutes - 0.072928) < 0.000001);
  assert.ok(Math.abs(result.phases.validation.measuredAiMinutes - 0.226715) < 0.000001);
  assert.ok(Math.abs(result.phases.unclassified.measuredAiMinutes - 0.053323) < 0.000001);
  assert.equal(base.phases.coding.estimatedManualMinutes, 0.08);
  assert.ok(Math.abs(base.phases.validation.estimatedManualMinutes - 0.421192) < 0.000001);
  assert.ok(Math.abs(base.phases.unclassified.estimatedManualMinutes - 0.066654) < 0.000001);
  assert.ok(Math.abs(base.estimatedManualMinutes - 0.567846) < 0.000001);
  assert.ok(Math.abs(base.estimatedMinutesSaved - 0.214879) < 0.000001);
  assert.ok(Math.abs(base.estimatedBenefitUsd - 0.164740) < 0.000001);
  assert.ok(Math.abs((base.roi ?? 0) - 0.0454) < 0.0001);
});

test('research duration and its prior change ROI independently of coding', () => {
  const input = {
    phases: {
      planning: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      research: { measuredAiSeconds: 300, uncachedInputTokens: 1200, outputTokens: 0, reasoningTokens: 0, toolCalls: 2, toolActiveSeconds: 10 },
      coding: { measuredAiSeconds: 300, uncachedInputTokens: 0, outputTokens: 100, reasoningTokens: 0, toolCalls: 1, toolActiveSeconds: 1 },
      validation: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      unclassified: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 }
    },
    aiCostUsd: 2,
    qualityFactor: 1,
    retainedSourceCharacters: 0
  };
  const config = {
    loadedHourlyRateUsd: 120,
    capacityRealization: 0.5,
    typingWordsPerMinute: 40,
    charactersPerWord: 5,
    scenarios: {
      pessimistic: {
        planning: { relevantTokenFraction: 0, tokensPerMinute: 600, interactionMinutesPerTool: 0 },
        research: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.1 },
        coding: { manualEntryFraction: 0.25, wordsPerMinute: 60 },
        validation: { relevantTokenFraction: 0, tokensPerMinute: 600, interactionMinutesPerTool: 0 },
        unclassifiedManualMultiplier: 1
      },
      base: {
        planning: { relevantTokenFraction: 0, tokensPerMinute: 400, interactionMinutesPerTool: 0 },
        research: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
        coding: { manualEntryFraction: 0.5, wordsPerMinute: 40 },
        validation: { relevantTokenFraction: 0, tokensPerMinute: 400, interactionMinutesPerTool: 0 },
        unclassifiedManualMultiplier: 1.25
      },
      optimistic: {
        planning: { relevantTokenFraction: 0, tokensPerMinute: 250, interactionMinutesPerTool: 0 },
        research: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5 },
        coding: { manualEntryFraction: 1, wordsPerMinute: 25 },
        validation: { relevantTokenFraction: 0, tokensPerMinute: 250, interactionMinutesPerTool: 0 },
        unclassifiedManualMultiplier: 1.5
      }
    }
  };
  const baseline = calculateMechanisticBenchmark(input, config);
  const researchHeavy = calculateMechanisticBenchmark(input, {
    ...config,
    scenarios: {
      ...config.scenarios,
      base: { ...config.scenarios.base, research: { ...config.scenarios.base.research, relevantTokenFraction: 0.6 } },
      optimistic: { ...config.scenarios.optimistic, research: { ...config.scenarios.optimistic.research, relevantTokenFraction: 0.8 } }
    }
  });
  assert.equal(baseline.scenarios.base.phases.coding.estimatedMinutesSaved,
    researchHeavy.scenarios.base.phases.coding.estimatedMinutesSaved);
  assert.ok((researchHeavy.scenarios.base.roi ?? 0) > (baseline.scenarios.base.roi ?? 0));
});

test('rejects invalid benchmark speedup assumptions', () => {
  assert.throws(() => calculateMechanisticBenchmark({
    phases: {
      planning: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      research: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      coding: { measuredAiSeconds: 60, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      validation: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      unclassified: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 }
    },
    aiCostUsd: 1,
    qualityFactor: 1,
    retainedSourceCharacters: 0
  }, {
    loadedHourlyRateUsd: 120,
    capacityRealization: 0.5,
    typingWordsPerMinute: 40,
    charactersPerWord: 5,
    scenarios: {
      pessimistic: { planning: { relevantTokenFraction: 0, tokensPerMinute: 600, interactionMinutesPerTool: 0 }, research: { relevantTokenFraction: 0, tokensPerMinute: 600, interactionMinutesPerTool: 0 }, coding: { manualEntryFraction: 0.25, wordsPerMinute: 60 }, validation: { relevantTokenFraction: 0, tokensPerMinute: 600, interactionMinutesPerTool: 0 }, unclassifiedManualMultiplier: 1 },
      base: { planning: { relevantTokenFraction: 0, tokensPerMinute: 400, interactionMinutesPerTool: 0 }, research: { relevantTokenFraction: 0, tokensPerMinute: 400, interactionMinutesPerTool: 0 }, coding: { manualEntryFraction: 1.2, wordsPerMinute: 40 }, validation: { relevantTokenFraction: 0, tokensPerMinute: 400, interactionMinutesPerTool: 0 }, unclassifiedManualMultiplier: 1.25 },
      optimistic: { planning: { relevantTokenFraction: 0, tokensPerMinute: 250, interactionMinutesPerTool: 0 }, research: { relevantTokenFraction: 0, tokensPerMinute: 250, interactionMinutesPerTool: 0 }, coding: { manualEntryFraction: 1, wordsPerMinute: 25 }, validation: { relevantTokenFraction: 0, tokensPerMinute: 250, interactionMinutesPerTool: 0 }, unclassifiedManualMultiplier: 1.5 }
    }
  }), /manual entry fraction/);
});

test('rejects scenario calibrations that invert their sensitivity order', () => {
  const calibration = {
    planning: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
    research: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
    coding: { manualEntryFraction: 0.5, wordsPerMinute: 40 },
    validation: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
    unclassifiedManualMultiplier: 1.25
  };
  const scenarios = {
    pessimistic: structuredClone(calibration),
    base: structuredClone(calibration),
    optimistic: structuredClone(calibration)
  };
  scenarios.base.research.tokensPerMinute = 700;

  assert.throws(
    () => validateMechanisticScenarioOrdering(scenarios),
    /research\.tokensPerMinute.*pessimistic >= base >= optimistic/
  );
});

test('weights reasoning tokens only where a scenario opts in', () => {
  const assumption = (reasoningTokenWeight: number) => ({
    relevantTokenFraction: 0.25,
    tokensPerMinute: 400,
    interactionMinutesPerTool: 0,
    reasoningTokenWeight
  });
  const result = calculateMechanisticBenchmark({
    phases: {
      planning: { measuredAiSeconds: 60, uncachedInputTokens: 0, outputTokens: 400, reasoningTokens: 800, toolCalls: 0, toolActiveSeconds: 0 },
      research: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      coding: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      validation: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      unclassified: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 }
    },
    aiCostUsd: 1,
    qualityFactor: 1,
    retainedSourceCharacters: 0
  }, {
    loadedHourlyRateUsd: 92,
    capacityRealization: 0.5,
    typingWordsPerMinute: 52,
    charactersPerWord: 5,
    scenarios: {
      pessimistic: { planning: assumption(0), research: assumption(0), coding: { manualEntryFraction: 0.25, wordsPerMinute: 60 }, validation: assumption(0), unclassifiedManualMultiplier: 1 },
      base: { planning: assumption(0), research: assumption(0), coding: { manualEntryFraction: 0.5, wordsPerMinute: 40 }, validation: assumption(0), unclassifiedManualMultiplier: 1.25 },
      optimistic: { planning: assumption(0.25), research: assumption(0.25), coding: { manualEntryFraction: 1, wordsPerMinute: 25 }, validation: assumption(0.25), unclassifiedManualMultiplier: 1.5 }
    }
  });

  assert.equal(result.scenarios.base.phases.planning.estimatedManualMinutes, 0.25);
  assert.equal(result.scenarios.optimistic.phases.planning.estimatedManualMinutes, 0.375);
});

test('reports a bounded delivery-cost reduction and an ordered capacity sensitivity band', () => {
  const assumption = {
    relevantTokenFraction: 0.25,
    tokensPerMinute: 400,
    interactionMinutesPerTool: 0.25,
    reasoningTokenWeight: 0
  };
  const result = calculateMechanisticBenchmark({
    phases: {
      planning: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      research: { measuredAiSeconds: 120, uncachedInputTokens: 40_000, outputTokens: 0, reasoningTokens: 0, toolCalls: 4, toolActiveSeconds: 10 },
      coding: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      validation: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 },
      unclassified: { measuredAiSeconds: 0, uncachedInputTokens: 0, outputTokens: 0, reasoningTokens: 0, toolCalls: 0, toolActiveSeconds: 0 }
    },
    aiCostUsd: 0.5,
    qualityFactor: 1,
    retainedSourceCharacters: 0
  }, {
    loadedHourlyRateUsd: 92,
    capacityRealization: 0.5,
    capacityRealizationBand: [0.25, 0.5, 0.75],
    typingWordsPerMinute: 52,
    charactersPerWord: 5,
    scenarios: {
      pessimistic: { planning: assumption, research: assumption, coding: { manualEntryFraction: 0.25, wordsPerMinute: 60 }, validation: assumption, unclassifiedManualMultiplier: 1 },
      base: { planning: assumption, research: assumption, coding: { manualEntryFraction: 0.5, wordsPerMinute: 40 }, validation: assumption, unclassifiedManualMultiplier: 1.25 },
      optimistic: { planning: assumption, research: assumption, coding: { manualEntryFraction: 1, wordsPerMinute: 25 }, validation: assumption, unclassifiedManualMultiplier: 1.5 }
    }
  });
  const base = result.scenarios.base;

  assert.ok((base.modeledDeliveryCostReduction ?? 0) > 0);
  assert.ok((base.modeledDeliveryCostReduction ?? 0) <= 1);
  assert.equal(
    base.modeledDeliveryCostReduction,
    base.estimatedGrossCostSavingsUsd / base.estimatedManualLaborCostUsd
  );
  assert.deepEqual(base.capacityBand.map((point) => point.capacityRealization), [0.25, 0.5, 0.75]);
  assert.equal(base.capacityBand[1]?.netValueUsd, base.netValueUsd);
  assert.equal(base.capacityBand[1]?.breakEvenManualMinutes, base.breakEvenManualMinutes);
  assert.ok((base.capacityBand[0]?.roi ?? 0) < (base.capacityBand[2]?.roi ?? 0));
});