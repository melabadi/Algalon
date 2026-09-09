import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { access, readdir, stat } from 'node:fs/promises';
import path from 'node:path';
import { createInterface } from 'node:readline';
import type { ExperimentUsage, ModelUsage, SessionTimeEvidence } from './experiment-types.js';

interface MutableUsage {
  requests: number;
  inputTokens: number;
  cacheReadTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  nanoAiCredits: number;
}

async function directChatLogPath(root: string, chatSessionId: string): Promise<string | null> {
  try {
    for (const workspace of await readdir(root, { withFileTypes: true })) {
      if (!workspace.isDirectory()) continue;
      const candidate = path.join(root, workspace.name, 'GitHub.copilot-chat', 'debug-logs', chatSessionId, 'main.jsonl');
      try {
        await access(candidate);
        return candidate;
      } catch {
      }
    }
  } catch {
  }
  return null;
}

export async function directTurnLogSignature(
  root: string,
  chatSessionId: string | readonly string[]
): Promise<string | null> {
  const chatSessionIds = [...new Set(
    typeof chatSessionId === 'string' ? [chatSessionId] : chatSessionId
  )].sort();
  if (chatSessionIds.length === 0) return null;
  const fingerprints: string[] = [];
  for (const currentChatSessionId of chatSessionIds) {
    const logPath = await directChatLogPath(root, currentChatSessionId);
    if (!logPath) return null;
    try {
      const metadata = await stat(logPath, { bigint: true });
      fingerprints.push([
        currentChatSessionId,
        metadata.dev,
        metadata.ino,
        metadata.size,
        metadata.mtimeNs
      ].join(':'));
    } catch {
      return null;
    }
  }
  return createHash('sha256').update(fingerprints.join('|')).digest('hex');
}

export function reuseDirectTurnUsage(
  usage: ExperimentUsage,
  timing: SessionTimeEvidence
): ExperimentUsage {
  return {
    ...usage,
    phases: timing.phases,
    elapsedSeconds: timing.elapsedSeconds,
    engagedSeconds: timing.engagedSeconds,
    activeSeconds: timing.activeSeconds,
    activityDensity: timing.activityDensity
  };
}

export async function collectDirectTurnUsage(
  root: string,
  chatSessionId: string | readonly string[],
  start: Date,
  end: Date,
  timing: SessionTimeEvidence
): Promise<ExperimentUsage | null> {
  const chatSessionIds = [...new Set(typeof chatSessionId === 'string' ? [chatSessionId] : chatSessionId)].sort();
  if (chatSessionIds.length === 0) return null;
  const startMilliseconds = start.getTime();
  const endMilliseconds = end.getTime();
  const models = new Map<string, MutableUsage>();
  for (const currentChatSessionId of chatSessionIds) {
    const logPath = await directChatLogPath(root, currentChatSessionId);
    if (!logPath) return null;
    const input = createReadStream(logPath, { encoding: 'utf8' });
    const lines = createInterface({ input, crlfDelay: Infinity });
    let includeTurn = false;
    let conversationRequests = 0;
    let conversationNanoAiCredits = 0;
    try {
      for await (const line of lines) {
        if (!line.trim()) continue;
        let event: { ts?: number; type?: string; attrs?: Record<string, unknown> };
        try {
          event = JSON.parse(line) as typeof event;
        } catch {
          continue;
        }
        if (event.type === 'user_message') {
          includeTurn = typeof event.ts === 'number' && event.ts >= startMilliseconds && event.ts <= endMilliseconds;
          continue;
        }
        if (event.type !== 'llm_request' || !includeTurn) continue;
        const attributes = event.attrs ?? {};
        const model = String(attributes.model ?? 'unknown');
        const usage = models.get(model) ?? { requests: 0, inputTokens: 0, cacheReadTokens: 0, outputTokens: 0, reasoningTokens: 0, nanoAiCredits: 0 };
        const nanoAiCredits = Number(attributes.copilotUsageNanoAiu ?? 0);
        usage.requests += 1;
        usage.inputTokens += Number(attributes.inputTokens ?? 0);
        usage.cacheReadTokens += Number(attributes.cachedTokens ?? 0);
        usage.outputTokens += Number(attributes.outputTokens ?? 0);
        usage.reasoningTokens += Number(attributes.reasoningTokens ?? 0);
        usage.nanoAiCredits += nanoAiCredits;
        models.set(model, usage);
        conversationRequests += 1;
        conversationNanoAiCredits += nanoAiCredits;
      }
    } finally {
      lines.close();
      input.destroy();
    }
    if (conversationRequests === 0 || conversationNanoAiCredits <= 0) return null;
  }
  const modelUsage = [...models.entries()].map(([model, usage]): ModelUsage => {
    const aiCredits = usage.nanoAiCredits / 1_000_000_000;
    return {
      model,
      requests: usage.requests,
      inputTokens: usage.inputTokens,
      cacheReadTokens: usage.cacheReadTokens,
      uncachedInputTokens: Math.max(0, usage.inputTokens - usage.cacheReadTokens),
      outputTokens: usage.outputTokens,
      reasoningTokens: usage.reasoningTokens,
      aiCredits,
      aiCostUsd: aiCredits / 100
    };
  }).sort((left, right) => left.model.localeCompare(right.model));
  const aiCredits = modelUsage.reduce((total, usage) => total + usage.aiCredits, 0);
  if (modelUsage.length === 0 || aiCredits <= 0) return null;
  return {
    source: 'copilot_turn_log',
    chatSpans: modelUsage.reduce((total, usage) => total + usage.requests, 0),
    inputTokens: modelUsage.reduce((total, usage) => total + usage.inputTokens, 0),
    cacheReadTokens: modelUsage.reduce((total, usage) => total + usage.cacheReadTokens, 0),
    uncachedInputTokens: modelUsage.reduce((total, usage) => total + usage.uncachedInputTokens, 0),
    outputTokens: modelUsage.reduce((total, usage) => total + usage.outputTokens, 0),
    reasoningTokens: modelUsage.reduce((total, usage) => total + usage.reasoningTokens, 0),
    aiCredits,
    aiCostUsd: aiCredits / 100,
    models: modelUsage,
    phases: timing.phases,
    elapsedSeconds: timing.elapsedSeconds,
    engagedSeconds: timing.engagedSeconds,
    activeSeconds: timing.activeSeconds,
    activityDensity: timing.activityDensity
  };
}
