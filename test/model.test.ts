import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { calculateValue, validateScenarioOrdering } from '../src/model.js';
import { otelSnapshotSchema, valueConfigSchema } from '../src/schema.js';

async function fixtures() {
  const snapshot = otelSnapshotSchema.parse(JSON.parse(await readFile('test/fixtures/otel-snapshot.json', 'utf8')));
  const config = valueConfigSchema.parse(JSON.parse(await readFile('test/fixtures/value-model.json', 'utf8')));
  return { snapshot, config };
}

test('preserves observed OTel output and configured daily cost allocation', async () => {
  const { snapshot, config } = await fixtures();
  const result = calculateValue(snapshot, config);
  assert.equal(result.observed.acceptedEditDecisions, 7);
  assert.equal(result.observed.agentEditLoc, 40);
  assert.equal(result.observed.codingToolCalls, 3);
  assert.equal(result.observed.researchToolCalls, 4);
  assert.equal(result.observed.planningToolCalls, 2);
  assert.equal(result.costs.variableAllocatedUsd, 0);
  assert.ok(Math.abs(result.costs.seatAllocatedUsd - 39 / 31) < 1e-12);
  assert.ok(Math.abs(
    result.costs.breakEvenMinutes - 60 * result.costs.cashCostUsd / config.loadedHourlyRateUsd
  ) < 1e-12);
});

test('uses overlap-safe coding evidence and observed survival in ROI scenarios', async () => {
  const { snapshot, config } = await fixtures();
  const result = calculateValue(snapshot, config);
  assert.ok(Math.abs((result.scenarios?.base.estimatedMinutesSaved ?? 0) - 38.8) < 1e-12);
  assert.ok(Math.abs(
    (result.scenarios?.base.estimatedBenefitUsd ?? 0) -
      38.8 / 60 * config.loadedHourlyRateUsd * config.scenarios.base.capacityRealization
  ) < 1e-12);
  assert.ok((result.scenarios?.pessimistic.roi ?? 0) < (result.scenarios?.base.roi ?? 0));
  assert.ok((result.scenarios?.base.roi ?? 0) < (result.scenarios?.optimistic.roi ?? 0));
});

test('rejects scenario assumptions that are not ordered', async () => {
  const { config } = await fixtures();
  config.scenarios.pessimistic.minutesPerAcceptedEdit = 10;
  assert.throws(() => validateScenarioOrdering(config), /pessimistic <= base <= optimistic/);
});

test('disables estimated ROI until assumptions are acknowledged', async () => {
  const { snapshot, config } = await fixtures();
  config.acknowledgedAssumptions = false;
  assert.equal(calculateValue(snapshot, config).scenarios, null);
});

test('uses OTel coding-tool calls when native edit and LoC instruments are absent', async () => {
  const { snapshot, config } = await fixtures();
  snapshot.acceptedEditDecisions = 0;
  snapshot.agentEditLoc = 0;
  snapshot.codingToolCalls = 4;
  const result = calculateValue(snapshot, config);
  assert.equal(result.scenarios?.base.estimatedMinutesSaved, 34);
});