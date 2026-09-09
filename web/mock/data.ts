import valueModelExample from '../../config/value-model.example.json';
import type {
  InsightMetric,
  Insights,
  Methodology,
  Overview,
  PhaseScenario,
  Prompt,
  Scenario,
  ScenarioResult,
  Session
} from '../src/types';

const hourlyRateUsd = 92;
const formulaVersion = 2;
const dayMilliseconds = 24 * 60 * 60 * 1_000;
const phases = ['planning', 'research', 'coding', 'validation', 'unclassified'] as const;
const scenarioFactors: Record<Scenario, { manual: number; capacity: number }> = {
  pessimistic: { manual: 0.72, capacity: 0.25 },
  base: { manual: 1, capacity: 0.5 },
  optimistic: { manual: 1.35, capacity: 0.75 }
};

function rounded(value: number, digits = 4): number {
  return Number(value.toFixed(digits));
}

function recentInterval(daysAgo: number, durationSeconds: number): { startedAt: string; completedAt: string } {
  const completed = new Date(Date.now() - daysAgo * dayMilliseconds);
  const started = new Date(completed.getTime() - durationSeconds * 1_000);
  return { startedAt: started.toISOString(), completedAt: completed.toISOString() };
}

function allocatedValues(total: number, weights: number[]): number[] {
  let allocated = 0;
  return weights.map((weight, index) => {
    if (index === weights.length - 1) return total - allocated;
    const value = Math.round(total * weight);
    allocated += value;
    return value;
  });
}

function scenarioResult(
  scenario: Scenario,
  engagedSeconds: number,
  aiCostUsd: number,
  baseManualMinutes: number,
  phaseSeconds: Record<string, number>
): ScenarioResult {
  const { manual: manualFactor, capacity } = scenarioFactors[scenario];
  const measuredAiMinutes = engagedSeconds / 60;
  const estimatedManualMinutes = baseManualMinutes * manualFactor;
  const estimatedMinutesSaved = estimatedManualMinutes - measuredAiMinutes;
  const estimatedManualLaborCostUsd = estimatedManualMinutes / 60 * hourlyRateUsd;
  const estimatedAiAssistedLaborCostUsd = measuredAiMinutes / 60 * hourlyRateUsd;
  const estimatedAiAssistedTotalCostUsd = estimatedAiAssistedLaborCostUsd + aiCostUsd;
  const estimatedGrossCostSavingsUsd = estimatedManualLaborCostUsd - estimatedAiAssistedTotalCostUsd;
  const estimatedBenefitUsd = estimatedMinutesSaved / 60 * hourlyRateUsd * capacity;
  const netValueUsd = estimatedBenefitUsd - aiCostUsd;
  const phaseEntries = phases.map((phase): [string, PhaseScenario] => {
    const measuredSeconds = phaseSeconds[phase] ?? 0;
    const share = engagedSeconds === 0 ? 0 : measuredSeconds / engagedSeconds;
    const manualMinutes = estimatedManualMinutes * share;
    const savedMinutes = manualMinutes - measuredSeconds / 60;
    return [phase, {
      measuredAiMinutes: rounded(measuredSeconds / 60),
      measuredAiSeconds: measuredSeconds,
      estimatedManualMinutes: rounded(manualMinutes),
      estimatedMinutesSaved: rounded(savedMinutes),
      timeReduction: manualMinutes === 0 ? 0 : rounded(savedMinutes / manualMinutes)
    }];
  });

  return {
    estimatedManualMinutes: rounded(estimatedManualMinutes),
    estimatedMinutesSaved: rounded(estimatedMinutesSaved),
    estimatedManualLaborCostUsd: rounded(estimatedManualLaborCostUsd),
    estimatedAiAssistedLaborCostUsd: rounded(estimatedAiAssistedLaborCostUsd),
    estimatedAiAssistedTotalCostUsd: rounded(estimatedAiAssistedTotalCostUsd),
    estimatedGrossCostSavingsUsd: rounded(estimatedGrossCostSavingsUsd),
    modeledDeliveryCostReduction: estimatedManualLaborCostUsd === 0
      ? null
      : rounded(estimatedGrossCostSavingsUsd / estimatedManualLaborCostUsd),
    estimatedBenefitUsd: rounded(estimatedBenefitUsd),
    netValueUsd: rounded(netValueUsd),
    roi: aiCostUsd === 0 ? null : rounded(netValueUsd / aiCostUsd),
    taskTimeReduction: estimatedManualMinutes === 0 ? null : rounded(estimatedMinutesSaved / estimatedManualMinutes),
    breakEvenManualMinutes: rounded(measuredAiMinutes + aiCostUsd * 60 / (hourlyRateUsd * capacity)),
    capacityBand: [0.25, 0.5, 0.75].map((capacityRealization) => {
      const estimatedBenefit = estimatedMinutesSaved / 60 * hourlyRateUsd * capacityRealization;
      const netValue = estimatedBenefit - aiCostUsd;
      return {
        capacityRealization,
        estimatedBenefitUsd: rounded(estimatedBenefit),
        netValueUsd: rounded(netValue),
        roi: aiCostUsd === 0 ? null : rounded(netValue / aiCostUsd),
        breakEvenManualMinutes: rounded(measuredAiMinutes + aiCostUsd * 60 / (hourlyRateUsd * capacityRealization))
      };
    }),
    phases: Object.fromEntries(phaseEntries)
  };
}

