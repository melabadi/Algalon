import { benchmarkPhases, calculateMechanisticBenchmark } from '../../../../shared/benchmark';
import { unionIntervalDuration } from '../../../../shared/intervals';
import valueModelExample from '../../../../config/value-model.example.json';
import { messages } from '../../i18n';
import { scenarioLabel } from '../../lib/format';
import type { BenchmarkScenario, CalibrationSource, ModelingStatus, Overview, Scenario, ScenarioResult, Session } from '../../types';

export const scenarios: Scenario[] = ['pessimistic', 'base', 'optimistic'];
export const scenarioSelections = [...scenarios, 'custom'] as const;
export type ScenarioSelection = typeof scenarioSelections[number];
export const phases = ['planning', 'research', 'coding', 'validation', 'unclassified'] as const;
export const tokenPhases = ['planning', 'research', 'validation'] as const;
export type TokenPhase = typeof tokenPhases[number];
export type TrackedPhase = typeof phases[number];

export const savedCalibrationStorageKey = 'algalon.savedCalibrations.v1';
export const activeCalibrationStorageKey = 'algalon.activeCalibration.v1';

export interface CalibrationDraft {
  loadedHourlyRateUsd: number;
  capacityRealization: number;
  capacityRealizationBand?: number[];
  typingWordsPerMinute: number;
  charactersPerWord: number;
  scenarios: Record<Scenario, BenchmarkScenario>;
}

export interface SavedCalibration {
  id: string;
  name: string;
  updatedAt: string;
  customScenario: Scenario;
  draft: CalibrationDraft;
}

const starterCalibrationSources = valueModelExample.benchmark.calibrationSources as unknown as CalibrationSource[];

export function activityLabel(value: TrackedPhase): string {
  return scenarioLabel(value);
}

export function calibrationSourcesWithBundledEvidence(
  configured: CalibrationSource[] | undefined
): CalibrationSource[] {
  const merged = new Map(starterCalibrationSources.map((source) => [source.url, source]));
  for (const source of configured ?? []) {
    const bundled = merged.get(source.url);
    merged.set(source.url, bundled ? {
      ...bundled,
      ...source,
      supportLevels: { ...bundled.supportLevels, ...source.supportLevels }
    } : source);
  }
  return [...merged.values()];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isTokenAssumption(value: unknown): boolean {
  if (!isRecord(value)) return false;
  return isFiniteNumber(value.relevantTokenFraction)
    && isFiniteNumber(value.tokensPerMinute)
    && isFiniteNumber(value.interactionMinutesPerTool)
    && (value.reasoningTokenWeight === undefined || isFiniteNumber(value.reasoningTokenWeight));
}

function isBenchmarkScenario(value: unknown): value is BenchmarkScenario {
  if (!isRecord(value) || !isRecord(value.coding)) return false;
  return isTokenAssumption(value.planning)
    && isTokenAssumption(value.research)
    && isTokenAssumption(value.validation)
    && isFiniteNumber(value.coding.manualEntryFraction)
    && isFiniteNumber(value.coding.wordsPerMinute)
    && isFiniteNumber(value.unclassifiedManualMultiplier);
}

function isCalibrationDraft(value: unknown): value is CalibrationDraft {
  if (!isRecord(value) || !isRecord(value.scenarios)) return false;
  const scenarioValues = value.scenarios;
  return isFiniteNumber(value.loadedHourlyRateUsd)
    && isFiniteNumber(value.capacityRealization)
    && isFiniteNumber(value.typingWordsPerMinute)
    && isFiniteNumber(value.charactersPerWord)
    && (value.capacityRealizationBand === undefined
      || Array.isArray(value.capacityRealizationBand)
        && value.capacityRealizationBand.every(isFiniteNumber))
    && scenarios.every((name) => isBenchmarkScenario(scenarioValues[name]));
}

function absoluteTimestamp(value: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6] ?? 0);
  const offsetHour = Number(match[9] ?? 0);
  const offsetMinute = Number(match[10] ?? 0);
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (
    year < 1 || month < 1 || month > 12 || day < 1 || day > daysInMonth[month - 1]
    || hour > 23 || minute > 59 || second > 59 || offsetHour > 23 || offsetMinute > 59
  ) return null;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return null;
  const utcYear = new Date(timestamp).getUTCFullYear();
  return utcYear >= 1 && utcYear <= 9_999 ? timestamp : null;
}

