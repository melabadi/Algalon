export type Scenario = 'pessimistic' | 'base' | 'optimistic';
export type ModelingStatus = 'available' | 'unavailable' | 'invalid';

export interface IndexingProgress {
  state: 'current' | 'catching_up' | 'blocked';
  pendingSessions: number;
  blockedSessions: number;
  oldestPendingSeconds: number;
  lastSuccessfulAt: string | null;
  lastDiscoveryAt: string | null;
  reason: string | null;
}

export interface PhaseScenario {
  measuredAiMinutes: number;
  measuredAiSeconds: number;
  estimatedManualMinutes: number;
  estimatedMinutesSaved: number;
  timeReduction: number;
}

export interface CapacityPoint {
  capacityRealization: number;
  estimatedBenefitUsd: number;
  netValueUsd: number;
  roi: number | null;
  breakEvenManualMinutes: number;
}

export interface ScenarioResult {
  estimatedManualMinutes: number;
  estimatedMinutesSaved: number;
  estimatedManualLaborCostUsd: number;
  estimatedAiAssistedLaborCostUsd: number;
  estimatedAiAssistedTotalCostUsd: number;
  estimatedGrossCostSavingsUsd: number;
  modeledDeliveryCostReduction?: number | null;
  estimatedBenefitUsd: number;
  netValueUsd: number;
  roi: number | null;
  taskTimeReduction: number | null;
  breakEvenManualMinutes: number;
  capacityBand?: CapacityPoint[];
  phases: Record<string, PhaseScenario>;
}

export interface Session {
  experiment: string;
  startedAt: string;
  completedAt: string;
  status: string;
  durationSeconds: number;
  aiCostUsd: number;
  aiCredits: number;
  chatSpans: number;
  tokens: {
    input: number;
    cacheRead: number;
    uncachedInput: number;
    output: number;
    reasoning: number;
  };
  retainedSourceCharacters: number;
  sourceEvidenceComplete: boolean;
  promptCount: number;
  latestMessageAt: string | null;
  scenario: Scenario;
  scenarioResult: ScenarioResult | null;
  formulaVersion?: number | null;
  currentFormula?: boolean;
  modelingStatus: ModelingStatus;
  usage: {
    source?: 'otel_traces' | 'copilot_turn_log';
    chatSpans?: number;
    inputTokens?: number;
    cacheReadTokens?: number;
    uncachedInputTokens?: number;
    outputTokens?: number;
    reasoningTokens?: number;
    aiCredits?: number;
    aiCostUsd?: number;
    models?: Array<{ model: string; requests: number; inputTokens: number; cacheReadTokens: number; uncachedInputTokens: number; outputTokens: number; reasoningTokens: number; aiCredits: number; aiCostUsd: number }>;
    phases?: Record<string, { allocatedSeconds: number; activeSeconds: number; toolActiveSeconds: number; toolCalls: number; modelSpans: number; uncachedInputTokens: number; outputTokens: number; reasoningTokens: number }>;
    elapsedSeconds?: number;
    engagedSeconds?: number;
    activeSeconds?: number;
    activityDensity?: number;
  };
  source: Record<string, unknown>;
  benchmark: { formulaVersion?: number; scenarios?: Record<Scenario, ScenarioResult>; qualityFactor?: number; typingEquivalentMinutes?: number } | null;
}

export interface Overview {
  scenario: Scenario;
  days: number;
  modelingStatus: ModelingStatus;
  totals: {
    sessions: number;
    prompts: number;
    durationSeconds: number;
    aiCostUsd: number;
    taskTimeReduction: number | null;
    estimatedManualMinutes: number;
    estimatedMinutesSaved: number;
    estimatedManualLaborCostUsd: number;
    estimatedAiAssistedLaborCostUsd: number;
    estimatedAiAssistedTotalCostUsd: number;
    estimatedGrossCostSavingsUsd: number;
    modeledDeliveryCostReduction?: number | null;
    estimatedBenefitUsd: number;
    netValueUsd: number;
    roi: number | null;
    formulaVersion?: number;
    supersededFormulaSessions?: number;
  };
  sessions: Session[];
}

export interface Prompt {
  promptId: string;
  experiment: string;
  ordinal: number | null;
  ordinalExact: string;
  startedAt: string;
  content: string;
  contentAvailable: boolean;
  capturedContentLength: number | null;
  capturedContentLengthExact: string;
  modelRequests: number | null;
  modelRequestsExact: string;
  toolCalls: number | null;
  toolCallsExact: string;
  inputTokens: number | null;
  inputTokensExact: string;
  cacheReadTokens: number | null;
  cacheReadTokensExact: string;
  cacheReadRatio: number;
  outputTokens: number | null;
  outputTokensExact: string;
  reasoningTokens: number | null;
  reasoningTokensExact: string;
  aiCredits: number;
  aiCostUsd: number;
  usageSource: 'copilot_turn_log' | 'otel_trace';
  models: Record<string, number | null>;
  modelsExact: Record<string, string>;
  roi: null;
  roiStatus: string;
}