interface SessionSpec {
  experiment: string;
  daysAgo: number;
  durationSeconds: number;
  engagedSeconds: number;
  activeSeconds: number;
  aiCredits: number;
  chatSpans: number;
  inputTokens: number;
  cacheReadTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  promptCount: number;
  model: string;
  baseManualMinutes: number;
  sourceCharacters?: number;
  modeled?: boolean;
}

function createSession(spec: SessionSpec): Session {
  const interval = recentInterval(spec.daysAgo, spec.durationSeconds);
  const aiCostUsd = spec.aiCredits / 100;
  const phaseWeights = [0.12, 0.24, 0.34, 0.22, 0.08];
  const phaseActiveSeconds = allocatedValues(spec.activeSeconds, phaseWeights);
  const phaseSeconds = phaseActiveSeconds.map((activeSeconds) => spec.engagedSeconds * activeSeconds / spec.activeSeconds);
  const classifiedModelSpans = allocatedValues(spec.chatSpans - 1, [0.13, 0.26, 0.37, 0.24]);
  const phaseModelSpans = [...classifiedModelSpans, 1];
  const modelWeights = phaseModelSpans.map((modelSpans) => modelSpans / spec.chatSpans);
  const phaseInputTokens = allocatedValues(spec.inputTokens - spec.cacheReadTokens, modelWeights);
  const phaseOutputTokens = allocatedValues(spec.outputTokens, modelWeights);
  const phaseReasoningTokens = allocatedValues(spec.reasoningTokens, modelWeights);
  const phaseUsage = Object.fromEntries(phases.map((phase, index) => [phase, {
    allocatedSeconds: phaseSeconds[index],
    activeSeconds: phaseActiveSeconds[index],
    toolActiveSeconds: Math.min(phaseActiveSeconds[index], index === 4 ? 0 : 35 + index * 18),
    toolCalls: index === 4 ? 0 : index + 1,
    modelSpans: phaseModelSpans[index],
    uncachedInputTokens: phaseInputTokens[index],
    outputTokens: phaseOutputTokens[index],
    reasoningTokens: phaseReasoningTokens[index]
  }]));
  const phaseSecondsByName = Object.fromEntries(phases.map((phase, index) => [phase, phaseSeconds[index]]));
  const benchmarkScenarios = Object.fromEntries((['pessimistic', 'base', 'optimistic'] as Scenario[]).map((scenario) => [
    scenario,
    scenarioResult(scenario, spec.engagedSeconds, aiCostUsd, spec.baseManualMinutes, phaseSecondsByName)
  ])) as Record<Scenario, ScenarioResult>;
  const sourceEvidenceComplete = spec.sourceCharacters !== undefined;
  const modeled = spec.modeled !== false;

  return {
    experiment: spec.experiment,
    ...interval,
    status: 'published',
    durationSeconds: spec.durationSeconds,
    aiCostUsd,
    aiCredits: spec.aiCredits,
    chatSpans: spec.chatSpans,
    tokens: {
      input: spec.inputTokens,
      cacheRead: spec.cacheReadTokens,
      uncachedInput: spec.inputTokens - spec.cacheReadTokens,
      output: spec.outputTokens,
      reasoning: spec.reasoningTokens
    },
    retainedSourceCharacters: spec.sourceCharacters ?? 0,
    sourceEvidenceComplete,
    promptCount: spec.promptCount,
    latestMessageAt: new Date(Date.parse(interval.completedAt) - 25_000).toISOString(),
    scenario: 'base',
    scenarioResult: modeled ? benchmarkScenarios.base : null,
    formulaVersion,
    currentFormula: modeled,
    modelingStatus: modeled ? 'available' : 'unavailable',
    usage: {
      source: spec.experiment === 'checkout-flow-refactor' ? 'copilot_turn_log' : 'otel_traces',
      chatSpans: spec.chatSpans,
      inputTokens: spec.inputTokens,
      cacheReadTokens: spec.cacheReadTokens,
      uncachedInputTokens: spec.inputTokens - spec.cacheReadTokens,
      outputTokens: spec.outputTokens,
      reasoningTokens: spec.reasoningTokens,
      aiCredits: spec.aiCredits,
      aiCostUsd,
      models: [{
        model: spec.model,
        requests: spec.chatSpans,
        inputTokens: spec.inputTokens,
        cacheReadTokens: spec.cacheReadTokens,
        uncachedInputTokens: spec.inputTokens - spec.cacheReadTokens,
        outputTokens: spec.outputTokens,
        reasoningTokens: spec.reasoningTokens,
        aiCredits: spec.aiCredits,
        aiCostUsd
      }],
      phases: phaseUsage,
      elapsedSeconds: spec.durationSeconds,
      engagedSeconds: spec.engagedSeconds,
      activeSeconds: spec.activeSeconds,
      activityDensity: spec.activeSeconds / spec.durationSeconds
    },
    source: sourceEvidenceComplete ? {
      source: 'retained_source_delta',
      filesAdded: 1,
      filesModified: Math.max(1, Math.round((spec.sourceCharacters ?? 0) / 900)),
      charactersAdded: spec.sourceCharacters,
      charactersRemoved: Math.round((spec.sourceCharacters ?? 0) * 0.08),
      linesAdded: Math.round((spec.sourceCharacters ?? 0) / 55),
      linesRemoved: Math.round((spec.sourceCharacters ?? 0) / 220)
    } : { source: 'unavailable' },
    benchmark: modeled ? {
      formulaVersion,
      scenarios: benchmarkScenarios,
      qualityFactor: 0.94,
      typingEquivalentMinutes: sourceEvidenceComplete ? rounded((spec.sourceCharacters ?? 0) / 260) : 0
    } : null
  };
}

