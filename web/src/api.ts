import type { Insights, Methodology, Overview, Prompt, Scenario, Session } from './types';

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Request failed (${response.status}): ${detail.slice(0, 300)}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  exportCsv: () => '/api/export.csv',
  overview: (scenario: Scenario, days: number) => get<Overview>(`/api/overview?scenario=${scenario}&days=${days}`),
  insights: (days: number) => get<Insights>(`/api/insights?days=${days}`),
  session: (experiment: string, scenario: Scenario) => get<Session>(`/api/sessions/${encodeURIComponent(experiment)}?scenario=${scenario}`),
  prompts: (experiment: string) => get<Prompt[]>(`/api/sessions/${encodeURIComponent(experiment)}/prompts`),
  prompt: (promptId: string) => get<Prompt>(`/api/prompts/${encodeURIComponent(promptId)}`),
  methodology: () => get<Methodology>('/api/methodology')
};