import type { BenchmarkPhase } from './benchmark.js';
import type { PhaseEvidence } from './phase-evidence.js';

export interface ModelUsage {
  model: string;
  requests: number;
  inputTokens: number;
  cacheReadTokens: number;
  uncachedInputTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  aiCredits: number;
  aiCostUsd: number;
}

export interface SessionTimeEvidence {
  phases: Record<BenchmarkPhase, PhaseEvidence>;
  elapsedSeconds: number;
  engagedSeconds: number;
  activeSeconds: number;
  activityDensity: number;
}

export interface ExperimentUsage extends SessionTimeEvidence {
  source: 'otel_traces' | 'copilot_turn_log';
  chatSpans: number;
  inputTokens: number;
  cacheReadTokens: number;
  uncachedInputTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  aiCredits: number;
  aiCostUsd: number;
  models: ModelUsage[];
}

export interface TraceSession {
  sessionId: string;
  chatSessionIds?: string[];
  startedAt: string;
  endedAt: string;
  aiCostUsd: number;
}

export interface TraceSessionEvidence {
  session: TraceSession;
  usage: ExperimentUsage;
}