const sessions = [
  createSession({ experiment: 'checkout-flow-refactor', daysAgo: 0.2, durationSeconds: 2_280, engagedSeconds: 1_620, activeSeconds: 840, aiCredits: 142, chatSpans: 8, inputTokens: 72_400, cacheReadTokens: 51_200, outputTokens: 4_800, reasoningTokens: 1_350, promptCount: 3, model: 'gpt-5.1-codex', baseManualMinutes: 58, sourceCharacters: 4_860 }),
  createSession({ experiment: 'auth-regression-fix', daysAgo: 2, durationSeconds: 1_260, engagedSeconds: 1_080, activeSeconds: 610, aiCredits: 84, chatSpans: 5, inputTokens: 38_200, cacheReadTokens: 21_700, outputTokens: 2_250, reasoningTokens: 740, promptCount: 2, model: 'claude-sonnet-4.5', baseManualMinutes: 14, sourceCharacters: 620 }),
  createSession({ experiment: 'api-test-coverage', daysAgo: 8, durationSeconds: 3_180, engagedSeconds: 2_460, activeSeconds: 1_380, aiCredits: 211, chatSpans: 12, inputTokens: 118_000, cacheReadTokens: 86_000, outputTokens: 7_450, reasoningTokens: 2_900, promptCount: 3, model: 'gpt-5.1-codex', baseManualMinutes: 74, sourceCharacters: 7_920 }),
  createSession({ experiment: 'telemetry-only-research', daysAgo: 18, durationSeconds: 900, engagedSeconds: 660, activeSeconds: 330, aiCredits: 25, chatSpans: 2, inputTokens: 12_800, cacheReadTokens: 8_200, outputTokens: 780, reasoningTokens: 110, promptCount: 1, model: 'gpt-5-mini', baseManualMinutes: 0, modeled: false })
];

