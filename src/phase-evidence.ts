import { benchmarkPhases, type BenchmarkPhase } from './benchmark.js';
import { normalizeIdentifier } from './normalize.js';
import { unionIntervalDuration } from '../shared/intervals.js';

export type PhaseToolPatterns = Record<Exclude<BenchmarkPhase, 'unclassified'>, string[]>;

export interface TimedPhaseSpan {
  identity: string;
  name: string;
  toolName: string | undefined;
  startMilliseconds: number;
  endMilliseconds: number;
  nanoAiCredits: number;
  uncachedInputTokens: number;
  outputTokens: number;
  reasoningTokens: number;
}

export interface PhaseEvidence {
  activeSeconds: number;
  allocatedSeconds: number;
  toolActiveSeconds: number;
  toolCalls: number;
  modelSpans: number;
  uncachedInputTokens: number;
  outputTokens: number;
  reasoningTokens: number;
}

export interface PhaseAllocation {
  phases: Record<BenchmarkPhase, PhaseEvidence>;
  elapsedSeconds: number;
  engagedSeconds: number;
  activeSeconds: number;
  activityDensity: number;
}

/** Idle longer than this is not treated as session work; shorter gaps stay as think time. */
export const defaultMaxIdleGapSeconds = 300;

interface PhaseInterval {
  phase: BenchmarkPhase;
  startMilliseconds: number;
  endMilliseconds: number;
}

function engagedMilliseconds(
  intervals: Array<{ startMilliseconds: number; endMilliseconds: number }>,
  maxIdleGapMilliseconds: number
): number {
  const ordered = [...intervals].sort((left, right) => left.startMilliseconds - right.startMilliseconds);
  let engaged = 0;
  let blockStart: number | null = null;
  let blockEnd = 0;
  for (const interval of ordered) {
    if (blockStart === null) {
      blockStart = interval.startMilliseconds;
      blockEnd = interval.endMilliseconds;
      continue;
    }
    if (interval.startMilliseconds - blockEnd <= maxIdleGapMilliseconds) {
      blockEnd = Math.max(blockEnd, interval.endMilliseconds);
      continue;
    }
    engaged += blockEnd - blockStart;
    blockStart = interval.startMilliseconds;
    blockEnd = interval.endMilliseconds;
  }
  return blockStart === null ? 0 : engaged + (blockEnd - blockStart);
}

function classifyTool(toolName: string | undefined, patterns: PhaseToolPatterns): BenchmarkPhase {
  if (!toolName) return 'unclassified';
  const normalized = normalizeIdentifier(toolName);
  for (const phase of benchmarkPhases) {
    if (phase === 'unclassified') continue;
    if (patterns[phase].some((pattern) => normalized.includes(normalizeIdentifier(pattern)))) {
      return phase;
    }
  }
  return 'unclassified';
}

