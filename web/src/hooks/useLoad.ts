import { createContext, useContext, useEffect, useState, type DependencyList } from 'react';

export const AutoRefreshContext = createContext(0);

interface LoadState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  retry: () => void;
}

export function useLoad<T>(loader: () => Promise<T>, dependencies: DependencyList): LoadState<T> {
  const refreshMilliseconds = useContext(AutoRefreshContext);
  const [state, setState] = useState<Omit<LoadState<T>, 'retry'>>({ data: null, loading: true, error: null });
  const [retryToken, setRetryToken] = useState(0);
  useEffect(() => {
    let current = true;
    let inFlight = false;
    let retryTimer: number | undefined;
    let refreshTimer: number | undefined;

    const load = (showLoading = false) => {
      if (inFlight) return;
      inFlight = true;
      window.clearTimeout(retryTimer);
      if (showLoading) setState((previous) => ({ ...previous, loading: true, error: null }));
      loader().then(
        (data) => {
          inFlight = false;
          if (current) setState({ data, loading: false, error: null });
        },
        (error: unknown) => {
          inFlight = false;
          if (!current) return;
          setState({ data: null, loading: false, error: error instanceof Error ? error.message : String(error) });
          retryTimer = window.setTimeout(() => load(false), 2_000);
        }
      );
    };

    load(true);
    if (refreshMilliseconds > 0) {
      refreshTimer = window.setInterval(() => load(false), refreshMilliseconds);
    }
    return () => {
      current = false;
      window.clearTimeout(retryTimer);
      window.clearInterval(refreshTimer);
    };
  }, [...dependencies, retryToken, refreshMilliseconds]);
  return { ...state, retry: () => setRetryToken((value) => value + 1) };
}