function prompt(
  session: Session,
  ordinal: number,
  content: string,
  values: { requests: number; tools: number; input: number; cache: number; output: number; reasoning: number; credits: number; source?: Prompt['usageSource'] }
): Prompt {
  const promptId = `${session.experiment}-prompt-${ordinal}`;
  const contentAvailable = content.length > 0;
  const startedAt = new Date(Date.parse(session.startedAt) + ordinal * 170_000).toISOString();
  return {
    promptId,
    experiment: session.experiment,
    ordinal,
    ordinalExact: String(ordinal),
    startedAt,
    content,
    contentAvailable,
    capturedContentLength: contentAvailable ? content.length + 2_400 : 1_180,
    capturedContentLengthExact: String(contentAvailable ? content.length + 2_400 : 1_180),
    modelRequests: values.requests,
    modelRequestsExact: String(values.requests),
    toolCalls: values.tools,
    toolCallsExact: String(values.tools),
    inputTokens: values.input,
    inputTokensExact: String(values.input),
    cacheReadTokens: values.cache,
    cacheReadTokensExact: String(values.cache),
    cacheReadRatio: values.input === 0 ? 0 : rounded(values.cache / values.input),
    outputTokens: values.output,
    outputTokensExact: String(values.output),
    reasoningTokens: values.reasoning,
    reasoningTokensExact: String(values.reasoning),
    aiCredits: values.credits,
    aiCostUsd: values.credits / 100,
    usageSource: values.source ?? 'copilot_turn_log',
    models: { [session.usage.models?.[0]?.model ?? 'unknown']: values.requests },
    modelsExact: { [session.usage.models?.[0]?.model ?? 'unknown']: String(values.requests) },
    roi: null,
    roiStatus: 'prompt_level_attribution_required'
  };
}

export const mockPrompts: Prompt[] = [
  prompt(sessions[0], 1, 'Trace the checkout orchestration and identify the smallest safe boundary for the refactor.', { requests: 3, tools: 7, input: 25_200, cache: 18_700, output: 1_620, reasoning: 480, credits: 49 }),
  prompt(sessions[0], 2, 'Implement the refactor with focused regression coverage for retries and partial failures.', { requests: 3, tools: 9, input: 31_800, cache: 24_100, output: 2_140, reasoning: 650, credits: 58 }),
  prompt(sessions[0], 3, '', { requests: 2, tools: 4, input: 15_400, cache: 8_400, output: 1_040, reasoning: 220, credits: 35 }),
  prompt(sessions[1], 1, 'Reproduce the authentication refresh regression and locate the state transition that drops the token.', { requests: 3, tools: 6, input: 23_900, cache: 13_800, output: 1_410, reasoning: 510, credits: 51, source: 'otel_trace' }),
  prompt(sessions[1], 2, 'Fix the refresh race and run the narrow authentication tests.', { requests: 2, tools: 5, input: 14_300, cache: 7_900, output: 840, reasoning: 230, credits: 33, source: 'otel_trace' }),
  prompt(sessions[2], 1, 'Map the untested API error branches and rank them by user impact.', { requests: 4, tools: 8, input: 39_500, cache: 30_200, output: 2_230, reasoning: 910, credits: 72 }),
  prompt(sessions[2], 2, 'Add coverage for validation failures, missing sessions, and CSV export headers.', { requests: 5, tools: 12, input: 51_200, cache: 38_100, output: 3_080, reasoning: 1_270, credits: 91 }),
  prompt(sessions[2], 3, 'Run the backend suite and repair only regressions caused by these tests.', { requests: 3, tools: 7, input: 27_300, cache: 17_700, output: 2_140, reasoning: 720, credits: 48 }),
  prompt(sessions[3], 1, 'Summarize the retained telemetry contract and its privacy boundary.', { requests: 2, tools: 2, input: 12_800, cache: 8_200, output: 780, reasoning: 110, credits: 25, source: 'otel_trace' })
];

