import { describe, expect, test } from 'vitest';
import { completeWorkerUsage } from '../src/features/calibration/model';
import { mockOverview } from '../mock/data';
import { handleMockApiRequest } from '../mock/server';

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
});