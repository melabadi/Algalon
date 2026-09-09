import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { api } from '../src/api';
import { IndexingStatus } from '../src/components/IndexingStatus';
import { AutoRefreshContext } from '../src/hooks/useLoad';
import type { IndexingProgress } from '../src/types';

const current: IndexingProgress = {
  state: 'current', pendingSessions: 0, blockedSessions: 0, oldestPendingSeconds: 0,
  lastSuccessfulAt: '2026-09-09T10:00:00Z', lastDiscoveryAt: '2026-09-09T10:00:00Z', reason: null,
};

afterEach(() => vi.restoreAllMocks());

test.each([
  ['current', 0, 'Index current'],
  ['catching_up', 3, 'Catching up (3)'],
  ['blocked', 1, 'Indexing blocked (1)'],
] as const)('shows durable %s progress', async (state, pendingSessions, label) => {
  vi.spyOn(api, 'indexing').mockResolvedValue({ ...current, state, pendingSessions });
  render(<IndexingStatus />);
  expect(await screen.findByText(label)).toBeVisible();
  expect(screen.getByRole('status', { name: 'Indexing status' })).toHaveAttribute('title', expect.stringContaining('last success:'));
});

test('does not claim current while loading and recovers from unavailable status', async () => {
  const request = vi.spyOn(api, 'indexing').mockRejectedValueOnce(new Error('offline')).mockResolvedValue(current);
  render(<IndexingStatus />);
  expect(screen.getByText('Checking index')).toBeVisible();
  expect(await screen.findByText('Index status unavailable')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Retry indexing status' }));
  expect(await screen.findByText('Index current')).toBeVisible();
  expect(request).toHaveBeenCalledTimes(2);
});

test('refreshes the indexing indicator independently of the parent data interval', async () => {
  vi.useFakeTimers();
  try {
    const request = vi.spyOn(api, 'indexing').mockResolvedValueOnce(current).mockResolvedValue({
      ...current, state: 'blocked', pendingSessions: 1, oldestPendingSeconds: 65,
    });
    render(<AutoRefreshContext.Provider value={0}><AutoRefreshContext.Provider value={5_000}><IndexingStatus /></AutoRefreshContext.Provider></AutoRefreshContext.Provider>);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText('Index current')).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(screen.getByText('Indexing blocked (1)')).toBeVisible();
    expect(request).toHaveBeenCalledTimes(2);
  } finally {
    vi.useRealTimers();
  }
});