function sessionForScenario(session: Session, scenario: Scenario): Session {
  const result = session.benchmark?.scenarios?.[scenario] ?? null;
  return {
    ...session,
    scenario,
    scenarioResult: result,
    modelingStatus: result ? 'available' : session.modelingStatus
  };
}

export function mockSession(experiment: string, scenario: Scenario): Session | undefined {
  const session = sessions.find((candidate) => candidate.experiment === experiment);
  return session ? sessionForScenario(session, scenario) : undefined;
}

export function mockSessionPrompts(experiment: string): Prompt[] {
  return mockPrompts.filter((candidate) => candidate.experiment === experiment);
}

export function mockPrompt(promptId: string): Prompt | undefined {
  return mockPrompts.find((candidate) => candidate.promptId === promptId);
}

export function mockOverview(scenario: Scenario, days: number): Overview {
  const cutoff = Date.now() - days * dayMilliseconds;
  const selectedSessions = sessions
    .filter((session) => Date.parse(session.startedAt) >= cutoff)
    .map((session) => sessionForScenario(session, scenario));
  const modeledResults = selectedSessions.flatMap((session) => session.scenarioResult ? [session.scenarioResult] : []);
  const sum = (select: (result: ScenarioResult) => number) => modeledResults.reduce((total, result) => total + select(result), 0);
  const estimatedManualMinutes = sum((result) => result.estimatedManualMinutes);
  const estimatedMinutesSaved = sum((result) => result.estimatedMinutesSaved);
  const estimatedManualLaborCostUsd = sum((result) => result.estimatedManualLaborCostUsd);
  const estimatedGrossCostSavingsUsd = sum((result) => result.estimatedGrossCostSavingsUsd);
  const aiCostUsd = selectedSessions.reduce((total, session) => total + session.aiCostUsd, 0);
  const netValueUsd = sum((result) => result.netValueUsd);

  return {
    scenario,
    days,
    modelingStatus: modeledResults.length > 0 ? 'available' : 'unavailable',
    totals: {
      sessions: selectedSessions.length,
      prompts: selectedSessions.reduce((total, session) => total + session.promptCount, 0),
      durationSeconds: selectedSessions.reduce((total, session) => total + session.durationSeconds, 0),
      aiCostUsd: rounded(aiCostUsd),
      taskTimeReduction: estimatedManualMinutes === 0 ? null : rounded(estimatedMinutesSaved / estimatedManualMinutes),
      estimatedManualMinutes: rounded(estimatedManualMinutes),
      estimatedMinutesSaved: rounded(estimatedMinutesSaved),
      estimatedManualLaborCostUsd: rounded(estimatedManualLaborCostUsd),
      estimatedAiAssistedLaborCostUsd: rounded(sum((result) => result.estimatedAiAssistedLaborCostUsd)),
      estimatedAiAssistedTotalCostUsd: rounded(sum((result) => result.estimatedAiAssistedTotalCostUsd)),
      estimatedGrossCostSavingsUsd: rounded(estimatedGrossCostSavingsUsd),
      modeledDeliveryCostReduction: estimatedManualLaborCostUsd === 0 ? null : rounded(estimatedGrossCostSavingsUsd / estimatedManualLaborCostUsd),
      estimatedBenefitUsd: rounded(sum((result) => result.estimatedBenefitUsd)),
      netValueUsd: rounded(netValueUsd),
      roi: aiCostUsd === 0 ? null : rounded(netValueUsd / aiCostUsd),
      formulaVersion,
      supersededFormulaSessions: 0
    },
    sessions: selectedSessions
  };
}