function checkedAdd(left: number, right: number): number | null {
  const result = left + right;
  return Number.isFinite(result) ? result : null;
}

function checkedSubtract(left: number, right: number): number | null {
  const result = left - right;
  return Number.isFinite(result) ? result : null;
}

function checkedProduct(...values: number[]): number | null {
  let result = 1;
  for (const value of values) {
    result *= value;
    if (!Number.isFinite(result)) return null;
  }
  return result;
}

function checkedDivide(numerator: number, denominator: number): number | null {
  if (denominator === 0) return null;
  const result = numerator / denominator;
  return Number.isFinite(result) ? result : null;
}

function checkedSum(values: number[]): number | null {
  let total = 0;
  for (const value of values) {
    const next = checkedAdd(total, value);
    if (next === null) return null;
    total = next;
  }
  return total;
}

function unionDurationSeconds(sessions: Session[]): number {
  const intervals = sessions
    .map(sessionInterval)
    .filter((interval): interval is [number, number] => interval !== null);
  return unionIntervalDuration(intervals) / 1_000;
}

function sessionInterval(session: Session): [number, number] | null {
  const started = absoluteTimestamp(session.startedAt);
  const completed = absoluteTimestamp(session.completedAt);
  return started !== null && completed !== null && completed > started
    ? [started, completed]
    : null;
}

function engagedSeconds(session: Session): number | null {
  const engaged = session.usage.engagedSeconds;
  return engaged !== undefined && Number.isFinite(engaged) && engaged >= 0 ? engaged : null;
}

function nearlyEqual(left: number, right: number): boolean {
  return Math.abs(left - right) <= Math.max(1e-9, Math.abs(right) * 1e-9);
}

function isNonnegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function isNonnegativeNumber(value: unknown): value is number {
  return isFiniteNumber(value) && value >= 0;
}

