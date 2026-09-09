import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { valueConfigSchema } from './schema.js';
import {
  calculateSourceDelta,
  captureSourceSnapshot,
  exportSourceDeltaToOtel,
  parseSourceSnapshot
} from './source-delta.js';

function optionValue(arguments_: string[], name: string): string | undefined {
  const index = arguments_.indexOf(name);
  return index >= 0 ? arguments_[index + 1] : undefined;
}

function optionValues(arguments_: string[], name: string): string[] {
  return arguments_.flatMap((argument, index) => argument === name && arguments_[index + 1]
    ? [arguments_[index + 1] as string]
    : []);
}

function requiredOption(arguments_: string[], name: string): string {
  const value = optionValue(arguments_, name);
  if (!value) throw new Error(`Missing required option '${name}'.`);
  return value;
}

async function snapshotCommand(arguments_: string[]): Promise<void> {
  const outputPath = requiredOption(arguments_, '--output');
  const capturedAt = optionValue(arguments_, '--captured-at') ?? new Date().toISOString();
  if (!Number.isFinite(Date.parse(capturedAt))) throw new Error(`Invalid capture timestamp '${capturedAt}'.`);
  const snapshot = await captureSourceSnapshot(optionValues(arguments_, '--source-path'), capturedAt);
  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(snapshot)}\n`, 'utf8');
  console.log(JSON.stringify({
    capturedAt: snapshot.capturedAt,
    files: Object.keys(snapshot.files).length,
    characters: Object.values(snapshot.files).reduce((total, content) => total + content.length, 0)
  }));
}

async function completeCommand(arguments_: string[]): Promise<void> {
  const experiment = requiredOption(arguments_, '--experiment');
  const snapshotPath = requiredOption(arguments_, '--snapshot');
  const completedAt = requiredOption(arguments_, '--completed-at');
  if (!Number.isFinite(Date.parse(completedAt))) throw new Error(`Invalid completion timestamp '${completedAt}'.`);
  const baseline = parseSourceSnapshot(JSON.parse(await readFile(snapshotPath, 'utf8')));
  if (Date.parse(completedAt) < Date.parse(baseline.capturedAt)) {
    throw new Error('Experiment completion precedes its source baseline.');
  }
  const configPath = optionValue(arguments_, '--config') ?? 'config/value-model.local.json';
  const config = valueConfigSchema.parse(JSON.parse(await readFile(configPath, 'utf8')));
  const current = await captureSourceSnapshot(baseline.roots, completedAt);
  const delta = calculateSourceDelta(baseline, current);
  await exportSourceDeltaToOtel(
    config.otelHttpEndpoint,
    config.victoriaMetricsUrl,
    experiment,
    baseline.capturedAt,
    completedAt,
    delta
  );
  console.log(JSON.stringify({ experiment, source: 'otel_source_delta', ...delta }));
}

async function diffCommand(arguments_: string[]): Promise<void> {
  const snapshotPath = requiredOption(arguments_, '--snapshot');
  const capturedAt = optionValue(arguments_, '--captured-at') ?? new Date().toISOString();
  if (!Number.isFinite(Date.parse(capturedAt))) throw new Error(`Invalid capture timestamp '${capturedAt}'.`);
  const baseline = parseSourceSnapshot(JSON.parse(await readFile(snapshotPath, 'utf8')));
  const current = await captureSourceSnapshot(baseline.roots, capturedAt);
  console.log(JSON.stringify({ source: 'local_source_delta', ...calculateSourceDelta(baseline, current) }));
}

async function main(): Promise<void> {
  const [command, ...arguments_] = process.argv.slice(2);
  if (command === 'snapshot') {
    await snapshotCommand(arguments_);
  } else if (command === 'diff') {
    await diffCommand(arguments_);
  } else if (command === 'complete') {
    await completeCommand(arguments_);
  } else {
    throw new Error('Supported source-evidence commands: snapshot, diff, complete.');
  }
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});