function insightMetric(overrides: Partial<InsightMetric> & Pick<InsightMetric, 'key' | 'label' | 'category' | 'unit' | 'current'>): InsightMetric {
  return {
    group: 'behavior',
    scope: 'session',
    evidence: 'derived',
    description: 'Synthetic development signal generated by the local mock API.',
    previous: null,
    trend: null,
    normalZone: null,
    zone: 'descriptive',
    status: 'descriptive',
    action: 'Use this synthetic signal to exercise the interface; validate decisions against live evidence.',
    severity: null,
    severityLabel: null,
    aggregateSize: 4,
    minimumBaseline: 5,
    relatedMetrics: [],
    signal: { level: 'not_rated', direction: null, attentionBoundary: null, dangerBoundary: null, minimumSamples: 0 },
    reference: { kind: 'local_measurement', support: 'direct', label: 'Mock development evidence', url: '', note: 'Synthetic fixture data for local UI development.' },
    ...overrides
  };
}

const insightMetrics: InsightMetric[] = [
  insightMetric({ key: 'session_usage_coverage', label: 'Sessions with complete usage', category: 'Evidence', group: 'evidence', scope: 'portfolio', unit: 'ratio', current: 1, description: 'Share of settled sessions carrying the complete usage contract.', signal: { level: 'none', direction: 'minimum', attentionBoundary: 1, dangerBoundary: 0.95, minimumSamples: 1 } }),
  insightMetric({ key: 'cache_read_ratio', label: 'Session cache reuse', category: 'Context', unit: 'ratio', current: 0.67, previous: 0.59, trend: 'improving', description: 'Share of input tokens served from cache.', zone: 'typical', status: 'normal', signal: { level: 'none', direction: 'minimum', attentionBoundary: 0.5, dangerBoundary: 0.2, minimumSamples: 5 }, reference: { kind: 'best_practice', support: 'direct', label: 'OpenAI prompt caching', url: 'https://developers.openai.com/api/docs/guides/prompt-caching', note: 'Defines cached-token reuse.' } }),
  insightMetric({ key: 'uncached_input_per_request', label: 'Uncached input per request', category: 'Context', scope: 'request', unit: 'tokens', current: 4_820, previous: 3_600, trend: 'worsening', description: 'Median uncached input supplied to each model request.', zone: 'elevated', status: 'watch', severity: 0.4, severityLabel: 'Elevated', signal: { level: 'high', direction: 'maximum', attentionBoundary: 4_000, dangerBoundary: 8_000, minimumSamples: 5 } }),
  insightMetric({ key: 'tool_calls_per_prompt', label: 'Tool calls per prompt', category: 'Execution', scope: 'prompt', unit: 'calls', current: 4.7, previous: 4.2, trend: 'up', description: 'Median tool calls grouped under each direct user turn.' }),
  insightMetric({ key: 'output_tokens', label: 'Output per request', category: 'Generation', scope: 'request', unit: 'tokens', current: 690, previous: 740, trend: 'down', description: 'Median output tokens per model request.', signal: { level: 'none', direction: 'maximum', attentionBoundary: 2_000, dangerBoundary: 8_000, minimumSamples: 5 } }),
  insightMetric({ key: 'ai_cost_usd', label: 'AI usage per request', category: 'Cost', scope: 'request', unit: 'usd', current: 0.14, previous: 0.13, trend: 'up', description: 'AI-credit dollar equivalent per model request.' })
];