export function completeWorkerUsage(session: Session): boolean {
  const usage = session.usage;
  const integerFields = [
    'chatSpans', 'inputTokens', 'cacheReadTokens', 'uncachedInputTokens',
    'outputTokens', 'reasoningTokens'
  ] as const;
  if (
    usage.source !== 'otel_traces' && usage.source !== 'copilot_turn_log'
    || integerFields.some((field) => !isNonnegativeInteger(usage[field]))
    || !isNonnegativeNumber(usage.aiCredits)
    || !isNonnegativeNumber(usage.aiCostUsd)
    || !isNonnegativeNumber(usage.elapsedSeconds)
    || !isNonnegativeNumber(usage.engagedSeconds)
    || !isNonnegativeNumber(usage.activeSeconds)
    || !isNonnegativeNumber(usage.activityDensity)
    || !Array.isArray(usage.models) || usage.models.length > 10_000
    || !isRecord(usage.phases) || Object.keys(usage.phases).length !== benchmarkPhases.length
    || !benchmarkPhases.every((phase) => Object.hasOwn(usage.phases!, phase))
  ) return false;
  if (
    session.chatSpans !== usage.chatSpans
    || session.tokens.input !== usage.inputTokens
    || session.tokens.cacheRead !== usage.cacheReadTokens
    || session.tokens.uncachedInput !== usage.uncachedInputTokens
    || session.tokens.output !== usage.outputTokens
    || session.tokens.reasoning !== usage.reasoningTokens
    || session.aiCredits !== usage.aiCredits
    || session.aiCostUsd !== usage.aiCostUsd
  ) return false;

  const modelNames = new Set<string>();
  const modelTotals = {
    chatSpans: 0,
    inputTokens: 0,
    cacheReadTokens: 0,
    uncachedInputTokens: 0,
    outputTokens: 0,
    reasoningTokens: 0,
    aiCredits: 0,
    aiCostUsd: 0
  };
  for (const model of usage.models) {
    if (
      !isRecord(model) || typeof model.model !== 'string' || !model.model
      || modelNames.has(model.model)
      || !isNonnegativeInteger(model.requests) || model.requests === 0
      || !isNonnegativeInteger(model.inputTokens)
      || !isNonnegativeInteger(model.cacheReadTokens)
      || !isNonnegativeInteger(model.uncachedInputTokens)
      || !isNonnegativeInteger(model.outputTokens)
      || !isNonnegativeInteger(model.reasoningTokens)
      || !isNonnegativeNumber(model.aiCredits)
      || !isNonnegativeNumber(model.aiCostUsd)
      || model.cacheReadTokens > model.inputTokens
      || model.uncachedInputTokens !== model.inputTokens - model.cacheReadTokens
      || model.aiCostUsd !== model.aiCredits / 100
    ) return false;
    modelNames.add(model.model);
    for (const [target, source] of [
      ['chatSpans', 'requests'],
      ['inputTokens', 'inputTokens'],
      ['cacheReadTokens', 'cacheReadTokens'],
      ['uncachedInputTokens', 'uncachedInputTokens'],
      ['outputTokens', 'outputTokens'],
      ['reasoningTokens', 'reasoningTokens'],
      ['aiCredits', 'aiCredits'],
      ['aiCostUsd', 'aiCostUsd']
    ] as const) {
      const total = checkedAdd(modelTotals[target], model[source]);
      if (total === null || target !== 'aiCredits' && target !== 'aiCostUsd' && !Number.isSafeInteger(total)) {
        return false;
      }
      modelTotals[target] = total;
    }
  }
  if (
    integerFields.some((field) => modelTotals[field] !== usage[field])
    || !nearlyEqual(modelTotals.aiCredits, usage.aiCredits)
    || !nearlyEqual(modelTotals.aiCostUsd, usage.aiCostUsd)
  ) return false;

  const phaseTotals = {
    activeSeconds: 0,
    allocatedSeconds: 0,
    toolCalls: 0,
    modelSpans: 0,
    uncachedInputTokens: 0,
    outputTokens: 0,
    reasoningTokens: 0
  };
  for (const phaseName of benchmarkPhases) {
    const phase = usage.phases[phaseName];
    if (
      !isRecord(phase)
      || !isNonnegativeNumber(phase.activeSeconds)
      || !isNonnegativeNumber(phase.allocatedSeconds)
      || !isNonnegativeNumber(phase.toolActiveSeconds)
      || !isNonnegativeInteger(phase.toolCalls)
      || !isNonnegativeInteger(phase.modelSpans)
      || !isNonnegativeInteger(phase.uncachedInputTokens)
      || !isNonnegativeInteger(phase.outputTokens)
      || !isNonnegativeInteger(phase.reasoningTokens)
      || phase.activeSeconds > usage.activeSeconds
      || phase.allocatedSeconds > usage.engagedSeconds
      || phase.toolActiveSeconds > usage.elapsedSeconds
      || phase.modelSpans === 0 && (
        phase.uncachedInputTokens !== 0 || phase.outputTokens !== 0 || phase.reasoningTokens !== 0
      )
      || phase.toolCalls === 0 && phase.toolActiveSeconds !== 0
      || phase.modelSpans === 0 && phase.toolCalls === 0
        && (phase.activeSeconds !== 0 || phase.allocatedSeconds !== 0)
    ) return false;
    for (const field of Object.keys(phaseTotals) as Array<keyof typeof phaseTotals>) {
      const total = checkedAdd(phaseTotals[field], phase[field]);
      if (total === null || field !== 'activeSeconds' && field !== 'allocatedSeconds'
        && !Number.isSafeInteger(total)) return false;
      phaseTotals[field] = total;
    }
    const expectedAllocation = usage.activeSeconds > 0
      ? usage.engagedSeconds * phase.activeSeconds / usage.activeSeconds : 0;
    if (!nearlyEqual(phase.allocatedSeconds, expectedAllocation)) return false;
  }

  const expectedDensity = usage.elapsedSeconds > 0
    ? usage.activeSeconds / usage.elapsedSeconds : 0;
  return usage.elapsedSeconds > 0
    && nearlyEqual(usage.elapsedSeconds, session.durationSeconds)
    && usage.activeSeconds <= usage.engagedSeconds
    && usage.engagedSeconds <= usage.elapsedSeconds
    && 0 <= usage.activityDensity && usage.activityDensity <= 1
    && nearlyEqual(usage.activityDensity, expectedDensity)
    && nearlyEqual(phaseTotals.activeSeconds, usage.activeSeconds)
    && nearlyEqual(phaseTotals.allocatedSeconds, usage.engagedSeconds)
    && usage.cacheReadTokens <= usage.inputTokens
    && usage.uncachedInputTokens === usage.inputTokens - usage.cacheReadTokens
    && usage.aiCostUsd === usage.aiCredits / 100
    && (usage.source !== 'copilot_turn_log' || usage.chatSpans > 0 && usage.aiCredits > 0)
    && (usage.chatSpans !== 0 || (
      usage.inputTokens === 0 && usage.cacheReadTokens === 0
      && usage.uncachedInputTokens === 0 && usage.outputTokens === 0
      && usage.reasoningTokens === 0 && usage.aiCredits === 0 && usage.aiCostUsd === 0
      && phaseTotals.modelSpans === 0 && phaseTotals.uncachedInputTokens === 0
      && phaseTotals.outputTokens === 0 && phaseTotals.reasoningTokens === 0
    ))
    && (usage.source !== 'otel_traces' || (
      phaseTotals.modelSpans <= usage.chatSpans
      && phaseTotals.uncachedInputTokens <= usage.uncachedInputTokens
      && phaseTotals.outputTokens <= usage.outputTokens
      && phaseTotals.reasoningTokens <= usage.reasoningTokens
    ));
}