export type InsightZone = 'typical' | 'elevated' | 'high' | 'unavailable' | 'descriptive';
export type InsightStatus = 'normal' | 'watch' | 'action' | 'unavailable' | 'insufficient' | 'descriptive';
export type InsightTrend = 'improving' | 'worsening' | 'flat' | 'up' | 'down';
export type InsightReferenceKind = 'best_practice' | 'local_measurement';

export interface InsightMetric {
  key: string;
  messageKey?: string;
  actionKey?: string;
  label: string;
  category: string;
  group: 'behavior' | 'evidence';
  scope: 'prompt' | 'request' | 'session' | 'portfolio';
  unit: 'ratio' | 'tokens' | 'requests' | 'calls' | 'usd' | 'seconds' | 'prompts';
  evidence: 'observed' | 'derived';
  description: string;
  current: number | null;
  previous: number | null;
  trend: InsightTrend | null;
  normalZone: {
    low: number;
    high: number;
    method: 'fixed_minimum' | 'fixed_maximum';
    watchMinimum?: number;
    watchMaximum?: number;
  } | null;
  zone: InsightZone;
  status: InsightStatus;
  action: string;
  severity: number | null;
  severityLabel: string | null;
  aggregateSize: number;
  minimumBaseline: number;
  relatedMetrics: string[];
  signal: {
    level: 'none' | 'low' | 'high' | 'danger' | 'insufficient' | 'not_rated' | 'unavailable';
    direction: 'minimum' | 'maximum' | null;
    attentionBoundary: number | null;
    dangerBoundary: number | null;
    minimumSamples: number;
  };
  reference: {
    messageKey?: string;
    kind: InsightReferenceKind;
    support: 'direct' | 'proxy' | 'none';
    label: string;
    url: string;
    note: string;
  };
}

export interface Insights {
  days: number;
  zoneMethod: {
    typical: string;
    elevated: string;
    high: string;
    descriptive: string;
    insufficient: string;
    low: string;
    baselineRule: string;
    coverageRule: string;
  };
  evidenceHealth: {
    sessionUsageCoverage: number | null;
    completeSessions: number;
    eligibleSessions: number;
    directUsageSessions: number;
    otelUsageSessions: number;
    requiredCoverage: number;
    integrityPassed: boolean;
    degraded: boolean;
    message: string | null;
  };
  summary: {
    metrics: number;
    guardrails: number;
    actions: number;
    watch: number;
    insufficient: number;
  };
  priorities: InsightMetric[];
  metrics: InsightMetric[];
}

export interface Methodology {
  claimKey?: string;
  claim: string;
  scenarios: Scenario[];
  formulaVersion?: number;
  config: {
    loadedHourlyRateUsd?: number;
    benchmark?: {
      acknowledgedAssumptions?: boolean;
      manualTimeModelSource?: string;
      capacityRealization?: number;
      capacityRealizationBand?: number[];
      maxIdleGapSeconds?: number;
      typingWordsPerMinute?: number;
      charactersPerWord?: number;
      calibrationSources?: CalibrationSource[];
      phaseToolPatterns?: PhaseToolPatterns;
      presetScenarios?: Record<Scenario, BenchmarkScenario>;
      scenarios?: Record<Scenario, BenchmarkScenario>;
    };
  };
  formulas: Record<string, string>;
}

export type CalibrationEvidenceClass =
  | 'controlled_experiment'
  | 'literature_benchmark'
  | 'official_statistic'
  | 'survey_context'
  | 'local_measurement'
  | 'other';

export type CalibrationSupportLevel = 'direct' | 'proxy' | 'context';

export interface CalibrationSource {
  title: string;
  publisher: string;
  publishedAt: string;
  url: string;
  evidenceClass: CalibrationEvidenceClass;
  appliesTo: string[];
  supportLevels?: Record<string, CalibrationSupportLevel>;
  finding: string;
  limitation: string;
}

export type PhaseToolPatterns = Record<'planning' | 'research' | 'coding' | 'validation', string[]>;

export interface TokenPhaseCalibration {
  relevantTokenFraction: number;
  tokensPerMinute: number;
  interactionMinutesPerTool: number;
  reasoningTokenWeight?: number;
}

export interface BenchmarkScenario {
  planning: TokenPhaseCalibration;
  research: TokenPhaseCalibration;
  coding: { manualEntryFraction: number; wordsPerMinute: number };
  validation: TokenPhaseCalibration;
  unclassifiedManualMultiplier: number;
}

export type Page =
  | { name: 'overall' }
  | { name: 'session'; experiment: string }
  | { name: 'prompts'; experiment: string }
  | { name: 'prompt'; promptId: string; experiment: string }
  | { name: 'insights' }
  | { name: 'methodology' };