export function mockInsights(days: number): Insights {
  return {
    days,
    zoneMethod: {
      typical: 'Within the product-owned development signal boundary.',
      elevated: 'Past the attention boundary but below the danger boundary.',
      high: 'Past the danger boundary.',
      descriptive: 'Reported without an arbitrary good or bad threshold.',
      insufficient: 'Too few eligible observations to assign a status.',
      low: 'Below the product-owned minimum signal boundary.',
      baselineRule: 'Current values are synthetic medians of the latest eligible mock sessions.',
      coverageRule: 'Coverage includes every synthetic settled session in the selected period.'
    },
    evidenceHealth: {
      sessionUsageCoverage: 1,
      completeSessions: 4,
      eligibleSessions: 4,
      directUsageSessions: 1,
      otelUsageSessions: 3,
      requiredCoverage: 1,
      integrityPassed: true,
      degraded: false,
      message: null
    },
    summary: { metrics: insightMetrics.length, guardrails: 2, actions: 0, watch: 1, insufficient: 0 },
    priorities: insightMetrics.filter((metric) => metric.status === 'watch'),
    metrics: insightMetrics
  };
}

export const mockMethodology: Methodology = {
  claimKey: 'modeled_ai_usage_roi',
  claim: 'Modeled AI Usage ROI',
  scenarios: ['pessimistic', 'base', 'optimistic'],
  formulaVersion,
  config: {
    loadedHourlyRateUsd: valueModelExample.loadedHourlyRateUsd,
    benchmark: valueModelExample.benchmark as unknown as NonNullable<Methodology['config']['benchmark']>
  },
  formulas: {
    engagedTime: 'W_engaged = measure(merge(active spans with gaps <= G))',
    phaseAllocation: 'T_AI,p = (W_engaged / 60) x (a_p / A_active)',
    phaseSavings: 'T_saved,p,s = T_manual,p,s - T_AI,p',
    totalSavings: 'T_saved,s = sum_p(T_saved,p,s)',
    portfolioAiTime: 'T_AI,portfolio = measure(union_i([t_start,i, t_end,i]))',
    portfolioSavings: 'T_saved,portfolio,s = sum_i(T_manual,i,s) - T_AI,portfolio',
    manualLaborCost: 'C_manual,s = (T_manual,s / 60) x H',
    aiAssistedTotalCost: 'C_assisted = (T_AI / 60) x H + C_AI',
    portfolioAssistedCost: 'C_assisted,portfolio = (T_AI,portfolio / 60) x H + sum_i(C_AI,i)',
    grossCostSavings: 'Delta_C_gross,s = C_manual,s - C_assisted',
    deliveryCostReduction: 'R_delivery,s = Delta_C_gross,s / C_manual,s',
    benefit: 'B_s = (T_saved,s / 60) x H x rho',
    breakEven: 'T_break_even = T_AI + (60 x C_AI) / (H x rho)',
    aiCost: 'C_AI = sum(copilot_usage_nano_aiu) / 1e11',
    roi: 'ROI_s = (B_s - C_AI) / C_AI'
  }
};

function csvCell(value: string | number | boolean | null): string {
  const text = value === null ? '' : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function mockCsv(): string {
  const rows: Array<Array<string | number | boolean | null>> = [[
    'record_type', 'session_id', 'prompt_id', 'started_at', 'status', 'ai_cost_usd', 'prompt_content'
  ]];
  for (const session of sessions) {
    rows.push(['session', session.experiment, '', session.startedAt, session.status, session.aiCostUsd, '']);
    for (const item of mockSessionPrompts(session.experiment)) {
      rows.push(['prompt', session.experiment, item.promptId, item.startedAt, '', item.aiCostUsd, item.contentAvailable ? item.content : '']);
    }
  }
  return `${rows.map((row) => row.map(csvCell).join(',')).join('\r\n')}\r\n`;
}