/** Engaged time drives the model, but it can never exceed the union of session windows. */
export function observedAssistedSeconds(sessions: Session[]): number | null {
  const positioned = sessions.filter((session) => sessionInterval(session) !== null);
  const engagedValues = positioned.map(engagedSeconds);
  if (engagedValues.some((value) => value === null)) return null;
  const engagedTotal = checkedSum(engagedValues as number[]);
  if (engagedTotal === null) return null;
  return Math.min(engagedTotal, unionDurationSeconds(positioned));
}

function containsOnlyFiniteOutput(value: unknown): boolean {
  if (value === null) return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(containsOnlyFiniteOutput);
  if (isRecord(value)) {
    return Object.values(value).every(containsOnlyFiniteOutput);
  }
  return false;
}

function phaseInputFromSession(
  session: Session
): Parameters<typeof calculateMechanisticBenchmark>[0]['phases'] | null {
  const usage = isRecord(session.usage) ? session.usage : null;
  const phaseUsage = usage && isRecord(usage.phases) ? usage.phases : null;
  if (!phaseUsage) return null;
  const result = {} as Parameters<typeof calculateMechanisticBenchmark>[0]['phases'];
  for (const phase of benchmarkPhases) {
    const evidence = phaseUsage[phase];
    if (!isRecord(evidence)) return null;
    const values = {
      measuredAiSeconds: evidence.allocatedSeconds,
      uncachedInputTokens: evidence.uncachedInputTokens,
      outputTokens: evidence.outputTokens,
      reasoningTokens: evidence.reasoningTokens,
      toolCalls: evidence.toolCalls,
      toolActiveSeconds: evidence.toolActiveSeconds
    };
    if (!Object.values(values).every(isFiniteNumber)) return null;
    result[phase] = values as typeof result[typeof phase];
  }
  return result;
}

type CalculatedBenchmark = ReturnType<typeof calculateMechanisticBenchmark>;

