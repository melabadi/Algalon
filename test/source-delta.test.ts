import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  buildSourceDeltaOtlpPayload,
  calculateSourceDelta,
  captureSourceSnapshot,
  collectOtelSourceDelta,
  exportSourceDeltaToOtel,
  type SourceSnapshot
} from '../src/source-delta.js';

test('tracks retained source delta without crediting unchanged renames', () => {
  const before: SourceSnapshot = {
    capturedAt: '2026-08-04T10:00:00Z',
    roots: ['fixture'],
    files: {
      '0:src/a.ts': 'const a = 1;\nkeep\n',
      '0:src/renamed-old.ts': 'same\n',
      '0:src/delete.ts': 'remove\n'
    }
  };
  const after: SourceSnapshot = {
    capturedAt: '2026-08-04T10:05:00Z',
    roots: ['fixture'],
    files: {
      '0:src/a.ts': 'const a = 2;\nkeep\nnew line\n',
      '0:src/renamed-new.ts': 'same\n',
      '0:src/add.css': 'body {}\n'
    }
  };

  const delta = calculateSourceDelta(before, after);
  assert.deepEqual(delta, {
    filesBefore: 3,
    filesAfter: 3,
    filesAdded: 1,
    filesModified: 1,
    filesDeleted: 1,
    filesRenamed: 1,
    filesUnchanged: 0,
    charactersBefore: 30,
    charactersAfter: 40,
    charactersAdded: 18,
    charactersRemoved: 8,
    linesAdded: 3,
    linesRemoved: 2
  });
});

test('builds aggregate-only OTel source evidence without paths or source content', () => {
  const delta = {
    filesBefore: 1,
    filesAfter: 1,
    filesAdded: 0,
    filesModified: 1,
    filesDeleted: 0,
    filesRenamed: 0,
    filesUnchanged: 0,
    charactersBefore: 12,
    charactersAfter: 20,
    charactersAdded: 8,
    charactersRemoved: 0,
    linesAdded: 1,
    linesRemoved: 0
  };
  const payload = JSON.stringify(buildSourceDeltaOtlpPayload(
    'delta-test',
    '2026-08-04T10:00:00Z',
    '2026-08-04T10:05:00Z',
    delta
  ));

  assert.match(payload, /copilot_value_experiment_source_delta_characters/);
  assert.match(payload, /delta-test/);
  assert.doesNotMatch(payload, /src\/|const a|file_path|source_content/i);
});

test('credits only the edit when a similar file is renamed and modified', () => {
  const before: SourceSnapshot = {
    capturedAt: '2026-08-04T10:00:00Z',
    roots: ['fixture'],
    files: { '0:src/before.ts': 'alpha\nbeta\n' }
  };
  const after: SourceSnapshot = {
    capturedAt: '2026-08-04T10:05:00Z',
    roots: ['fixture'],
    files: { '0:src/after.ts': 'alpha\nbeta!\n' }
  };

  const delta = calculateSourceDelta(before, after);
  assert.equal(delta.filesRenamed, 1);
  assert.equal(delta.filesModified, 1);
  assert.equal(delta.filesAdded, 0);
  assert.equal(delta.filesDeleted, 0);
  assert.equal(delta.charactersAdded, 1);
  assert.equal(delta.charactersRemoved, 0);
});

test('excludes a repo-root .copilot-value installation from source evidence', async () => {
  const repository = await mkdtemp(path.join(os.tmpdir(), 'copilot-value-repo-root-'));
  try {
    await mkdir(path.join(repository, 'src'));
    await mkdir(path.join(repository, '.copilot-value', 'config'), { recursive: true });
    await writeFile(path.join(repository, 'src', 'app.ts'), 'export const value = 1;\n');
    await writeFile(path.join(repository, '.copilot-value', 'config', 'runtime.json'), '{"state":1}\n');
    const before = await captureSourceSnapshot([repository], '2026-08-05T10:00:00Z');

    await writeFile(path.join(repository, 'src', 'app.ts'), 'export const value = 2;\n');
    await writeFile(path.join(repository, '.copilot-value', 'config', 'runtime.json'), '{"state":2,"noise":true}\n');
    const after = await captureSourceSnapshot([repository], '2026-08-05T10:05:00Z');
    const delta = calculateSourceDelta(before, after);

    assert.deepEqual(Object.keys(before.files), ['0:src/app.ts']);
    assert.equal(delta.filesModified, 1);
    assert.equal(delta.filesAdded, 0);
    assert.equal(delta.filesDeleted, 0);
    assert.equal(delta.charactersAdded, 1);
    assert.equal(delta.charactersRemoved, 1);
  } finally {
    await rm(repository, { recursive: true, force: true });
  }
});

