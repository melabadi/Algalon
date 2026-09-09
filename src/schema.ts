import { z } from 'zod';

const count = z.number().nonnegative().nullish().transform((value) => value ?? 0);
const money = z.number().nonnegative();

export const otelSnapshotSchema = z.object({
  day: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  activeDay: count,
  sessions: count,
  agentInvocations: count,
  agentTurns: count,
  toolCalls: count,
  successfulToolCalls: count,
  codingToolCalls: count,
  researchToolCalls: count,
  planningToolCalls: count,
  unmappedToolCalls: count,
  acceptedEditDecisions: count,
  rejectedEditDecisions: count,
  unmappedEditDecisions: count,
  agentEditLoc: count,
  appliedUserActions: count,
  editSurvivalFourGram: z.number().min(0).max(1).nullable(),
  editSurvivalNoRevert: z.number().min(0).max(1).nullable()
});

const scenarioSchema = z.object({
  minutesPerAcceptedEdit: money,
  minutesPerAgentEditLoc: money,
  minutesPerCodingToolCall: money,
  minutesPerResearchToolCall: money,
  minutesPerPlanningToolCall: money,
  capacityRealization: z.number().min(0).max(1)
});

const tokenManualAssumptionSchema = z.object({
  relevantTokenFraction: z.number().min(0).max(1),
  tokensPerMinute: z.number().positive(),
  interactionMinutesPerTool: z.number().nonnegative(),
  reasoningTokenWeight: z.number().min(0).max(1).default(0)
});

const benchmarkScenarioSchema = z.object({
  planning: tokenManualAssumptionSchema,
  research: tokenManualAssumptionSchema,
  coding: z.object({
    manualEntryFraction: z.number().min(0).max(1),
    wordsPerMinute: z.number().positive()
  }),
  validation: tokenManualAssumptionSchema,
  unclassifiedManualMultiplier: z.number().positive()
});

const benchmarkScenariosSchema = z.object({
  pessimistic: benchmarkScenarioSchema,
  base: benchmarkScenarioSchema,
  optimistic: benchmarkScenarioSchema
});

const calibrationSourceSchema = z.object({
  title: z.string().min(1),
  publisher: z.string().min(1),
  publishedAt: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  url: z.string().url().refine((value) => value.startsWith('https://'), {
    message: 'Calibration source URLs must use HTTPS.'
  }),
  evidenceClass: z.enum([
    'controlled_experiment',
    'literature_benchmark',
    'official_statistic',
    'survey_context',
    'local_measurement',
    'other'
  ]),
  appliesTo: z.array(z.string().min(1)).min(1),
  supportLevels: z.record(
    z.string().min(1),
    z.enum(['direct', 'proxy', 'context'])
  ).default({}),
  finding: z.string().min(1),
  limitation: z.string().min(1)
});

const defaultBenchmark = {
  acknowledgedAssumptions: false,
  manualTimeModelSource: 'Mechanistic token/artifact assumptions; calibrate with local paired tasks',
  capacityRealization: 0.5,
  capacityRealizationBand: [0.25, 0.5, 0.75],
  maxIdleGapSeconds: 300,
  typingWordsPerMinute: 52,
  charactersPerWord: 5,
  calibrationSources: [],
  phaseToolPatterns: {
    planning: ['manage_todo_list', 'todo', 'ask_questions', 'questions'],
    research: ['search', 'read', 'fetch', 'grep', 'semantic', 'reference', 'documentation', 'list_dir', 'view_image', 'tool_search', 'usages'],
    coding: ['apply_patch', 'create_file', 'create_directory', 'replace_string_in_file', 'edit_notebook_file', 'rename'],
    validation: ['run_in_terminal', 'get_terminal_output', 'terminal', 'get_errors', 'test', 'playwright', 'screenshot', 'click', 'navigate', 'open_browser', 'run_task', 'task_output']
  },
  scenarios: {
    pessimistic: {
      planning: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.12, reasoningTokenWeight: 0 },
      research: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.12, reasoningTokenWeight: 0 },
      coding: { manualEntryFraction: 0.25, wordsPerMinute: 60 },
      validation: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.12, reasoningTokenWeight: 0 },
      unclassifiedManualMultiplier: 1
    },
    base: {
      planning: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25, reasoningTokenWeight: 0 },
      research: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25, reasoningTokenWeight: 0 },
      coding: { manualEntryFraction: 0.5, wordsPerMinute: 40 },
      validation: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25, reasoningTokenWeight: 0 },
      unclassifiedManualMultiplier: 1.25
    },
    optimistic: {
      planning: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5, reasoningTokenWeight: 0.25 },
      research: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5, reasoningTokenWeight: 0.25 },
      coding: { manualEntryFraction: 1, wordsPerMinute: 25 },
      validation: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5, reasoningTokenWeight: 0.25 },
      unclassifiedManualMultiplier: 1.5
    }
  }
};

