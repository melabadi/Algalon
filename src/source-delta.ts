import { createHash } from 'node:crypto';
import { readdir, readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { diffChars, diffLines } from 'diff';

export interface SourceSnapshot {
  capturedAt: string;
  roots: string[];
  files: Record<string, string>;
}

export interface SourceDelta {
  filesBefore: number;
  filesAfter: number;
  filesAdded: number;
  filesModified: number;
  filesDeleted: number;
  filesRenamed: number;
  filesUnchanged: number;
  charactersBefore: number;
  charactersAfter: number;
  charactersAdded: number;
  charactersRemoved: number;
  linesAdded: number;
  linesRemoved: number;
}

export interface OtelSourceDelta extends SourceDelta {
  source: 'otel_source_delta' | 'otel_only';
}

interface OtlpAttribute {
  key: string;
  value: { stringValue: string };
}

const sourceExtensions = new Set(['.css', '.html', '.js', '.jsx', '.json', '.svg', '.ts', '.tsx']);
const excludedDirectories = new Set(['.copilot-value', '.git', 'coverage', 'dist', 'node_modules']);
const excludedFiles = new Set(['package-lock.json', 'pnpm-lock.yaml', 'yarn.lock']);

function hash(content: string): string {
  return createHash('sha256').update(content).digest('hex');
}

function lineCount(content: string): number {
  if (!content) return 0;
  const lines = content.split(/\r?\n/).length;
  return content.endsWith('\n') ? lines - 1 : lines;
}

async function sourceFiles(targetPath: string): Promise<string[]> {
  const targetStat = await stat(targetPath);
  if (targetStat.isFile()) {
    return sourceExtensions.has(path.extname(targetPath).toLowerCase()) &&
      !excludedFiles.has(path.basename(targetPath))
      ? [targetPath]
      : [];
  }
  if (!targetStat.isDirectory()) return [];

  const files: string[] = [];
  for (const entry of await readdir(targetPath, { withFileTypes: true })) {
    if (entry.isSymbolicLink() || excludedDirectories.has(entry.name) || excludedFiles.has(entry.name)) {
      continue;
    }
    files.push(...await sourceFiles(path.join(targetPath, entry.name)));
  }
  return files;
}

export async function captureSourceSnapshot(
  sourcePaths: string[],
  capturedAt = new Date().toISOString()
): Promise<SourceSnapshot> {
  if (sourcePaths.length === 0) {
    throw new Error('At least one source path is required for a source snapshot.');
  }
  const roots = sourcePaths.map((sourcePath) => path.resolve(sourcePath));
  const files: Record<string, string> = {};
  for (let rootIndex = 0; rootIndex < roots.length; rootIndex += 1) {
    const root = roots[rootIndex] as string;
    for (const filePath of await sourceFiles(root)) {
      const rootStat = await stat(root);
      const relativePath = rootStat.isFile()
        ? path.basename(root)
        : path.relative(root, filePath).replace(/\\/g, '/');
      files[`${rootIndex}:${relativePath}`] = await readFile(filePath, 'utf8');
    }
  }
  return { capturedAt, roots, files };
}

export function parseSourceSnapshot(input: unknown): SourceSnapshot {
  if (!input || typeof input !== 'object') {
    throw new Error('Source baseline is not a JSON object.');
  }
  const candidate = input as Partial<SourceSnapshot>;
  if (typeof candidate.capturedAt !== 'string' || !Number.isFinite(Date.parse(candidate.capturedAt))) {
    throw new Error('Source baseline has an invalid capture timestamp.');
  }
  if (!Array.isArray(candidate.roots) || candidate.roots.length === 0 ||
    candidate.roots.some((root) => typeof root !== 'string' || root.length === 0)) {
    throw new Error('Source baseline has no valid source roots.');
  }
  if (!candidate.files || typeof candidate.files !== 'object' || Array.isArray(candidate.files) ||
    Object.values(candidate.files).some((content) => typeof content !== 'string')) {
    throw new Error('Source baseline has invalid file evidence.');
  }
  return candidate as SourceSnapshot;
}

interface RenamePair {
  beforePath: string;
  afterPath: string;
  modified: boolean;
}

function contentSimilarity(before: string, after: string): number {
  if (before.length === 0 && after.length === 0) return 1;
  const commonCharacters = diffChars(before, after).reduce(
    (total, change) => total + (!change.added && !change.removed ? change.value.length : 0),
    0
  );
  return 2 * commonCharacters / (before.length + after.length);
}

function pairRenames(
  before: Record<string, string>,
  after: Record<string, string>,
  deletedPaths: Set<string>,
  addedPaths: Set<string>
): RenamePair[] {
  const candidates = [...deletedPaths].flatMap((beforePath) => [...addedPaths]
    .filter((afterPath) => path.extname(beforePath) === path.extname(afterPath))
    .map((afterPath) => {
      const beforeContent = before[beforePath] ?? '';
      const afterContent = after[afterPath] ?? '';
      const exact = hash(beforeContent) === hash(afterContent);
      return {
        beforePath,
        afterPath,
        modified: !exact,
        similarity: exact ? 1 : contentSimilarity(beforeContent, afterContent)
      };
    })
    .filter(({ similarity }) => similarity >= 0.5));
  candidates.sort((left, right) => right.similarity - left.similarity ||
    left.beforePath.localeCompare(right.beforePath) || left.afterPath.localeCompare(right.afterPath));

  const pairs: RenamePair[] = [];
  for (const candidate of candidates) {
    if (!deletedPaths.has(candidate.beforePath) || !addedPaths.has(candidate.afterPath)) continue;
    deletedPaths.delete(candidate.beforePath);
    addedPaths.delete(candidate.afterPath);
    pairs.push(candidate);
  }
  return pairs;
}

export function calculateSourceDelta(before: SourceSnapshot, after: SourceSnapshot): SourceDelta {
  const beforePaths = new Set(Object.keys(before.files));
  const afterPaths = new Set(Object.keys(after.files));
  const commonPaths = [...beforePaths].filter((filePath) => afterPaths.has(filePath));
  const addedPaths = new Set([...afterPaths].filter((filePath) => !beforePaths.has(filePath)));
  const deletedPaths = new Set([...beforePaths].filter((filePath) => !afterPaths.has(filePath)));
  const renamedFiles = pairRenames(before.files, after.files, deletedPaths, addedPaths);
  const modifiedPaths = commonPaths.filter((filePath) => before.files[filePath] !== after.files[filePath]);
  const filesUnchanged = commonPaths.length - modifiedPaths.length;

  let charactersAdded = [...addedPaths].reduce((total, filePath) => total + (after.files[filePath]?.length ?? 0), 0);
  let charactersRemoved = [...deletedPaths].reduce((total, filePath) => total + (before.files[filePath]?.length ?? 0), 0);
  let linesAdded = [...addedPaths].reduce((total, filePath) => total + lineCount(after.files[filePath] ?? ''), 0);
  let linesRemoved = [...deletedPaths].reduce((total, filePath) => total + lineCount(before.files[filePath] ?? ''), 0);

  const modifiedFiles = [
    ...modifiedPaths.map((filePath) => ({ beforePath: filePath, afterPath: filePath })),
    ...renamedFiles.filter(({ modified }) => modified)
  ];
  for (const { beforePath, afterPath } of modifiedFiles) {
    const beforeContent = before.files[beforePath] ?? '';
    const afterContent = after.files[afterPath] ?? '';
    for (const change of diffChars(beforeContent, afterContent)) {
      if (change.added) {
        charactersAdded += change.value.length;
      } else if (change.removed) {
        charactersRemoved += change.value.length;
      }
    }
    for (const change of diffLines(beforeContent, afterContent)) {
      if (change.added) {
        linesAdded += lineCount(change.value);
      } else if (change.removed) {
        linesRemoved += lineCount(change.value);
      }
    }
  }

  return {
    filesBefore: beforePaths.size,
    filesAfter: afterPaths.size,
    filesAdded: addedPaths.size,
    filesModified: modifiedFiles.length,
    filesDeleted: deletedPaths.size,
    filesRenamed: renamedFiles.length,
    filesUnchanged,
    charactersBefore: Object.values(before.files).reduce((total, content) => total + content.length, 0),
    charactersAfter: Object.values(after.files).reduce((total, content) => total + content.length, 0),
    charactersAdded,
    charactersRemoved,
    linesAdded,
    linesRemoved
  };
}

function attribute(key: string, value: string): OtlpAttribute {
  return { key, value: { stringValue: value } };
}

function gauge(name: string, value: number, attributes: OtlpAttribute[], timeUnixNano: string) {
  return {
    name,
    gauge: { dataPoints: [{ asDouble: value, timeUnixNano, attributes }] }
  };
}

export function buildSourceDeltaOtlpPayload(
  experiment: string,
  startedAt: string,
  completedAt: string,
  delta: SourceDelta
) {
  const timeUnixNano = (BigInt(Date.parse(completedAt)) * 1_000_000n).toString();
  const labels = [attribute('experiment', experiment), attribute('evidence', 'observed_local_diff')];
  const metrics = [
    gauge('copilot_value_experiment_source_delta_info', 1, labels, timeUnixNano),
    gauge('copilot_value_experiment_source_delta_characters', delta.charactersAdded, [...labels, attribute('change_type', 'added')], timeUnixNano),
    gauge('copilot_value_experiment_source_delta_characters', delta.charactersRemoved, [...labels, attribute('change_type', 'removed')], timeUnixNano),
    gauge('copilot_value_experiment_source_delta_lines', delta.linesAdded, [...labels, attribute('change_type', 'added')], timeUnixNano),
    gauge('copilot_value_experiment_source_delta_lines', delta.linesRemoved, [...labels, attribute('change_type', 'removed')], timeUnixNano),
    gauge('copilot_value_experiment_source_snapshot_characters', delta.charactersBefore, [...labels, attribute('state', 'before')], timeUnixNano),
    gauge('copilot_value_experiment_source_snapshot_characters', delta.charactersAfter, [...labels, attribute('state', 'after')], timeUnixNano),
    gauge('copilot_value_experiment_source_snapshot_files', delta.filesBefore, [...labels, attribute('state', 'before')], timeUnixNano),
    gauge('copilot_value_experiment_source_snapshot_files', delta.filesAfter, [...labels, attribute('state', 'after')], timeUnixNano),
    ...([
      ['added', delta.filesAdded],
      ['modified', delta.filesModified],
      ['deleted', delta.filesDeleted],
      ['renamed', delta.filesRenamed],
      ['unchanged', delta.filesUnchanged]
    ] as const).map(([changeType, value]) => gauge(
      'copilot_value_experiment_source_delta_files',
      value,
      [...labels, attribute('change_type', changeType)],
      timeUnixNano
    )),
    gauge('copilot_value_experiment_source_start_timestamp_seconds', Date.parse(startedAt) / 1_000, labels, timeUnixNano),
    gauge('copilot_value_experiment_source_completion_timestamp_seconds', Date.parse(completedAt) / 1_000, labels, timeUnixNano)
  ];
  return {
    resourceMetrics: [{
      resource: { attributes: [attribute('service.name', 'copilot-value-source-evidence')] },
      scopeMetrics: [{
        scope: { name: 'copilot-value-source-evidence', version: '1.0.0' },
        metrics
      }]
    }]
  };
}

async function queryVictoriaMetrics(victoriaMetricsUrl: string, expression: string) {
  const endpoint = new URL('/prometheus/api/v1/query', victoriaMetricsUrl);
  endpoint.searchParams.set('query', expression);
  const response = await fetch(endpoint, { signal: AbortSignal.timeout(10_000) });
  if (!response.ok) {
    throw new Error(`VictoriaMetrics source-evidence query failed (${response.status}).`);
  }
  const payload = await response.json() as {
    status: string;
    data?: { result?: Array<{ metric: Record<string, string>; value: [number, string] }> };
  };
  if (payload.status !== 'success') {
    throw new Error('VictoriaMetrics source-evidence query did not succeed.');
  }
  return payload.data?.result ?? [];
}

function experimentSelector(metricName: string, experiment: string): string {
  const escaped = experiment.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  return `last_over_time(${metricName}{experiment="${escaped}"}[90d])`;
}

async function metricSeries(victoriaMetricsUrl: string, metricName: string, experiment: string) {
  return queryVictoriaMetrics(victoriaMetricsUrl, experimentSelector(metricName, experiment));
}

function valueByLabel(
  results: Array<{ metric: Record<string, string>; value: [number, string] }>,
  label: string,
  value: string
): number {
  const candidate = results.find((result) => result.metric[label] === value)?.value[1];
  const number = Number(candidate ?? 0);
  return Number.isFinite(number) ? number : 0;
}

function requireLabelValues(
  results: Array<{ metric: Record<string, string>; value: [number, string] }>,
  metricName: string,
  label: string,
  expectedValues: string[]
): void {
  const missing = expectedValues.filter((value) => !results.some((result) => result.metric[label] === value));
  if (missing.length > 0) {
    throw new Error(`OTel source evidence metric '${metricName}' is incomplete (${label}: ${missing.join(', ')}).`);
  }
}

function hasTimestamp(
  results: Array<{ metric: Record<string, string>; value: [number, string] }>,
  expectedTimestamp: string
): boolean {
  const expected = Date.parse(expectedTimestamp) / 1_000;
  return results.some((result) => Math.abs(Number(result.value[1]) - expected) < 0.001);
}

export async function collectOtelSourceDelta(
  victoriaMetricsUrl: string,
  experiment: string,
  expectedWindow?: { startedAt: string; completedAt: string }
): Promise<OtelSourceDelta> {
  const [infoSeries, characterSeries, lineSeries, fileSeries, snapshotCharacters, snapshotFiles, startSeries, completionSeries] = await Promise.all([
    metricSeries(victoriaMetricsUrl, 'copilot_value_experiment_source_delta_info', experiment),
    metricSeries(victoriaMetricsUrl, 'copilot_value_experiment_source_delta_characters', experiment),
    metricSeries(victoriaMetricsUrl, 'copilot_value_experiment_source_delta_lines', experiment),
    metricSeries(victoriaMetricsUrl, 'copilot_value_experiment_source_delta_files', experiment),
    metricSeries(victoriaMetricsUrl, 'copilot_value_experiment_source_snapshot_characters', experiment),
    metricSeries(victoriaMetricsUrl, 'copilot_value_experiment_source_snapshot_files', experiment),
    metricSeries(victoriaMetricsUrl, 'copilot_value_experiment_source_start_timestamp_seconds', experiment),
    metricSeries(victoriaMetricsUrl, 'copilot_value_experiment_source_completion_timestamp_seconds', experiment)
  ]);
  if (infoSeries.length === 0 || startSeries.length === 0 || completionSeries.length === 0) {
    throw new Error(`No OTel source delta found for experiment '${experiment}'. Start it with -SourcePath.`);
  }
  requireLabelValues(characterSeries, 'source_delta_characters', 'change_type', ['added', 'removed']);
  requireLabelValues(lineSeries, 'source_delta_lines', 'change_type', ['added', 'removed']);
  requireLabelValues(fileSeries, 'source_delta_files', 'change_type', ['added', 'modified', 'deleted', 'renamed', 'unchanged']);
  requireLabelValues(snapshotCharacters, 'source_snapshot_characters', 'state', ['before', 'after']);
  requireLabelValues(snapshotFiles, 'source_snapshot_files', 'state', ['before', 'after']);
  if (expectedWindow && (!hasTimestamp(startSeries, expectedWindow.startedAt) ||
    !hasTimestamp(completionSeries, expectedWindow.completedAt))) {
    throw new Error(`OTel source delta for experiment '${experiment}' does not match its current run window.`);
  }
  const delta: OtelSourceDelta = {
    source: 'otel_source_delta',
    filesBefore: valueByLabel(snapshotFiles, 'state', 'before'),
    filesAfter: valueByLabel(snapshotFiles, 'state', 'after'),
    filesAdded: valueByLabel(fileSeries, 'change_type', 'added'),
    filesModified: valueByLabel(fileSeries, 'change_type', 'modified'),
    filesDeleted: valueByLabel(fileSeries, 'change_type', 'deleted'),
    filesRenamed: valueByLabel(fileSeries, 'change_type', 'renamed'),
    filesUnchanged: valueByLabel(fileSeries, 'change_type', 'unchanged'),
    charactersBefore: valueByLabel(snapshotCharacters, 'state', 'before'),
    charactersAfter: valueByLabel(snapshotCharacters, 'state', 'after'),
    charactersAdded: valueByLabel(characterSeries, 'change_type', 'added'),
    charactersRemoved: valueByLabel(characterSeries, 'change_type', 'removed'),
    linesAdded: valueByLabel(lineSeries, 'change_type', 'added'),
    linesRemoved: valueByLabel(lineSeries, 'change_type', 'removed')
  };
  if (Object.entries(delta).some(([key, value]) => key !== 'source' &&
    (typeof value !== 'number' || !Number.isFinite(value) || value < 0))) {
    throw new Error(`OTel source delta for experiment '${experiment}' contains invalid values.`);
  }
  return delta;
}

export async function exportSourceDeltaToOtel(
  collectorEndpoint: string,
  victoriaMetricsUrl: string,
  experiment: string,
  startedAt: string,
  completedAt: string,
  delta: SourceDelta
): Promise<void> {
  await sendSourceDeltaToOtel(collectorEndpoint, experiment, startedAt, completedAt, delta);

  const expectedCompletion = Date.parse(completedAt) / 1_000;
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const results = await metricSeries(
      victoriaMetricsUrl,
      'copilot_value_experiment_source_completion_timestamp_seconds',
      experiment
    );
    if (results.some((result) => Math.abs(Number(result.value[1]) - expectedCompletion) < 0.001)) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out waiting for OTel source delta '${experiment}' in VictoriaMetrics.`);
}

export async function sendSourceDeltaToOtel(
  collectorEndpoint: string,
  experiment: string,
  startedAt: string,
  completedAt: string,
  delta: SourceDelta
): Promise<void> {
  const response = await fetch(`${collectorEndpoint.replace(/\/$/, '')}/v1/metrics`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(buildSourceDeltaOtlpPayload(experiment, startedAt, completedAt, delta)),
    signal: AbortSignal.timeout(10_000)
  });
  if (!response.ok) {
    throw new Error(`Source-delta OTLP export failed (${response.status}): ${(await response.text()).slice(0, 300)}`);
  }
}