test('fails closed when OTel source evidence is incomplete', async (context) => {
  const server = createServer((request, response) => {
    const query = new URL(request.url ?? '/', 'http://127.0.0.1').searchParams.get('query') ?? '';
    const present = query.includes('_info') || query.includes('_start_timestamp_') ||
      query.includes('_completion_timestamp_');
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({
      status: 'success',
      data: {
        result: present
          ? [{ metric: { experiment: 'incomplete' }, value: [0, '1'] }]
          : []
      }
    }));
  });
  server.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.once('listening', resolve));
  context.after(() => new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve())));
  const address = server.address();
  assert.ok(address && typeof address === 'object');

  await assert.rejects(
    collectOtelSourceDelta(`http://127.0.0.1:${address.port}`, 'incomplete'),
    /source_delta_characters.*incomplete/
  );
});

test('reads complete source evidence for the matching experiment window', async (context) => {
  const startedAt = '2026-08-05T10:00:00Z';
  const completedAt = '2026-08-05T10:05:00Z';
  const resultByMetric: Record<string, Array<{ metric: Record<string, string>; value: [number, string] }>> = {
    copilot_value_experiment_source_delta_info: [{ metric: { experiment: 'complete' }, value: [0, '1'] }],
    copilot_value_experiment_source_delta_characters: [
      { metric: { change_type: 'added' }, value: [0, '32'] },
      { metric: { change_type: 'removed' }, value: [0, '1'] }
    ],
    copilot_value_experiment_source_delta_lines: [
      { metric: { change_type: 'added' }, value: [0, '2'] },
      { metric: { change_type: 'removed' }, value: [0, '1'] }
    ],
    copilot_value_experiment_source_delta_files: [
      { metric: { change_type: 'added' }, value: [0, '1'] },
      { metric: { change_type: 'modified' }, value: [0, '2'] },
      { metric: { change_type: 'deleted' }, value: [0, '3'] },
      { metric: { change_type: 'renamed' }, value: [0, '4'] },
      { metric: { change_type: 'unchanged' }, value: [0, '5'] }
    ],
    copilot_value_experiment_source_snapshot_characters: [
      { metric: { state: 'before' }, value: [0, '100'] },
      { metric: { state: 'after' }, value: [0, '131'] }
    ],
    copilot_value_experiment_source_snapshot_files: [
      { metric: { state: 'before' }, value: [0, '9'] },
      { metric: { state: 'after' }, value: [0, '10'] }
    ],
    copilot_value_experiment_source_start_timestamp_seconds: [
      { metric: {}, value: [0, String(Date.parse(startedAt) / 1_000)] }
    ],
    copilot_value_experiment_source_completion_timestamp_seconds: [
      { metric: {}, value: [0, String(Date.parse(completedAt) / 1_000)] }
    ]
  };
  const server = createServer((request, response) => {
    const expression = new URL(request.url ?? '/', 'http://127.0.0.1').searchParams.get('query') ?? '';
    const metric = Object.keys(resultByMetric).find((name) => expression.includes(name));
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({
      status: 'success',
      data: { result: metric ? resultByMetric[metric] : [] }
    }));
  });
  server.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.once('listening', resolve));
  context.after(() => new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve())));
  const address = server.address();
  assert.ok(address && typeof address === 'object');

  const delta = await collectOtelSourceDelta(
    `http://127.0.0.1:${address.port}`,
    'complete',
    { startedAt, completedAt }
  );

  assert.deepEqual(delta, {
    source: 'otel_source_delta',
    filesBefore: 9,
    filesAfter: 10,
    filesAdded: 1,
    filesModified: 2,
    filesDeleted: 3,
    filesRenamed: 4,
    filesUnchanged: 5,
    charactersBefore: 100,
    charactersAfter: 131,
    charactersAdded: 32,
    charactersRemoved: 1,
    linesAdded: 2,
    linesRemoved: 1
  });
});

test('exports source evidence and waits for its completion timestamp', async (context) => {
  const startedAt = '2026-08-05T10:00:00Z';
  const completedAt = '2026-08-05T10:05:00Z';
  let exportedBody = '';
  const server = createServer((request, response) => {
    if (request.method === 'POST') {
      request.setEncoding('utf8');
      request.on('data', (chunk: string) => { exportedBody += chunk; });
      request.on('end', () => {
        response.writeHead(200);
        response.end();
      });
      return;
    }
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({
      status: 'success',
      data: { result: [{ metric: {}, value: [0, String(Date.parse(completedAt) / 1_000)] }] }
    }));
  });
  server.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.once('listening', resolve));
  context.after(() => new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve())));
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const endpoint = `http://127.0.0.1:${address.port}`;

  await exportSourceDeltaToOtel(endpoint, endpoint, 'export-test', startedAt, completedAt, {
    filesBefore: 1,
    filesAfter: 1,
    filesAdded: 0,
    filesModified: 1,
    filesDeleted: 0,
    filesRenamed: 0,
    filesUnchanged: 0,
    charactersBefore: 10,
    charactersAfter: 11,
    charactersAdded: 1,
    charactersRemoved: 0,
    linesAdded: 1,
    linesRemoved: 1
  });

  assert.match(exportedBody, /copilot_value_experiment_source_delta_characters/);
  assert.match(exportedBody, /export-test/);
  assert.doesNotMatch(exportedBody, /source_content|file_path/);
});