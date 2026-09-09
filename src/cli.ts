import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { calculateMechanisticBenchmark, conservativeQualityFactor } from './benchmark.js';
import { collectTraceUsage } from './experiment-evidence.js';
import { calculateValue } from './model.js';
import { buildExperimentMetricPayload, buildMetricPayload, publishMetrics } from './metrics.js';
import { collectOtelSnapshot } from './otel.js';
import { otelSnapshotSchema, valueConfigSchema } from './schema.js';
import { collectOtelSourceDelta } from './source-delta.js';

interface CliOptions {
  configPath: string;
  day: string | undefined;
  experiment: string | undefined;
  startTime: string | undefined;
  endTime: string | undefined;
  fixturePath: string | undefined;
  publish: boolean;
  write: boolean;
}

function optionValue(arguments_: string[], name: string): string | undefined {
  const index = arguments_.indexOf(name);
  return index >= 0 ? arguments_[index + 1] : undefined;
}

function parseOptions(arguments_: string[]): CliOptions {
  const command = arguments_[0] ?? 'calculate';
  if (command !== 'calculate' && command !== 'sync') {
    throw new Error('Supported command: calculate');
  }
  return {
    configPath: optionValue(arguments_, '--config') ?? 'config/value-model.local.json',
    day: optionValue(arguments_, '--day'),
    experiment: optionValue(arguments_, '--experiment'),
    startTime: optionValue(arguments_, '--start-time'),
    endTime: optionValue(arguments_, '--end-time'),
    fixturePath: optionValue(arguments_, '--fixture'),
    publish: !arguments_.includes('--no-publish'),
    write: !arguments_.includes('--no-write')
  };
}

function measurementDay(delayDays: number): string {
  const day = new Date();
  day.setUTCDate(day.getUTCDate() - delayDays);
  return day.toISOString().slice(0, 10);
}

