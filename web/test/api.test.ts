import { afterEach, describe, expect, test, vi } from 'vitest';
import { api } from '../src/api';

afterEach(() => vi.unstubAllGlobals());

describe('FastAPI client', () => {
  test('builds every endpoint request', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    expect(api.exportCsv()).toBe('/api/export.csv');
    await api.overview('optimistic', 90);
    await api.insights(30);
    await api.session('session / one', 'base');
    await api.prompts('session / one');
    await api.prompt('prompt / one');
    await api.methodology();

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/overview?scenario=optimistic&days=90',
      '/api/insights?days=30',
      '/api/sessions/session%20%2F%20one?scenario=base',
      '/api/sessions/session%20%2F%20one/prompts',
      '/api/prompts/prompt%20%2F%20one',
      '/api/methodology'
    ]);
  });

  test('reports bounded response details for failed requests', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('private detail', { status: 503 })));
    await expect(api.methodology()).rejects.toThrow('Request failed (503): private detail');
  });
});