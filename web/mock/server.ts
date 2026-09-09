import type { Plugin } from 'vite';
import {
  mockCsv,
  mockInsights,
  mockMethodology,
  mockOverview,
  mockPrompt,
  mockSession,
  mockSessionPrompts
} from './data';
import type { Scenario } from '../src/types';

export interface MockApiResponse {
  status: number;
  headers: Record<string, string>;
  body: string;
}

function response(body: unknown, status = 200): MockApiResponse {
  return {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
    body: JSON.stringify(body)
  };
}

function error(detail: string, status: number): MockApiResponse {
  return response({ detail }, status);
}

function scenarioFrom(url: URL): Scenario | null {
  const scenario = url.searchParams.get('scenario') ?? 'base';
  return scenario === 'pessimistic' || scenario === 'base' || scenario === 'optimistic' ? scenario : null;
}

function daysFrom(url: URL): number | null {
  const rawDays = url.searchParams.get('days') ?? '30';
  const days = Number(rawDays);
  return Number.isInteger(days) && days >= 1 && days <= 3_650 ? days : null;
}

function decodeSegment(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

export function handleMockApiRequest(method: string, requestUrl: string): MockApiResponse | null {
  const url = new URL(requestUrl, 'http://127.0.0.1');
  if (!url.pathname.startsWith('/api/')) return null;
  if (method !== 'GET' && method !== 'HEAD') return error('method not allowed', 405);

  if (url.pathname === '/api/health') return response({ status: 'ok', mode: 'mock' });
  if (url.pathname === '/api/methodology') return response(mockMethodology);
  if (url.pathname === '/api/export.csv') {
    return {
      status: 200,
      headers: {
        'Content-Type': 'text/csv; charset=utf-8',
        'Cache-Control': 'no-store',
        'Content-Disposition': 'attachment; filename="algalon-mock-data.csv"'
      },
      body: mockCsv()
    };
  }
  if (url.pathname === '/api/overview') {
    const scenario = scenarioFrom(url);
    const days = daysFrom(url);
    if (!scenario) return error('scenario must be one of pessimistic, base, optimistic', 400);
    if (days === null) return error('days must be an integer from 1 through 3650', 422);
    return response(mockOverview(scenario, days));
  }
  if (url.pathname === '/api/insights') {
    const days = daysFrom(url);
    if (days === null) return error('days must be an integer from 1 through 3650', 422);
    return response(mockInsights(days));
  }

  const sessionPromptsMatch = url.pathname.match(/^\/api\/sessions\/([^/]+)\/prompts$/);
  if (sessionPromptsMatch) {
    const experiment = decodeSegment(sessionPromptsMatch[1]);
    if (!experiment) return error('invalid session id', 400);
    if (!mockSession(experiment, 'base')) return error('session not found', 404);
    return response(mockSessionPrompts(experiment));
  }

  const sessionMatch = url.pathname.match(/^\/api\/sessions\/([^/]+)$/);
  if (sessionMatch) {
    const experiment = decodeSegment(sessionMatch[1]);
    const scenario = scenarioFrom(url);
    if (!experiment) return error('invalid session id', 400);
    if (!scenario) return error('scenario must be one of pessimistic, base, optimistic', 400);
    const session = mockSession(experiment, scenario);
    return session ? response(session) : error('session not found', 404);
  }

  const promptMatch = url.pathname.match(/^\/api\/prompts\/([^/]+)$/);
  if (promptMatch) {
    const promptId = decodeSegment(promptMatch[1]);
    if (!promptId) return error('invalid prompt id', 400);
    const prompt = mockPrompt(promptId);
    return prompt ? response(prompt) : error('prompt not found', 404);
  }

  return error('not found', 404);
}

export function mockApiPlugin(): Plugin {
  return {
    name: 'algalon-mock-api',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((request, serverResponse, next) => {
        const mockResponse = handleMockApiRequest(request.method ?? 'GET', request.url ?? '/');
        if (!mockResponse) {
          next();
          return;
        }
        serverResponse.statusCode = mockResponse.status;
        for (const [name, value] of Object.entries(mockResponse.headers)) {
          serverResponse.setHeader(name, value);
        }
        serverResponse.end(request.method === 'HEAD' ? '' : mockResponse.body);
      });
    }
  };
}