function calibratedBenchmark(
  session: Session,
  draft: CalibrationDraft
): { benchmark: CalculatedBenchmark | null; invalid: boolean } {
  if (
    !isCalibrationDraft(draft)
    || !isFiniteNumber(session.aiCostUsd) || session.aiCostUsd < 0
    || !completeWorkerUsage(session)
  ) {
    return { benchmark: null, invalid: true };
  }
  const phaseInput = phaseInputFromSession(session);
  if (!phaseInput) return { benchmark: null, invalid: true };
  const interval = sessionInterval(session);
  const engaged = engagedSeconds(session);
  const allocated = checkedSum(benchmarkPhases.map((phase) => phaseInput[phase].measuredAiSeconds));
  if (
    interval === null
    || engaged === null
    || allocated === null
    || !isFiniteNumber(session.durationSeconds)
    || session.durationSeconds < 0
    || !nearlyEqual(session.durationSeconds, (interval[1] - interval[0]) / 1_000)
    || engaged > session.durationSeconds
    || !nearlyEqual(allocated, engaged)
  ) return { benchmark: null, invalid: true };
  if (session.aiCostUsd === 0) return { benchmark: null, invalid: false };
  if (benchmarkPhases.every((phase) => phaseInput[phase].measuredAiSeconds === 0)) {
    return { benchmark: null, invalid: false };
  }
  if (session.benchmark !== null && session.benchmark !== undefined && !isRecord(session.benchmark)) {
    return { benchmark: null, invalid: true };
  }
  const qualityFactor = session.benchmark?.qualityFactor;
  if (qualityFactor !== undefined && !isFiniteNumber(qualityFactor)) {
    return { benchmark: null, invalid: true };
  }
  if (!isFiniteNumber(session.retainedSourceCharacters)) {
    return { benchmark: null, invalid: true };
  }
  const benchmarkInput = {
    phases: phaseInput,
    aiCostUsd: session.aiCostUsd,
    qualityFactor: qualityFactor ?? 1,
    retainedSourceCharacters: session.sourceEvidenceComplete ? session.retainedSourceCharacters : 0
  };
  const benchmarkConfig = {
    loadedHourlyRateUsd: draft.loadedHourlyRateUsd,
    capacityRealization: draft.capacityRealization,
    ...(draft.capacityRealizationBand === undefined
      ? {} : { capacityRealizationBand: draft.capacityRealizationBand }),
    typingWordsPerMinute: draft.typingWordsPerMinute,
    charactersPerWord: draft.charactersPerWord,
    scenarios: draft.scenarios
  };
  try {
    const benchmark = calculateMechanisticBenchmark(benchmarkInput, benchmarkConfig);
    return containsOnlyFiniteOutput(benchmark)
      ? { benchmark, invalid: false }
      : { benchmark: null, invalid: true };
  } catch {
    return { benchmark: null, invalid: true };
  }
}

function calibratedSessionResult(
  session: Session,
  draft: CalibrationDraft,
  scenario: Scenario
): { session: Session; status: ModelingStatus } {
  const { benchmark, invalid } = calibratedBenchmark(session, draft);
  const scenarioResults = benchmark?.scenarios as Record<Scenario, ScenarioResult> | undefined;
  const status: ModelingStatus = invalid ? 'invalid' : benchmark ? 'available' : 'unavailable';
  return {
    status,
    session: {
      ...session,
      scenario,
      modelingStatus: status,
      scenarioResult: scenarioResults?.[scenario] ?? null,
      benchmark: benchmark ? {
        ...(session.benchmark ?? {}),
        scenarios: scenarioResults,
        qualityFactor: benchmark.qualityFactor,
        typingEquivalentMinutes: benchmark.typingEquivalentMinutes
      } : null
    }
  };
}

export function calibratedSession(
  session: Session,
  draft: CalibrationDraft,
  scenario: Scenario
): Session {
  return calibratedSessionResult(session, draft, scenario).session;
}

