import { AlertTriangle, CheckCircle2, Clock3, RefreshCw } from 'lucide-react';
import { api } from '../api';
import { useLoad } from '../hooks/useLoad';
import { messages } from '../i18n';

export function IndexingStatus() {
  const { data, error, retry } = useLoad(api.indexing, []);
  const state = error ? 'unavailable' : data?.state ?? 'checking';
  const Icon = state === 'current' ? CheckCircle2 : state === 'blocked' || state === 'unavailable' ? AlertTriangle : Clock3;
  const detail = data ? messages.shell.indexingDetail(
    data.pendingSessions, data.oldestPendingSeconds,
    data.lastSuccessfulAt ? new Date(data.lastSuccessfulAt).toLocaleString() : messages.shell.indexingNotYet,
  ) : undefined;
  return (
    <span className="local-status indexing-status" data-state={state} role="status" aria-label={messages.shell.indexingStatus} title={detail}>
      <Icon size={13} aria-hidden="true" />
      <span>{messages.shell.indexingStates[state]}{data && data.pendingSessions > 0 ? ` (${data.pendingSessions})` : ''}</span>
      {error && <button className="indexing-retry" onClick={retry} aria-label={messages.shell.indexingRetry} title={messages.shell.indexingRetry}><RefreshCw size={14} /></button>}
    </span>
  );
}