async function main(): Promise<void> {
  const options = parseOptions(process.argv.slice(2));
  if (process.argv.includes('--source-path')) {
    throw new Error('--source-path is not supported by calculate; the session worker captures source baselines automatically.');
  }
  if ((options.experiment && !options.startTime) || (!options.experiment && (options.startTime || options.endTime))) {
    throw new Error('--experiment and --start-time must be provided together.');
  }
  if (options.experiment && !/^[a-z0-9][a-z0-9_-]{0,63}$/i.test(options.experiment)) {
    throw new Error('Experiment names may contain only letters, numbers, hyphens, and underscores.');
  }
  let configText: string;
  try {
    configText = await readFile(options.configPath, 'utf8');
  } catch {
    throw new Error(`Configuration not found at '${options.configPath}'. Create it from config/value-model.example.json.`);
  }
  const config = valueConfigSchema.parse(JSON.parse(configText));
  const completedAt = options.endTime ? new Date(options.endTime) : new Date();
  if (!Number.isFinite(completedAt.getTime())) {
    throw new Error(`Invalid experiment end time '${options.endTime}'.`);
  }
  const day = options.day ?? options.startTime?.slice(0, 10) ?? measurementDay(config.measurementDelayDays);
  const snapshot = options.fixturePath
    ? otelSnapshotSchema.parse(JSON.parse(await readFile(options.fixturePath, 'utf8')))
    : await collectOtelSnapshot(day, {
        victoriaMetricsUrl: config.victoriaMetricsUrl,
      ...(options.startTime ? { startTime: options.startTime } : {}),
        codingToolPatterns: config.codingToolPatterns,
        researchToolPatterns: config.researchToolPatterns,
        planningToolPatterns: config.planningToolPatterns,
        acceptedDecisionValues: config.acceptedDecisionValues,
        rejectedDecisionValues: config.rejectedDecisionValues
      }, completedAt);

  const result = calculateValue(snapshot, config);
  const experimentEvidence = options.experiment && options.startTime
    ? await (async () => {
        const usage = await collectTraceUsage(
          config.traceArchivePath,
          new Date(options.startTime as string),
          completedAt,
          config.benchmark.phaseToolPatterns,
          undefined,
          config.benchmark.maxIdleGapSeconds
        );
        const source = await collectOtelSourceDelta(
          config.victoriaMetricsUrl,
          options.experiment as string,
          { startedAt: options.startTime as string, completedAt: completedAt.toISOString() }
        );
        const qualityFactor = conservativeQualityFactor(
          snapshot.editSurvivalNoRevert,
          snapshot.editSurvivalFourGram
        );
        const benchmark = config.benchmark.acknowledgedAssumptions
          ? calculateMechanisticBenchmark({
              phases: Object.fromEntries(Object.entries(usage.phases).map(([phase, evidence]) => [phase, {
                measuredAiSeconds: evidence.allocatedSeconds,
                uncachedInputTokens: evidence.uncachedInputTokens,
                outputTokens: evidence.outputTokens,
                reasoningTokens: evidence.reasoningTokens,
                toolCalls: evidence.toolCalls,
                toolActiveSeconds: evidence.toolActiveSeconds
              }])) as Parameters<typeof calculateMechanisticBenchmark>[0]['phases'],
              aiCostUsd: usage.aiCostUsd,
              qualityFactor,
              retainedSourceCharacters: source.charactersAdded
            }, {
              loadedHourlyRateUsd: config.loadedHourlyRateUsd,
              capacityRealization: config.benchmark.capacityRealization,
              capacityRealizationBand: config.benchmark.capacityRealizationBand,
              typingWordsPerMinute: config.benchmark.typingWordsPerMinute,
              charactersPerWord: config.benchmark.charactersPerWord,
              scenarios: config.benchmark.scenarios
            })
          : null;
        return { usage, source, qualityFactor, benchmark };
      })()
    : null;
  if (options.write) {
    const outputDirectory = options.experiment
      ? path.join('data', 'value', 'experiments')
      : path.join('data', 'value');
    await mkdir(outputDirectory, { recursive: true });
    const output = options.experiment && experimentEvidence
      ? {
          experiment: { name: options.experiment, startedAt: options.startTime, completedAt: completedAt.toISOString() },
          observed: snapshot,
          usage: experimentEvidence.usage,
          source: experimentEvidence.source,
          benchmark: experimentEvidence.benchmark,
          metadata: {
            source: 'local_otel',
            costBasis: 'ai_credit_usage_value',
            manualTimeModelSource: config.benchmark.manualTimeModelSource,
            calibration: {
              capacityRealization: config.benchmark.capacityRealization,
              charactersPerWord: config.benchmark.charactersPerWord,
              scenarios: config.benchmark.scenarios
            },
            assumptionsAcknowledged: config.benchmark.acknowledgedAssumptions
          }
        }
      : result;
    const outputName = options.experiment ?? day;
    await writeFile(path.join(outputDirectory, `${outputName}.json`), `${JSON.stringify(output, null, 2)}\n`, 'utf8');
  }

  if (options.publish) {
    const payload = options.experiment && options.startTime && experimentEvidence
      ? buildExperimentMetricPayload(
          snapshot,
          experimentEvidence.usage,
          experimentEvidence.source,
          experimentEvidence.benchmark,
          options.experiment,
          options.startTime,
          completedAt,
          new Date()
        )
      : buildMetricPayload(result, config);
    await publishMetrics(payload, config.victoriaMetricsUrl);
  }

  console.log(JSON.stringify({
    experiment: options.experiment,
    startedAt: options.startTime,
    completedAt: options.experiment ? completedAt.toISOString() : undefined,
    measurementDay: day,
    sessions: result.observed.sessions,
    agentInvocations: result.observed.agentInvocations,
    codingToolCalls: result.observed.codingToolCalls,
    acceptedEditDecisions: result.observed.acceptedEditDecisions,
    agentEditLoc: result.observed.agentEditLoc,
    researchToolCalls: result.observed.researchToolCalls,
    planningToolCalls: result.observed.planningToolCalls,
    aiCostUsd: experimentEvidence?.usage.aiCostUsd,
    aiCredits: experimentEvidence?.usage.aiCredits,
    models: experimentEvidence?.usage.models.map(({ model, requests }) => ({ model, requests })),
    retainedSourceCharacters: experimentEvidence?.source.charactersAdded,
    removedSourceCharacters: experimentEvidence?.source.charactersRemoved,
    baseScenario: experimentEvidence?.benchmark?.scenarios.base ?? result.scenarios?.base ?? 'not configured',
    written: options.write,
    published: options.publish
  }, null, 2));
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});