export function calibratedOverview(
  overview: Overview,
  draft: CalibrationDraft,
  scenario: Scenario
): Overview {
  const calibrated = overview.sessions.map((session) => calibratedSessionResult(session, draft, scenario));
  const sessions = calibrated.map((result) => result.session);
  const benchmarkedSessions = sessions.filter((session) => session.scenarioResult !== null);
  const totals: Overview['totals'] = {
    ...overview.totals,
    taskTimeReduction: null,
    estimatedManualMinutes: 0,
    estimatedMinutesSaved: 0,
    estimatedManualLaborCostUsd: 0,
    estimatedAiAssistedLaborCostUsd: 0,
    estimatedAiAssistedTotalCostUsd: 0,
    estimatedGrossCostSavingsUsd: 0,
    modeledDeliveryCostReduction: null,
    estimatedBenefitUsd: 0,
    netValueUsd: 0,
    roi: null
  };
  const finish = (modelingStatus: ModelingStatus): Overview => ({
    ...overview, scenario, modelingStatus, totals, sessions
  });
  if (calibrated.some((result) => result.status === 'invalid')) {
    return finish('invalid');
  }
  const estimatedManualMinutes = checkedSum(benchmarkedSessions.map(
    (session) => session.scenarioResult!.estimatedManualMinutes
  ));
  const estimatedManualLaborCostUsd = checkedSum(benchmarkedSessions.map(
    (session) => session.scenarioResult!.estimatedManualLaborCostUsd
  ));
  const assistedSecondsValue = observedAssistedSeconds(benchmarkedSessions);
  const laborCostPerMinute = checkedDivide(draft.loadedHourlyRateUsd, 60);
  if (
    estimatedManualMinutes === null
    || estimatedManualLaborCostUsd === null
    || assistedSecondsValue === null
    || laborCostPerMinute === null
  ) return finish('invalid');

  const assistedMinutes = checkedDivide(assistedSecondsValue, 60);
  const estimatedMinutesSaved = assistedMinutes === null
    ? null : checkedSubtract(estimatedManualMinutes, assistedMinutes);
  const estimatedAiAssistedLaborCostUsd = assistedMinutes === null
    ? null : checkedProduct(assistedMinutes, laborCostPerMinute);
  const estimatedAiAssistedTotalCostUsd = estimatedAiAssistedLaborCostUsd === null
    ? null : checkedAdd(estimatedAiAssistedLaborCostUsd, totals.aiCostUsd);
  const estimatedGrossCostSavingsUsd = estimatedAiAssistedTotalCostUsd === null
    ? null : checkedSubtract(estimatedManualLaborCostUsd, estimatedAiAssistedTotalCostUsd);
  const estimatedBenefitUsd = estimatedMinutesSaved === null
    ? null : checkedProduct(estimatedMinutesSaved, laborCostPerMinute, draft.capacityRealization);
  const netValueUsd = estimatedBenefitUsd === null
    ? null : checkedSubtract(estimatedBenefitUsd, totals.aiCostUsd);
  if (
    assistedMinutes === null
    || estimatedMinutesSaved === null
    || estimatedAiAssistedLaborCostUsd === null
    || estimatedAiAssistedTotalCostUsd === null
    || estimatedGrossCostSavingsUsd === null
    || estimatedBenefitUsd === null
    || netValueUsd === null
  ) return finish('invalid');

  Object.assign(totals, {
    estimatedManualMinutes,
    estimatedMinutesSaved,
    estimatedManualLaborCostUsd,
    estimatedAiAssistedLaborCostUsd,
    estimatedAiAssistedTotalCostUsd,
    estimatedGrossCostSavingsUsd,
    estimatedBenefitUsd,
    netValueUsd
  });
  if (totals.aiCostUsd > 0) totals.roi = checkedDivide(totals.netValueUsd, totals.aiCostUsd);
  if (totals.estimatedManualLaborCostUsd > 0) {
    totals.modeledDeliveryCostReduction = checkedDivide(
      totals.estimatedGrossCostSavingsUsd, totals.estimatedManualLaborCostUsd
    );
  }
  if (totals.estimatedManualMinutes !== 0) {
    totals.taskTimeReduction = checkedDivide(
      totals.estimatedMinutesSaved, totals.estimatedManualMinutes
    );
  }
  return finish(benchmarkedSessions.length > 0 ? 'available' : 'unavailable');
}