export function calculatePhaseEvidence(
  spans: TimedPhaseSpan[],
  startMilliseconds: number,
  endMilliseconds: number,
  patterns: PhaseToolPatterns,
  maxIdleGapSeconds: number = defaultMaxIdleGapSeconds
): PhaseAllocation {
  const intervals: PhaseInterval[] = [];
  const toolSpans = spans.filter((span) => span.toolName || span.name.startsWith('execute_tool '));
  const toolCalls = Object.fromEntries(benchmarkPhases.map((phase) => [phase, 0])) as Record<BenchmarkPhase, number>;
  const modelSpans = Object.fromEntries(benchmarkPhases.map((phase) => [phase, 0])) as Record<BenchmarkPhase, number>;
  const uncachedInputTokens = Object.fromEntries(benchmarkPhases.map((phase) => [phase, 0])) as Record<BenchmarkPhase, number>;
  const outputTokens = Object.fromEntries(benchmarkPhases.map((phase) => [phase, 0])) as Record<BenchmarkPhase, number>;
  const reasoningTokens = Object.fromEntries(benchmarkPhases.map((phase) => [phase, 0])) as Record<BenchmarkPhase, number>;
  const toolIntervals: PhaseInterval[] = [];

  for (const span of toolSpans) {
    const phase = classifyTool(span.toolName ?? span.name.replace(/^execute_tool\s+/i, ''), patterns);
    toolCalls[phase] += 1;
    const interval = { phase, startMilliseconds: span.startMilliseconds, endMilliseconds: span.endMilliseconds };
    intervals.push(interval);
    toolIntervals.push(interval);
  }

  for (const span of spans.filter((candidate) => candidate.name.startsWith('chat ') && candidate.nanoAiCredits > 0)) {
    const nextTool = toolSpans
      .filter((tool) => tool.startMilliseconds >= span.endMilliseconds - 250 &&
        tool.startMilliseconds <= span.endMilliseconds + 3_000)
      .sort((left, right) => left.startMilliseconds - right.startMilliseconds)[0];
    const phase = classifyTool(
      nextTool?.toolName ?? nextTool?.name.replace(/^execute_tool\s+/i, ''),
      patterns
    );
    modelSpans[phase] += 1;
    uncachedInputTokens[phase] += span.uncachedInputTokens;
    outputTokens[phase] += span.outputTokens;
    reasoningTokens[phase] += span.reasoningTokens;
    intervals.push({ phase, startMilliseconds: span.startMilliseconds, endMilliseconds: span.endMilliseconds });
  }

  const clipped = intervals
    .map((interval) => ({
      ...interval,
      startMilliseconds: Math.max(startMilliseconds, interval.startMilliseconds),
      endMilliseconds: Math.min(endMilliseconds, interval.endMilliseconds)
    }))
    .filter((interval) => interval.endMilliseconds > interval.startMilliseconds);
  const boundaries = [...new Set(clipped.flatMap((interval) => [
    interval.startMilliseconds, interval.endMilliseconds
  ]))].sort((left, right) => left - right);
  const activeMilliseconds = Object.fromEntries(benchmarkPhases.map((phase) => [phase, 0])) as Record<BenchmarkPhase, number>;
  let activeUnionMilliseconds = 0;

  for (let index = 0; index < boundaries.length - 1; index += 1) {
    const segmentStart = boundaries[index] as number;
    const segmentEnd = boundaries[index + 1] as number;
    const midpoint = (segmentStart + segmentEnd) / 2;
    const coveringPhases = [...new Set(clipped
      .filter((interval) => interval.startMilliseconds <= midpoint && interval.endMilliseconds >= midpoint)
      .map((interval) => interval.phase))];
    if (coveringPhases.length === 0) continue;
    const duration = segmentEnd - segmentStart;
    activeUnionMilliseconds += duration;
    for (const phase of coveringPhases) {
      activeMilliseconds[phase] += duration / coveringPhases.length;
    }
  }

  const elapsedSeconds = (endMilliseconds - startMilliseconds) / 1_000;
  const engagedSeconds = engagedMilliseconds(clipped, Math.max(0, maxIdleGapSeconds) * 1_000) / 1_000;
  const phases = Object.fromEntries(benchmarkPhases.map((phase) => {
    const activeSeconds = activeMilliseconds[phase] / 1_000;
    const allocatedSeconds = activeUnionMilliseconds > 0
      ? engagedSeconds * activeMilliseconds[phase] / activeUnionMilliseconds
      : 0;
    return [phase, {
      activeSeconds,
      allocatedSeconds,
      toolActiveSeconds: unionIntervalDuration(toolIntervals
        .filter((interval) => interval.phase === phase)
        .map((interval) => [interval.startMilliseconds, interval.endMilliseconds] as const)) / 1_000,
      toolCalls: toolCalls[phase],
      modelSpans: modelSpans[phase],
      uncachedInputTokens: uncachedInputTokens[phase],
      outputTokens: outputTokens[phase],
      reasoningTokens: reasoningTokens[phase]
    }];
  })) as Record<BenchmarkPhase, PhaseEvidence>;

  return {
    phases,
    elapsedSeconds,
    engagedSeconds,
    activeSeconds: activeUnionMilliseconds / 1_000,
    activityDensity: elapsedSeconds > 0 ? activeUnionMilliseconds / 1_000 / elapsedSeconds : 0
  };
}