export const valueConfigSchema = z.object({
  victoriaMetricsUrl: z.string().url().default('http://127.0.0.1:8428'),
  otelHttpEndpoint: z.string().url().default('http://127.0.0.1:4318'),
  traceArchivePath: z.string().min(1).default('data/otel/traces.json'),
  measurementDelayDays: z.number().int().min(0).max(14).default(0),
  loadedHourlyRateUsd: z.number().positive(),
  monthlySeatCostUsd: money,
  monthlyVariableCostUsd: money.default(0),
  monthlyEnablementCostUsd: money.default(0),
  acknowledgedAssumptions: z.boolean().default(false),
  codingToolPatterns: z.array(z.string().min(1)).min(1).default([
    'apply_patch', 'create_file', 'create_directory', 'replace_string_in_file', 'edit_notebook_file', 'rename'
  ]),
  researchToolPatterns: z.array(z.string().min(1)).min(1).default([
    'search', 'read', 'fetch', 'grep', 'semantic', 'reference', 'documentation', 'usages'
  ]),
  planningToolPatterns: z.array(z.string().min(1)).min(1).default([
    'manage_todo_list', 'todo', 'questions'
  ]),
  acceptedDecisionValues: z.array(z.string().min(1)).min(1).default([
    'accept', 'accepted', 'apply', 'applied', 'saved', 'keep', 'kept'
  ]),
  rejectedDecisionValues: z.array(z.string().min(1)).min(1).default([
    'reject', 'rejected', 'discard', 'discarded'
  ]),
  benchmark: z.object({
    acknowledgedAssumptions: z.boolean().default(false),
    manualTimeModelSource: z.string().min(1),
    capacityRealization: z.number().gt(0).max(1),
    capacityRealizationBand: z.array(z.number().gt(0).max(1)).min(1).default([0.25, 0.5, 0.75]),
    maxIdleGapSeconds: z.number().nonnegative().max(86_400).default(300),
    typingWordsPerMinute: z.number().positive(),
    charactersPerWord: z.number().positive(),
    calibrationSources: z.array(calibrationSourceSchema).default([]),
    phaseToolPatterns: z.object({
      planning: z.array(z.string().min(1)).min(1),
      research: z.array(z.string().min(1)).min(1),
      coding: z.array(z.string().min(1)).min(1),
      validation: z.array(z.string().min(1)).min(1)
    }),
    presetScenarios: benchmarkScenariosSchema.optional(),
    scenarios: benchmarkScenariosSchema
  }).default(defaultBenchmark),
  scenarios: z.object({
    pessimistic: scenarioSchema,
    base: scenarioSchema,
    optimistic: scenarioSchema
  })
});

export type OtelSnapshot = z.output<typeof otelSnapshotSchema>;
export type ValueConfig = z.output<typeof valueConfigSchema>;
export type Scenario = z.output<typeof scenarioSchema>;