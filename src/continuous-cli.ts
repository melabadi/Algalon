import { createHash } from 'node:crypto';
import { access, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import {
  canonicalizeUsageCosts,
  earliestSessionStart,
  evaluateOtelOnlyWindow,
  migrationUsageFromRefreshedEvidence,
  modelConfigurationSignature,
  publishAndCommitSession,
  SESSION_CALCULATION_VERSION,
  sessionCoverageRegressed,
  sessionNeedsEvaluation,
  sessionStateForObservedBounds,
  sessionExperimentName
} from './continuous.js';
import {
  collectDirectTurnUsage,
  directTurnLogSignature,
  reuseDirectTurnUsage,
  type ExperimentUsage,
  type TraceSessionEvidence
} from './experiment-evidence.js';
import {
  IncrementalTraceSessionCollector,
  OtlpInboxSessionCollector,
  type TraceEvidenceState
} from './incremental-evidence.js';
import { valueConfigSchema, type ValueConfig } from './schema.js';

interface TrackedSession {
  sessionId: string;
  experiment: string;
  startedAt: string;
  publishedThrough?: string;
  calculationVersion?: number;
  publishedUsageSignature?: string;
  publishedModelConfigurationSignature?: string;
  directLogSignature?: string | null;
  directUsage?: ExperimentUsage;
}

interface SessionWorkerState {
  initializedAt: string;
  sessions: TrackedSession[];
  trace?: TraceEvidenceState;
}

const DIRECT_LOG_RECHECK_MILLISECONDS = 30_000;
const DIRECT_LOG_SETTLE_MILLISECONDS = 5 * 60_000;

function positiveInteger(value: string | undefined, fallback: number, name: string): number {
  const parsed = Number(value ?? fallback);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer.`);
  }
  return parsed;
}

async function waitForEndpoint(name: string, endpoint: string): Promise<void> {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(endpoint, { signal: AbortSignal.timeout(3_000) });
      if (response.status < 500) return;
    } catch {
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`${name} did not become ready at '${endpoint}'.`);
}

async function loadState(statePath: string): Promise<SessionWorkerState | null> {
  try {
    const state = JSON.parse(await readFile(statePath, 'utf8')) as SessionWorkerState;
    return {
      initializedAt: state.initializedAt,
      sessions: state.sessions.map((session) => ({
        sessionId: session.sessionId,
        experiment: session.experiment,
        startedAt: session.startedAt,
        ...(session.publishedThrough ? { publishedThrough: session.publishedThrough } : {}),
        ...(session.calculationVersion !== undefined
          ? { calculationVersion: session.calculationVersion }
          : {}),
        ...(session.publishedUsageSignature
          ? { publishedUsageSignature: session.publishedUsageSignature }
          : {}),
        ...(session.publishedModelConfigurationSignature
          ? { publishedModelConfigurationSignature: session.publishedModelConfigurationSignature }
          : {}),
        ...(session.directLogSignature !== undefined
          ? { directLogSignature: session.directLogSignature }
          : {}),
        ...(session.directUsage && typeof session.directUsage === 'object'
          ? { directUsage: canonicalizeUsageCosts(session.directUsage) }
          : {})
      })),
      ...((state.trace?.version === 1 || state.trace?.version === 2 ||
        state.trace?.version === 3 || state.trace?.version === 4)
        && state.trace.evidence?.version === 1
        ? { trace: state.trace }
        : {})
    };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw error;
  }
}

async function saveState(statePath: string, state: SessionWorkerState): Promise<void> {
  const temporaryPath = `${statePath}.${process.pid}.tmp`;
  await writeFile(temporaryPath, `${JSON.stringify(state)}\n`, 'utf8');
  await rename(temporaryPath, statePath);
}

function usageSignature(usage: ExperimentUsage): string {
  return createHash('sha256').update(JSON.stringify(usage)).digest('hex');
}

async function publishTrackedSession(
  config: ValueConfig,
  outputDirectory: string,
  trackedSession: TrackedSession,
  completedAt: string,
  usage: ExperimentUsage,
  inboxCursor?: number
) {
  const result = await evaluateOtelOnlyWindow({
    experiment: trackedSession.experiment,
    usage,
    startedAt: trackedSession.startedAt,
    completedAt,
    config
  }, {
    publish: async (payload, workerConfig) => {
      const { publishMetrics } = await import('./metrics.js');
      await publishMetrics(payload, workerConfig.victoriaMetricsUrl);
    }
  });
  const publishedUsageSignature = usageSignature(usage);
  const publishedModelConfigurationSignature = modelConfigurationSignature(config);
  const artifactPath = path.join(outputDirectory, `${trackedSession.experiment}.json`);
  const temporaryArtifactPath = `${artifactPath}.${process.pid}.tmp`;
  await writeFile(temporaryArtifactPath, `${JSON.stringify({
    calculationVersion: SESSION_CALCULATION_VERSION,
    ...(inboxCursor !== undefined ? { inboxCursor } : {}),
    modelConfigurationSignature: publishedModelConfigurationSignature,
    experiment: trackedSession.experiment,
    startedAt: trackedSession.startedAt,
    completedAt,
    status: result.status,
    usage: result.usage,
    source: result.source,
    benchmark: result.benchmark
  }, null, 2)}\n`, 'utf8');
  await rename(temporaryArtifactPath, artifactPath);
  trackedSession.publishedThrough = completedAt;
  trackedSession.calculationVersion = SESSION_CALCULATION_VERSION;
  trackedSession.publishedUsageSignature = publishedUsageSignature;
  trackedSession.publishedModelConfigurationSignature = publishedModelConfigurationSignature;
  return result;
}

async function sleep(milliseconds: number, registerWake: (wake: () => void) => void): Promise<void> {
  await new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, milliseconds);
    registerWake(() => {
      clearTimeout(timer);
      resolve();
    });
  });
}

async function main(): Promise<void> {
  const configPath = process.env.COPILOT_VALUE_CONFIG ?? '/app/config/value-model.local.json';
  const parsedConfig = valueConfigSchema.parse(JSON.parse(await readFile(configPath, 'utf8')));
  const config = valueConfigSchema.parse({
    ...parsedConfig,
    victoriaMetricsUrl: process.env.COPILOT_VALUE_VICTORIA_URL ?? parsedConfig.victoriaMetricsUrl,
    otelHttpEndpoint: process.env.COPILOT_VALUE_OTEL_URL ?? parsedConfig.otelHttpEndpoint,
    traceArchivePath: process.env.COPILOT_VALUE_TRACE_ARCHIVE ?? parsedConfig.traceArchivePath
  });
  const currentModelConfigurationSignature = modelConfigurationSignature(config);
  const pollSeconds = positiveInteger(
    process.env.COPILOT_VALUE_POLL_SECONDS,
    5,
    'COPILOT_VALUE_POLL_SECONDS'
  );
  const outputDirectory = process.env.COPILOT_VALUE_OUTPUT_DIR ?? '/data/value/sessions';
  const inboxUrl = process.env.COPILOT_VALUE_OTEL_INBOX_URL;
  const chatLogRoot = process.env.COPILOT_VALUE_CHAT_LOG_ROOT;
  await mkdir(outputDirectory, { recursive: true });
  const statePath = path.join(outputDirectory, 'state.json');

  await Promise.all([
    waitForEndpoint('OpenTelemetry Collector', `${config.otelHttpEndpoint.replace(/\/$/, '')}/`),
    waitForEndpoint('VictoriaMetrics', `${config.victoriaMetricsUrl.replace(/\/$/, '')}/-/healthy`),
    ...(inboxUrl ? [waitForEndpoint('OTLP inbox', inboxUrl)] : []),
  ]);

  let stopped = false;
  let wake: (() => void) | undefined;
  const stop = () => {
    stopped = true;
    wake?.();
  };
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);

  const initializedAt = new Date().toISOString();
  const state = await loadState(statePath) ?? { initializedAt, sessions: [] };
  const tracked = new Map(state.sessions.map((session) => [session.sessionId, session]));
  const traceCollector = inboxUrl
    ? new OtlpInboxSessionCollector(
        inboxUrl,
        config.benchmark.phaseToolPatterns,
        state.trace,
        config.benchmark.maxIdleGapSeconds,
      )
    : new IncrementalTraceSessionCollector(
        config.traceArchivePath,
        config.benchmark.phaseToolPatterns,
        state.trace?.version === 1 ? state.trace : undefined,
        config.benchmark.maxIdleGapSeconds,
      );
  const directLogCheckedAt = new Map<string, number>();
  console.log(JSON.stringify({
    event: 'session_measurement_started',
    pollSeconds,
    mode: inboxUrl ? 'transactional_otlp_inbox' : 'legacy_trace_archive',
    initializedAt: state.initializedAt,
    resumedSessions: tracked.size
  }));

  const pendingMigrationSessionIds = new Set(
    [...tracked.values()]
      .filter((session) =>
        session.calculationVersion !== SESSION_CALCULATION_VERSION
        || session.publishedModelConfigurationSignature !== currentModelConfigurationSignature
      )
      .map((session) => session.sessionId)
  );

  const migratePendingSessions = async (): Promise<number> => {
    if (pendingMigrationSessionIds.size === 0) return 0;
    const migrationEvidenceBySessionId = new Map<string, TraceSessionEvidence>(
      traceCollector.evidence(pendingMigrationSessionIds).map((entry) => [
        entry.session.sessionId, entry,
      ])
    );

    let migrated = 0;
    for (const sessionId of [...pendingMigrationSessionIds]) {
      const trackedSession = tracked.get(sessionId);
      if (!trackedSession) {
        pendingMigrationSessionIds.delete(sessionId);
        continue;
      }
      try {
        const artifact = JSON.parse(await readFile(
          path.join(outputDirectory, `${trackedSession.experiment}.json`),
          'utf8'
        )) as { completedAt?: unknown; usage?: unknown };
        if (typeof artifact.completedAt !== 'string' || !artifact.usage ||
          typeof artifact.usage !== 'object') {
          continue;
        }
        const refreshed = migrationEvidenceBySessionId.get(sessionId);
        let usage = migrationUsageFromRefreshedEvidence(
          artifact.usage as ExperimentUsage,
          refreshed,
          trackedSession.startedAt,
          artifact.completedAt,
        );
        if (!usage || !refreshed) {
          console.error(JSON.stringify({
            event: 'session_migration_skipped',
            experiment: trackedSession.experiment,
            reason: 'retained_phase_evidence_unavailable'
          }));
          continue;
        }
        const { candidate: migrationSession } = sessionStateForObservedBounds(
          trackedSession,
          refreshed.session.startedAt,
          refreshed.session.endedAt
        );
        delete migrationSession.directLogSignature;
        delete migrationSession.directUsage;
        const migrationDirectLogSignature = chatLogRoot && refreshed.session.chatSessionIds?.length
          ? await directTurnLogSignature(chatLogRoot, refreshed.session.chatSessionIds)
          : null;
        const migrationDirectUsage = chatLogRoot && migrationDirectLogSignature &&
          refreshed.session.chatSessionIds?.length
          ? await collectDirectTurnUsage(
            chatLogRoot,
            refreshed.session.chatSessionIds,
            new Date(refreshed.session.startedAt),
            new Date(refreshed.session.endedAt),
            refreshed.usage
          ) ?? undefined
          : undefined;
        if (migrationDirectUsage) {
          usage = canonicalizeUsageCosts(migrationDirectUsage);
          migrationSession.directLogSignature = migrationDirectLogSignature;
          migrationSession.directUsage = usage;
        }
        const result = await publishAndCommitSession(
          tracked,
          sessionId,
          migrationSession,
          (candidate) => publishTrackedSession(
            config,
            outputDirectory,
            candidate,
            refreshed.session.endedAt,
            usage,
            traceCollector.inboxCursor
          )
        );
        pendingMigrationSessionIds.delete(sessionId);
        migrated += 1;
        console.log(JSON.stringify({
          event: 'session_migrated',
          experiment: trackedSession.experiment,
          calculationVersion: SESSION_CALCULATION_VERSION,
          phaseEvidence: 'rederived',
          sourceEvidence: result.source.source
        }));
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
          console.error(JSON.stringify({
            event: 'session_migration_failed',
            experiment: trackedSession.experiment,
            error: error instanceof Error ? error.message : String(error)
          }));
        }
      }
    }
    return migrated;
  };

  await migratePendingSessions();
  state.sessions = [...tracked.values()];
  await saveState(statePath, state);

  while (!stopped) {
    await sleep(pollSeconds * 1_000, (nextWake) => { wake = nextWake; });
    wake = undefined;
    if (stopped) break;

    const capturedAt = new Date().toISOString();

    try {
      if (!inboxUrl) await access(config.traceArchivePath);
      const scan = await traceCollector.scan(false);
      const discovered = scan.sessions;
      let stateChanged = scan.stateChanged;
      if (scan.recordsRead > 0) {
        console.log(JSON.stringify({
          event: 'session_trace_increment_ingested',
          recordsRead: scan.recordsRead,
          bytesRead: scan.bytesRead,
          changedSessions: scan.changedSessionIds.length,
          latestTimestamp: scan.latestTimestamp ?? null
        }));
      }
      for (const session of discovered) {
        if (tracked.has(session.sessionId)) continue;
        const trackedSession: TrackedSession = {
          sessionId: session.sessionId,
          experiment: sessionExperimentName(session.sessionId, session.startedAt),
          startedAt: session.startedAt
        };
        tracked.set(session.sessionId, trackedSession);
        stateChanged = true;
        console.log(JSON.stringify({
          event: 'session_discovered', experiment: trackedSession.experiment, startedAt: session.startedAt
        }));
      }
      if (!scan.caughtUp) {
        state.sessions = [...tracked.values()];
        state.trace = traceCollector.serialize();
        await saveState(statePath, state);
        continue;
      }

      const migratedSessions = await migratePendingSessions();
      stateChanged ||= migratedSessions > 0;

      const candidateSessionIds = new Set(scan.changedSessionIds);
      const observedDirectLogSignatures = new Map<string, string | null>();
      if (chatLogRoot) {
        const capturedMilliseconds = Date.parse(capturedAt);
        const latestMilliseconds = scan.latestTimestamp
          ? Date.parse(scan.latestTimestamp)
          : 0;
        for (const session of discovered) {
          if (!session.chatSessionIds?.length) continue;
          const changed = candidateSessionIds.has(session.sessionId);
          const recentlyEnded = latestMilliseconds - Date.parse(session.endedAt) <=
            DIRECT_LOG_SETTLE_MILLISECONDS;
          const checkDue = capturedMilliseconds - (directLogCheckedAt.get(session.sessionId) ?? 0) >=
            DIRECT_LOG_RECHECK_MILLISECONDS;
          if (!changed && (!recentlyEnded || !checkDue)) continue;
          directLogCheckedAt.set(session.sessionId, capturedMilliseconds);
          const signature = await directTurnLogSignature(chatLogRoot, session.chatSessionIds);
          observedDirectLogSignatures.set(session.sessionId, signature);
          const trackedSession = tracked.get(session.sessionId);
          if (signature !== null && signature !== trackedSession?.directLogSignature) {
            candidateSessionIds.add(session.sessionId);
          }
        }
      }

      const evidence = traceCollector.evidence(candidateSessionIds);
      for (const entry of evidence) {
        const session = entry.session;
        const trackedSession = tracked.get(session.sessionId);
        if (!trackedSession) continue;
        if (pendingMigrationSessionIds.has(session.sessionId)) continue;
        if (sessionCoverageRegressed(trackedSession.startedAt, session.startedAt)) {
          console.error(JSON.stringify({
            event: 'session_evaluation_skipped',
            experiment: trackedSession.experiment,
            reason: 'retained_evidence_starts_after_tracked_session',
            trackedStartedAt: trackedSession.startedAt,
            observedStartedAt: session.startedAt
          }));
          continue;
        }
        const { candidate: publicationSession, boundsExpanded: sessionBoundsExpanded } =
          sessionStateForObservedBounds(trackedSession, session.startedAt, session.endedAt);
        const directLogSignature = observedDirectLogSignatures.get(session.sessionId);
        const directUsage = chatLogRoot && directLogSignature && session.chatSessionIds?.length
          ? directLogSignature === publicationSession.directLogSignature && publicationSession.directUsage
            ? reuseDirectTurnUsage(
              canonicalizeUsageCosts(publicationSession.directUsage),
              entry.usage
            )
            : await collectDirectTurnUsage(
              chatLogRoot,
              session.chatSessionIds,
              new Date(session.startedAt),
              new Date(session.endedAt),
              entry.usage
            ) ?? undefined
          : undefined;
        const sessionUsage = canonicalizeUsageCosts(directUsage ?? entry.usage);
        const currentUsageSignature = usageSignature(sessionUsage);
        if (!sessionNeedsEvaluation(
          trackedSession.publishedThrough,
          trackedSession.calculationVersion,
          session.endedAt,
          trackedSession.publishedUsageSignature,
          currentUsageSignature,
          publicationSession.publishedModelConfigurationSignature,
          currentModelConfigurationSignature,
          sessionBoundsExpanded
        )) {
          if (directLogSignature !== undefined &&
            (trackedSession.directLogSignature !== directLogSignature ||
              Boolean(directUsage && !trackedSession.directUsage))) {
            trackedSession.directLogSignature = directLogSignature;
            if (directUsage) trackedSession.directUsage = directUsage;
            else delete trackedSession.directUsage;
            stateChanged = true;
          }
          continue;
        }

        console.log(JSON.stringify({
          event: 'session_evaluation_started',
          experiment: publicationSession.experiment,
          startedAt: publicationSession.startedAt,
          completedAt: session.endedAt
        }));
        const result = await publishAndCommitSession(
          tracked,
          session.sessionId,
          publicationSession,
          (candidate) => publishTrackedSession(
            config,
            outputDirectory,
            candidate,
            session.endedAt,
            sessionUsage,
            traceCollector.inboxCursor
          )
        );
        pendingMigrationSessionIds.delete(session.sessionId);
        if (directLogSignature !== undefined) {
          publicationSession.directLogSignature = directLogSignature;
          if (directUsage) publicationSession.directUsage = directUsage;
          else delete publicationSession.directUsage;
        }
        stateChanged = true;
        console.log(JSON.stringify({
          event: 'session_published',
          experiment: publicationSession.experiment,
          startedAt: publicationSession.startedAt,
          completedAt: session.endedAt,
          aiCostUsd: result.usage.aiCostUsd,
          sourceEvidence: result.source.source,
          baseRoi: result.benchmark?.scenarios.base.roi ?? null
        }));
      }
      for (const session of discovered) {
        const trackedSession = tracked.get(session.sessionId);
        if (!trackedSession || trackedSession.directLogSignature !== undefined ||
          observedDirectLogSignatures.get(session.sessionId) !== null) continue;
        trackedSession.directLogSignature = null;
        stateChanged = true;
      }
      if (stateChanged) {
        state.sessions = [...tracked.values()];
        state.trace = traceCollector.serialize();
        await saveState(statePath, state);
        traceCollector.commit();
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
        console.log(JSON.stringify({ event: 'session_scan_skipped', reason: 'no_trace_archive', capturedAt }));
      } else {
        console.error(JSON.stringify({
          event: 'session_scan_failed',
          capturedAt,
          error: error instanceof Error ? error.message : String(error)
        }));
      }
    }
  }
  state.sessions = [...tracked.values()];
  await saveState(statePath, state);
  console.log(JSON.stringify({ event: 'session_measurement_stopped' }));
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});