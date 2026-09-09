import type { Page } from '../types';

export function parsePage(pathname = window.location.pathname): Page {
  const segments = pathname.split('/').filter(Boolean).map(decodeURIComponent);
  if (segments[0] === 'sessions' && segments[1] && segments[2] === 'prompts' && segments[3]) {
    return { name: 'prompt', experiment: segments[1], promptId: segments[3] };
  }
  if (segments[0] === 'sessions' && segments[1] && segments[2] === 'prompts') {
    return { name: 'prompts', experiment: segments[1] };
  }
  if (segments[0] === 'sessions' && segments[1]) return { name: 'session', experiment: segments[1] };
  if (segments[0] === 'insights') return { name: 'insights' };
  if (segments[0] === 'methodology') return { name: 'methodology' };
  return { name: 'overall' };
}

export function pagePath(page: Page): string {
  if (page.name === 'session') return `/sessions/${encodeURIComponent(page.experiment)}`;
  if (page.name === 'prompts') return `/sessions/${encodeURIComponent(page.experiment)}/prompts`;
  if (page.name === 'prompt') return `/sessions/${encodeURIComponent(page.experiment)}/prompts/${encodeURIComponent(page.promptId)}`;
  if (page.name === 'insights') return '/insights';
  if (page.name === 'methodology') return '/methodology';
  return '/';
}
