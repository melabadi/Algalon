import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { valueConfigSchema } from '../src/schema.js';

test('keeps evidence-anchored fallback assumptions aligned with the example config', async () => {
  const example = JSON.parse(await readFile(
    path.join(process.cwd(), 'config', 'value-model.example.json'),
    'utf8'
  )) as {
    benchmark: {
      phaseToolPatterns: Record<string, string[]>;
      typingWordsPerMinute: number;
      scenarios: {
        pessimistic: {
          planning: { interactionMinutesPerTool: number };
        };
      };
    };
    loadedHourlyRateUsd: number;
    [key: string]: unknown;
  };
  const withoutBenchmark = structuredClone(example) as Record<string, unknown>;
  delete withoutBenchmark.benchmark;

  const parsed = valueConfigSchema.parse(withoutBenchmark);

  assert.deepEqual(parsed.benchmark.phaseToolPatterns, example.benchmark.phaseToolPatterns);
  assert.equal(example.loadedHourlyRateUsd, 92);
  assert.equal(parsed.benchmark.typingWordsPerMinute, example.benchmark.typingWordsPerMinute);
  assert.equal(parsed.benchmark.typingWordsPerMinute, 52);
  assert.equal(
    parsed.benchmark.scenarios.pessimistic.planning.interactionMinutesPerTool,
    example.benchmark.scenarios.pessimistic.planning.interactionMinutesPerTool
  );
  assert.equal(parsed.benchmark.scenarios.pessimistic.planning.interactionMinutesPerTool, 0.12);
});

test('accepts scoped calibration evidence and rejects insecure citations', async () => {
  const example = JSON.parse(await readFile(
    path.join(process.cwd(), 'config', 'value-model.example.json'),
    'utf8'
  )) as Record<string, unknown>;

  const parsed = valueConfigSchema.parse(example);
  assert.equal(parsed.benchmark.calibrationSources.length, 10);
  assert.deepEqual(parsed.benchmark.calibrationSources[0]?.appliesTo, ['scenario envelope']);
  const typingStudy = parsed.benchmark.calibrationSources.find(
    (source) => source.title === 'Observations on Typing from 136 Million Keystrokes'
  );
  assert.equal(typingStudy?.supportLevels['characters per word'], 'direct');
  assert.equal(typingStudy?.supportLevels['manual source-entry rate'], 'proxy');
  const fieldExperiment = parsed.benchmark.calibrationSources.find(
    (source) => source.title.startsWith('The Effects of Generative AI on High-Skilled Work')
  );
  assert.equal(fieldExperiment?.supportLevels['scenario envelope'], 'context');
  const interactionStudy = parsed.benchmark.calibrationSources.find(
    (source) => source.title.startsWith('Reading Between the Lines')
  );
  assert.equal(interactionStudy?.evidenceClass, 'literature_benchmark');
  assert.equal(interactionStudy?.supportLevels['tool interaction overhead'], 'proxy');

  const expanded = structuredClone(example) as {
    benchmark: { calibrationSources: Array<Record<string, unknown>> };
  };
  expanded.benchmark.calibrationSources.push(
    {
      title: 'Published benchmark',
      publisher: 'Research venue',
      publishedAt: '2020-01-01',
      url: 'https://example.com/literature',
      evidenceClass: 'literature_benchmark',
      appliesTo: ['human review rate'],
      finding: 'Reports a benchmark measured in another task population.',
      limitation: 'Requires local validation before use as a model constant.'
    },
    {
      title: 'Official statistic',
      publisher: 'Statistical agency',
      publishedAt: '2020-01-01',
      url: 'https://example.com/statistic',
      evidenceClass: 'official_statistic',
      appliesTo: ['loaded labor rate'],
      finding: 'Reports an official market statistic.',
      limitation: 'Does not replace organization-specific finance data.'
    }
  );
  assert.doesNotThrow(() => valueConfigSchema.parse(expanded));

  const insecure = structuredClone(example) as {
    benchmark: { calibrationSources: Array<{ url: string }> };
  };
  insecure.benchmark.calibrationSources[0]!.url = 'http://example.com/source';
  assert.throws(() => valueConfigSchema.parse(insecure), /must use HTTPS/);
});

test('preserves immutable scenario presets beside an active custom calibration', async () => {
  const example = JSON.parse(await readFile(
    path.join(process.cwd(), 'config', 'value-model.example.json'),
    'utf8'
  )) as {
    benchmark: {
      scenarios: Record<string, unknown>;
      presetScenarios?: Record<string, unknown>;
    };
    [key: string]: unknown;
  };
  example.benchmark.presetScenarios = structuredClone(example.benchmark.scenarios);
  const parsed = valueConfigSchema.parse(example);

  assert.deepEqual(parsed.benchmark.presetScenarios, parsed.benchmark.scenarios);
});