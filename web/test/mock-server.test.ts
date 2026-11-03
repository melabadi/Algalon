import { describe, expect, test } from 'vitest';
import { completeWorkerUsage } from '../src/features/calibration/model';
import { mockOverview } from '../mock/data';
import { handleMockApiRequest } from '../mock/server';
import type { Insights, Overview, Prompt } from '../src/types';

function json(path: string): { status: number; body: unknown } {
  const result = handleMockApiRequest('GET', path);
  if (!result) throw new Error(`Mock API did not handle ${path}`);
  return { status: result.status, body: JSON.parse(result.body) };
}

describe('mock API server', () => {
  test('serves the complete frontend API contract', () => {
    const health = json('/api/health');
    expect(health).toEqual({ status: 200, body: { status: 'ok', mode: 'mock' } });

    const overview = json('/api/overview?scenario=optimistic&days=30');
    expect(overview.status).toBe(200);
    expect(overview.body).toMatchObject({ scenario: 'optimistic', days: 30, modelingStatus: 'available' });
    expect((overview.body as { sessions: unknown[] }).sessions.length).toBeGreaterThan(2);

    const session = json('/api/sessions/checkout-flow-refactor?scenario=pessimistic');
    expect(session.body).toMatchObject({ experiment: 'checkout-flow-refactor', scenario: 'pessimistic' });

    const prompts = json('/api/sessions/checkout-flow-refactor/prompts');
    expect((prompts.body as unknown[])).toHaveLength(3);

    const prompt = json('/api/prompts/checkout-flow-refactor-prompt-1');
    expect(prompt.body).toMatchObject({ experiment: 'checkout-flow-refactor', ordinal: 1 });

    const insights = json('/api/insights?days=90');
    expect(insights.body).toMatchObject({ days: 90, evidenceHealth: { integrityPassed: true } });

    const methodology = json('/api/methodology');
    expect(methodology.body).toMatchObject({ formulaVersion: 2, scenarios: ['pessimistic', 'base', 'optimistic'] });

    const csv = handleMockApiRequest('GET', '/api/export.csv');
    expect(csv?.headers['Content-Type']).toContain('text/csv');
    expect(csv?.body).toContain('checkout-flow-refactor-prompt-1');
  });

  test('matches live API validation and missing-resource behavior', () => {
    expect(json('/api/overview?scenario=impossible').status).toBe(400);
    expect(json('/api/insights?days=0').status).toBe(422);
    expect(json('/api/sessions/missing').status).toBe(404);
    expect(json('/api/prompts/missing').status).toBe(404);
    expect(json('/api/unknown').status).toBe(404);
    expect(handleMockApiRequest('POST', '/api/overview')?.status).toBe(405);
    expect(handleMockApiRequest('GET', '/assets/app.js')).toBeNull();
  });

  test('keeps synthetic sessions eligible for custom calibration', () => {
    const overview = mockOverview('base', 30);
    expect(overview.sessions).not.toHaveLength(0);
    expect(overview.sessions.every(completeWorkerUsage)).toBe(true);
  });

  test('mirrors the Copilot runtime session and prompt contract', () => {
    const overview = json('/api/overview?scenario=base&days=30').body as Overview;
    expect(new Set(overview.sessions.map((session) => session.surface))).toEqual(new Set(['vscode', 'cli', 'mixed', 'app', 'sdk']));
    const automated = overview.sessions.filter((session) => session.sessionMode === 'automated');
    expect(automated).toHaveLength(1);
    expect(automated[0]).toMatchObject({
      surface: 'sdk', exclusionReason: 'automated_session', modelingStatus: 'unavailable', scenarioResult: null, benchmark: null
    });
    expect(automated[0].aiCostUsd).toBeGreaterThan(0);
    expect(overview.sessions.filter((session) => session.sessionMode === 'interactive').every((session) => session.exclusionReason === null)).toBe(true);
    expect(overview.sessions.every((session) => session.usage.source === 'otel_traces')).toBe(true);
    expect(overview.totals).toMatchObject({
      sessions: overview.sessions.length - 1,
      automatedSessions: 1,
      automatedAiCostUsd: automated[0].aiCostUsd
    });
    const interactiveCost = overview.sessions
      .filter((session) => session.sessionMode === 'interactive')
      .reduce((total, session) => total + session.aiCostUsd, 0);
    expect(overview.totals.aiCostUsd).toBeCloseTo(interactiveCost, 6);

    const automatedPrompts = json(`/api/sessions/${automated[0].experiment}/prompts`).body as Prompt[];
    expect(automatedPrompts).toHaveLength(automated[0].promptCount);
    for (const session of overview.sessions) {
      const sessionPrompts = json(`/api/sessions/${session.experiment}/prompts`).body as Prompt[];
      expect(sessionPrompts.every((prompt) => prompt.content === '' && !prompt.contentAvailable
        && prompt.capturedContentLength === 0 && prompt.usageSource === 'otel_trace')).toBe(true);
    }

    const insights = json('/api/insights?days=30').body as Insights;
    expect(insights.evidenceHealth).not.toHaveProperty('directUsageSessions');
    expect(insights.evidenceHealth.otelUsageSessions).toBe(overview.sessions.length);

    const methodology = json('/api/methodology').body as { formulas: Record<string, string> };
    expect(methodology.formulas.aiCost).toBe('C_AI = sum(github.copilot.nano_aiu over chat spans) / 1e11');

    const csv = handleMockApiRequest('GET', '/api/export.csv')?.body ?? '';
    expect(csv.split('\r\n')[0]).toBe('record_type,session_id,prompt_id,started_at,status,surface,session_mode,exclusion_reason,ai_cost_usd');
    expect(csv).toContain(`session,${automated[0].experiment},,`);
    expect(csv).toContain('sdk,automated,automated_session');
  });
});