export function calibrationError(draft: CalibrationDraft): string | null {
  if (!isCalibrationDraft(draft)) return messages.calibration.invalidGlobalRates;
  if (draft.loadedHourlyRateUsd <= 0 || draft.capacityRealization <= 0 || draft.capacityRealization > 1 ||
    draft.typingWordsPerMinute <= 0 || draft.charactersPerWord <= 0 ||
    (draft.capacityRealizationBand !== undefined && (
      draft.capacityRealizationBand.length === 0
      || draft.capacityRealizationBand.some((value) => value <= 0 || value > 1)
    ))) {
    return messages.calibration.invalidGlobalRates;
  }
  for (const scenario of scenarios) {
    const calibration = draft.scenarios[scenario];
    for (const phase of tokenPhases) {
      const assumption = calibration[phase];
      const reasoningTokenWeight = assumption.reasoningTokenWeight ?? 0;
      if (assumption.relevantTokenFraction < 0 || assumption.relevantTokenFraction > 1 ||
        assumption.tokensPerMinute <= 0 || assumption.interactionMinutesPerTool < 0 ||
        reasoningTokenWeight < 0 || reasoningTokenWeight > 1) {
        return messages.calibration.invalidPhase(scenarioLabel(scenario), phase);
      }
    }
    if (calibration.coding.manualEntryFraction < 0 || calibration.coding.manualEntryFraction > 1 ||
      calibration.coding.wordsPerMinute <= 0 || calibration.unclassifiedManualMultiplier <= 0) {
      return messages.calibration.invalidCoding(scenarioLabel(scenario));
    }
  }
  const { pessimistic, base, optimistic } = draft.scenarios;
  for (const phase of tokenPhases) {
    if (pessimistic[phase].relevantTokenFraction > base[phase].relevantTokenFraction ||
      base[phase].relevantTokenFraction > optimistic[phase].relevantTokenFraction ||
      pessimistic[phase].tokensPerMinute < base[phase].tokensPerMinute ||
      base[phase].tokensPerMinute < optimistic[phase].tokensPerMinute ||
      pessimistic[phase].interactionMinutesPerTool > base[phase].interactionMinutesPerTool ||
      base[phase].interactionMinutesPerTool > optimistic[phase].interactionMinutesPerTool ||
      (pessimistic[phase].reasoningTokenWeight ?? 0) > (base[phase].reasoningTokenWeight ?? 0) ||
      (base[phase].reasoningTokenWeight ?? 0) > (optimistic[phase].reasoningTokenWeight ?? 0)) {
      return messages.calibration.unorderedPhase(activityLabel(phase));
    }
  }
  if (pessimistic.coding.manualEntryFraction > base.coding.manualEntryFraction ||
    base.coding.manualEntryFraction > optimistic.coding.manualEntryFraction ||
    pessimistic.coding.wordsPerMinute < base.coding.wordsPerMinute ||
    base.coding.wordsPerMinute < optimistic.coding.wordsPerMinute ||
    pessimistic.unclassifiedManualMultiplier > base.unclassifiedManualMultiplier ||
    base.unclassifiedManualMultiplier > optimistic.unclassifiedManualMultiplier) {
    return messages.calibration.unorderedCoding;
  }
  return null;
}

export function loadSavedCalibrations(): SavedCalibration[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(savedCalibrationStorageKey) ?? '[]') as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((value): SavedCalibration[] => {
      if (!value || typeof value !== 'object') return [];
      const candidate = value as Partial<SavedCalibration>;
      if (typeof candidate.id !== 'string' || typeof candidate.name !== 'string' ||
        typeof candidate.updatedAt !== 'string' || !candidate.draft
        || !scenarios.includes(candidate.customScenario as Scenario)
        || !isCalibrationDraft(candidate.draft)) return [];
      try {
        if (calibrationError(candidate.draft) !== null) return [];
        return [{
          id: candidate.id,
          name: candidate.name,
          updatedAt: candidate.updatedAt,
          customScenario: candidate.customScenario as Scenario,
          draft: candidate.draft
        }];
      } catch {
        return [];
      }
    });
  } catch {
    return [];
  }
}
