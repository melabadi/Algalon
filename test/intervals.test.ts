import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { unionIntervalDuration } from '../shared/intervals.js';

interface IntervalCase {
  name: string;
  intervals: Array<[number, number]>;
  expected: number;
}

test('matches the shared interval-union contract', async () => {
  const cases = JSON.parse(
    await readFile('test/fixtures/interval-union.json', 'utf8')
  ) as IntervalCase[];
  for (const fixture of cases) {
    assert.equal(unionIntervalDuration(fixture.intervals), fixture.expected, fixture.name);
  }
});
