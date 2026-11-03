import type { BenchmarkPhase } from './benchmark.js';
import type { PhaseEvidence } from './phase-evidence.js';
import type { SessionMode, SessionSurface } from './runtime-telemetry.js';

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
  source: 'otel_traces';
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
  surface: SessionSurface;
  mode: SessionMode;
  startedAt: string;
  endedAt: string;
  aiCostUsd: number;
}

export interface TraceSessionEvidence {
  session: TraceSession;
  usage: ExperimentUsage;
}
