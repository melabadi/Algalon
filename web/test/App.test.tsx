import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { App } from '../src/App';
import {
  activeCalibrationStorageKey,
  calibratedOverview,
  completeWorkerUsage,
  loadSavedCalibrations,
  savedCalibrationStorageKey,
  type CalibrationDraft
} from '../src/features/calibration/model';
import type {
  BenchmarkScenario,
  Insights,
  Methodology,
  ModelingStatus,
  Overview,
  Prompt,
  Scenario,
  ScenarioResult,
  Session
} from '../src/types';

const phases = ['planning', 'research', 'coding', 'validation', 'unclassified'];

function createUsage(
  durationSeconds = 754,
  engagedSeconds = Math.min(450, durationSeconds),
  aiCredits = 100,
  aiCostUsd = aiCredits / 100
): Session['usage'] {
  const activeSeconds = engagedSeconds === 0 ? 0 : Math.min(150, engagedSeconds);
  return {
    source: 'otel_traces',
    chatSpans: 4,
    inputTokens: 20_000,
    cacheReadTokens: 10_000,
    uncachedInputTokens: 10_000,
    outputTokens: 1_500,
    reasoningTokens: 500,
    aiCredits,
    aiCostUsd,
    elapsedSeconds: durationSeconds,
    engagedSeconds,
    activeSeconds,
    activityDensity: activeSeconds / durationSeconds,
    models: [{
      model: 'model-a',
      requests: 4,
      inputTokens: 20_000,
      cacheReadTokens: 10_000,
      uncachedInputTokens: 10_000,
      outputTokens: 1_500,
      reasoningTokens: 500,
      aiCredits,
      aiCostUsd
    }],
    phases: Object.fromEntries(phases.map((phase, index) => [phase, {
      allocatedSeconds: engagedSeconds * (index + 1) / 15,
      activeSeconds: activeSeconds * (index + 1) / 15,
      toolActiveSeconds: engagedSeconds === 0 ? 0 : index * 5,
      toolCalls: index,
      modelSpans: index < 4 ? 1 : 0,
      uncachedInputTokens: index < 4 ? index * 100 : 0,
      outputTokens: index < 4 ? index * 20 : 0,
      reasoningTokens: index < 4 ? index * 5 : 0
    }]))
  };
}

function scenarioResult(scale = 1): ScenarioResult {
  return {
    estimatedManualMinutes: 20 * scale,
    estimatedMinutesSaved: 8 * scale,
    estimatedManualLaborCostUsd: 40 * scale,
    estimatedAiAssistedLaborCostUsd: 24,
    estimatedAiAssistedTotalCostUsd: 25,
    estimatedGrossCostSavingsUsd: 15 * scale,
    estimatedBenefitUsd: 8 * scale,
    netValueUsd: 7 * scale,
    roi: 7 * scale,
    taskTimeReduction: 0.4,
    breakEvenManualMinutes: 13,
    phases: Object.fromEntries(phases.map((phase, index) => [phase, {
      measuredAiMinutes: index + 1,
      measuredAiSeconds: (index + 1) * 60,
      estimatedManualMinutes: (index + 2) * scale,
      estimatedMinutesSaved: phase === 'validation' ? -1 : scale,
      timeReduction: 0.2
    }]))
  };
}

function createSession(overrides: Partial<Session> = {}): Session {
  const pessimistic = scenarioResult(0.5);
  const base = scenarioResult(1);
  const optimistic = scenarioResult(2);
  const durationSeconds = overrides.durationSeconds ?? 754;
  const aiCostUsd = overrides.aiCostUsd ?? 1;
  const aiCredits = overrides.aiCredits ?? aiCostUsd * 100;
  return {
    experiment: 'session-a',
    startedAt: '2026-08-05T10:00:00Z',
    completedAt: '2026-08-05T10:12:34Z',
    status: 'published',
    durationSeconds,
    aiCostUsd,
    aiCredits,
    chatSpans: 4,
    tokens: {
      input: 20_000,
      cacheRead: 10_000,
      uncachedInput: 10_000,
      output: 1_500,
      reasoning: 500
    },
    retainedSourceCharacters: 120,
    sourceEvidenceComplete: true,
    promptCount: 2,
    latestMessageAt: '2026-08-05T10:11:00Z',
    scenario: 'base',
    modelingStatus: 'available',
    scenarioResult: base,
    usage: createUsage(durationSeconds, Math.min(450, durationSeconds), aiCredits, aiCostUsd),
    source: {
      source: 'otel_source_delta',
      filesAdded: 1,
      filesModified: 2,
      charactersAdded: 120,
      charactersRemoved: 5,
      linesAdded: 12,
      linesRemoved: 1
    },
    benchmark: {
      scenarios: { pessimistic, base, optimistic },
      qualityFactor: 1,
      typingEquivalentMinutes: 2.5
    },
    ...overrides
  };
}

function createOverview(scenario: Scenario, sessions = [
  createSession(),
  createSession({
    experiment: 'session-b',
    durationSeconds: 45,
    aiCostUsd: 0,
    promptCount: 0,
    latestMessageAt: null,
    sourceEvidenceComplete: false,
    scenarioResult: null,
    benchmark: null,
    source: { source: 'otel_only' }
  })
], modelingStatus: ModelingStatus = 'available'): Overview {
  const scale = scenario === 'pessimistic' ? 0.5 : scenario === 'optimistic' ? 2 : 1;
  return {
    scenario,
    days: 30,
    modelingStatus,
    totals: {
      sessions: sessions.length,
      prompts: sessions.reduce((total, session) => total + session.promptCount, 0),
      durationSeconds: sessions.reduce((total, session) => total + session.durationSeconds, 0),
      aiCostUsd: sessions.reduce((total, session) => total + session.aiCostUsd, 0),
      taskTimeReduction: 0.4,
      estimatedManualMinutes: 20 * scale,
      estimatedMinutesSaved: 8 * scale,
      estimatedManualLaborCostUsd: 40 * scale,
      estimatedAiAssistedLaborCostUsd: 24,
      estimatedAiAssistedTotalCostUsd: 25,
      estimatedGrossCostSavingsUsd: 15 * scale,
      estimatedBenefitUsd: 8 * scale,
      netValueUsd: 7 * scale,
      roi: 7 * scale
    },
    sessions
  };
}

const prompts: Prompt[] = [
  {
    promptId: 'prompt-1',
    experiment: 'session-a',
    ordinal: 1,
    ordinalExact: '1',
    startedAt: '2026-08-05T10:00:10Z',
    content: 'Build the feature',
    contentAvailable: true,
    capturedContentLength: 12_345,
    capturedContentLengthExact: '12345',
    modelRequests: 2,
    modelRequestsExact: '2',
    toolCalls: 3,
    toolCallsExact: '3',
    inputTokens: 2_000,
    inputTokensExact: '2000',
    cacheReadTokens: 1_200,
    cacheReadTokensExact: '1200',
    cacheReadRatio: 0.6,
    outputTokens: 300,
    outputTokensExact: '300',
    reasoningTokens: 100,
    reasoningTokensExact: '100',
    aiCredits: 25,
    aiCostUsd: 0.25,
    usageSource: 'copilot_turn_log',
    models: { 'model-a': 2 },
    modelsExact: { 'model-a': '2' },
    roi: null,
    roiStatus: 'prompt_level_attribution_required'
  },
  {
    promptId: 'prompt-2',
    experiment: 'session-a',
    ordinal: 2,
    ordinalExact: '2',
    startedAt: '2026-08-05T10:05:00Z',
    content: '',
    contentAvailable: false,
    capturedContentLength: 10,
    capturedContentLengthExact: '10',
    modelRequests: 1,
    modelRequestsExact: '1',
    toolCalls: 0,
    toolCallsExact: '0',
    inputTokens: 100,
    inputTokensExact: '100',
    cacheReadTokens: 120,
    cacheReadTokensExact: '120',
    cacheReadRatio: 1,
    outputTokens: 10,
    outputTokensExact: '10',
    reasoningTokens: 0,
    reasoningTokensExact: '0',
    aiCredits: 1,
    aiCostUsd: 0.01,
    usageSource: 'otel_trace',
    models: {},
    modelsExact: {},
    roi: null,
    roiStatus: 'prompt_level_attribution_required'
  }
];

const calibration: BenchmarkScenario = {
  planning: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
  research: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
  coding: { manualEntryFraction: 0.5, wordsPerMinute: 40 },
  validation: { relevantTokenFraction: 0.25, tokensPerMinute: 400, interactionMinutesPerTool: 0.25 },
  unclassifiedManualMultiplier: 1.25
};

const pessimisticCalibration: BenchmarkScenario = {
  planning: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.12 },
  research: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.12 },
  coding: { manualEntryFraction: 0.25, wordsPerMinute: 60 },
  validation: { relevantTokenFraction: 0.1, tokensPerMinute: 600, interactionMinutesPerTool: 0.12 },
  unclassifiedManualMultiplier: 1
};

const optimisticCalibration: BenchmarkScenario = {
  planning: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5 },
  research: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5 },
  coding: { manualEntryFraction: 1, wordsPerMinute: 25 },
  validation: { relevantTokenFraction: 0.5, tokensPerMinute: 250, interactionMinutesPerTool: 0.5 },
  unclassifiedManualMultiplier: 1.5
};

const validCalibrationDraft: CalibrationDraft = {
  loadedHourlyRateUsd: 92,
  capacityRealization: 0.5,
  typingWordsPerMinute: 52,
  charactersPerWord: 5,
  scenarios: {
    pessimistic: pessimisticCalibration,
    base: calibration,
    optimistic: optimisticCalibration
  }
};

const methodology: Methodology = {
  claim: 'Modeled AI Usage ROI',
  scenarios: ['pessimistic', 'base', 'optimistic'],
  config: {
    loadedHourlyRateUsd: 92,
    benchmark: {
      capacityRealization: 0.5,
      typingWordsPerMinute: 52,
      charactersPerWord: 5,
      calibrationSources: [{
        title: 'The Impact of AI on Developer Productivity: Evidence from GitHub Copilot',
        publisher: 'arXiv',
        publishedAt: '2023-02-13',
        url: 'https://arxiv.org/abs/2302.06590',
        evidenceClass: 'controlled_experiment',
        appliesTo: ['scenario envelope'],
        finding: 'In a controlled JavaScript HTTP-server task, participants with Copilot completed the task 55.8% faster than the control group.',
        limitation: 'One bounded task does not directly calibrate phase token relevance, review speed, tool overhead, capacity realization, or local ROI.'
      }],
      phaseToolPatterns: {
        planning: ['manage_todo_list'],
        research: ['read_file'],
        coding: ['apply_patch'],
        validation: ['run_in_terminal']
      },
      scenarios: {
        pessimistic: pessimisticCalibration,
        base: calibration,
        optimistic: optimisticCalibration
      }
    }
  },
  formulas: {
    phaseSavings: 'T_saved,p,s = T_manual,p,s - T_AI,p',
    totalSavings: 'T_saved,s = sum_p(T_saved,p,s)',
    portfolioAiTime: 'T_AI,portfolio = measure(union_i([t_start,i, t_end,i]))',
    portfolioSavings: 'T_saved,portfolio,s = sum_i(T_manual,i,s) - T_AI,portfolio',
    manualLaborCost: 'C_manual,s = (T_manual,s / 60) * H',
    aiAssistedTotalCost: 'C_assisted = (T_AI / 60) * H + C_AI',
    portfolioAssistedCost: 'C_assisted,portfolio = (T_AI,portfolio / 60) * H + sum_i(C_AI,i)',
    grossCostSavings: 'Delta_C_gross,s = C_manual,s - C_assisted',
    benefit: 'B_s = (T_saved,s / 60) * H * rho',
    aiCost: 'C_AI = sum(copilot_usage_nano_aiu) / 1e11',
    roi: 'ROI_s = (B_s - C_AI) / C_AI'
  }
};

const insightMetric: Insights['metrics'][number] = {
  key: 'cache_read_ratio',
  label: 'Session cache reuse',
  category: 'Context',
  group: 'behavior',
  scope: 'session',
  unit: 'ratio',
  evidence: 'derived',
  description: 'Share of input tokens served from cache.',
  current: 0.1,
  previous: 0.4,
  trend: 'down',
  normalZone: null,
  zone: 'descriptive',
  status: 'descriptive',
  action: 'If cache reuse is unexpectedly low, keep stable instructions and reusable context at the beginning.',
  severity: null,
  severityLabel: null,
  aggregateSize: 4,
  minimumBaseline: 5,
  relatedMetrics: ['uncached_input_per_request'],
  signal: { level: 'danger', direction: 'minimum', attentionBoundary: 0.5, dangerBoundary: 0.2, minimumSamples: 5 },
  reference: { kind: 'best_practice', support: 'direct', label: 'OpenAI prompt caching', url: 'https://developers.openai.com/api/docs/guides/prompt-caching', note: 'Directly defines cached-token reuse used to calculate this metric.' }
};

const requestInsightMetric: Insights['metrics'][number] = {
  ...insightMetric,
  key: 'output_tokens',
  label: 'Output per request',
  category: 'Generation',
  scope: 'request',
  unit: 'tokens',
  evidence: 'derived',
  description: 'Output tokens divided by model requests.',
  current: 1_500,
  previous: 1_500,
  trend: 'flat',
  normalZone: null,
  zone: 'descriptive',
  status: 'descriptive',
  action: 'If response latency matters, ask for bounded deliverables.',
  severity: null,
  severityLabel: null,
  aggregateSize: 4,
  relatedMetrics: [],
  signal: { level: 'none', direction: 'maximum', attentionBoundary: 2_000, dangerBoundary: 8_000, minimumSamples: 5 },
  reference: { kind: 'best_practice', support: 'direct', label: 'Azure OpenAI latency guidance', url: 'https://learn.microsoft.com/azure/foundry/openai/how-to/latency', note: 'Directly identifies generated tokens as the main latency driver.' }
};

const descriptiveInsightMetric: Insights['metrics'][number] = {
  ...insightMetric,
  key: 'ai_cost_usd',
  label: 'AI usage per request',
  category: 'Cost',
  scope: 'request',
  unit: 'usd',
  evidence: 'derived',
  description: 'AI-credit dollar equivalent divided by model requests.',
  current: 0.13,
  previous: null,
  trend: null,
  normalZone: null,
  zone: 'descriptive',
  status: 'descriptive',
  action: 'Reported without a threshold; read it alongside the guardrail metrics.',
  severity: null,
  severityLabel: null,
  relatedMetrics: [],
  signal: { level: 'not_rated', direction: null, attentionBoundary: null, dangerBoundary: null, minimumSamples: 0 },
  reference: { kind: 'best_practice', support: 'none', label: 'FinOps unit economics', url: 'https://www.finops.org/framework/capabilities/unit-economics/', note: 'Unit economics requires a value denominator that Algalon does not have.' }
};

const insights: Insights = {
  days: 30,
  zoneMethod: {
    typical: 'No good/bad status is assigned without a directly supported threshold.',
    elevated: 'No good/bad status is assigned without a directly supported threshold.',
    high: 'No good/bad status is assigned without a directly supported threshold.',
    descriptive: 'Observed: reported without an arbitrary good/bad threshold.',
    insufficient: 'Insufficient: fewer than 5 eligible observations (20 for coverage), so no status is assigned.',
    low: 'No universal thresholds are applied to these metrics.',
    baselineRule: 'Current values are medians of up to 5 eligible observations, taking the latest within the selected period, and each metric is compared with the immediately preceding period of equal length.',
    coverageRule: 'Coverage metrics use every settled session in the selected period.'
  },
  evidenceHealth: {
    sessionUsageCoverage: 1,
    completeSessions: 4,
    eligibleSessions: 4,
    directUsageSessions: 1,
    otelUsageSessions: 3,
    requiredCoverage: 1,
    integrityPassed: true,
    degraded: false,
    message: null
  },
  summary: { metrics: 4, guardrails: 0, actions: 0, watch: 0, insufficient: 0 },
  priorities: [],
  metrics: [
    insightMetric,
    requestInsightMetric,
    descriptiveInsightMetric,
    {
      ...insightMetric,
      key: 'session_usage_coverage',
      label: 'Sessions with complete usage',
      category: 'Evidence',
      group: 'evidence',
      scope: 'portfolio',
      unit: 'ratio',
      evidence: 'derived',
      current: 1,
      previous: null,
      trend: null,
      normalZone: null,
      zone: 'descriptive',
      status: 'descriptive',
      action: 'Inspect worker artifacts and retained OTel if any settled session lacks complete usage.',
      severity: null,
      severityLabel: null,
      aggregateSize: 4,
      minimumBaseline: 0,
      relatedMetrics: [],
      signal: { level: 'none', direction: 'minimum', attentionBoundary: 1, dangerBoundary: 0.95, minimumSamples: 1 },
      reference: { kind: 'local_measurement', support: 'direct', label: 'Authoritative session usage contract', url: '', note: 'Checks required worker usage fields for every settled session.' }
    }
  ]
};

const degradedInsights: Insights = {
  ...insights,
  evidenceHealth: {
    sessionUsageCoverage: 0.75,
    completeSessions: 3,
    eligibleSessions: 4,
    directUsageSessions: 1,
    otelUsageSessions: 2,
    requiredCoverage: 1,
    integrityPassed: false,
    degraded: true,
    message: 'Integrity gate failed: 3 of 4 settled sessions carry the complete authoritative usage contract. Insights are withheld until session usage coverage returns to 100%.'
  },
  metrics: insights.metrics.map((metric) => metric.key === 'session_usage_coverage' ? {
    ...metric,
    current: 0.75,
    aggregateSize: 4,
    signal: { ...metric.signal, level: 'danger', minimumSamples: 1 }
  } : {
    ...metric,
    aggregateSize: 3,
    current: null,
    previous: null,
    trend: null,
    zone: 'unavailable',
    status: 'unavailable',
    action: 'Withheld: every settled session must carry complete authoritative usage.',
    signal: { ...metric.signal, level: 'unavailable' }
  })
};

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' }
  });
}

function installFetch(options: {
  overviewSessions?: Session[];
  promptValues?: Prompt[];
  failOnce?: boolean;
  methodologyValue?: Methodology;
  insightsValue?: Insights;
  overviewStatus?: ModelingStatus;
  sessionValue?: Session;
} = {}) {
  let remainingFailures = options.failOnce ? 1 : 0;
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    if (remainingFailures > 0) {
      remainingFailures -= 1;
      throw new Error('temporary API failure');
    }
    const rawUrl = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    const url = new URL(rawUrl, 'http://localhost');
    if (url.pathname === '/api/overview') {
      const scenario = (url.searchParams.get('scenario') ?? 'base') as Scenario;
      return jsonResponse(createOverview(
        scenario,
        options.overviewSessions,
        options.overviewStatus ?? 'available'
      ));
    }
    if (url.pathname === '/api/insights') {
      return jsonResponse(options.insightsValue ?? insights);
    }
    if (url.pathname === '/api/methodology') {
      return jsonResponse(options.methodologyValue ?? methodology);
    }
    if (url.pathname.startsWith('/api/prompts/')) {
      const promptId = decodeURIComponent(url.pathname.split('/').at(-1) ?? '');
      return jsonResponse((options.promptValues ?? prompts).find((prompt) => prompt.promptId === promptId));
    }
    if (url.pathname.endsWith('/prompts')) {
      return jsonResponse(options.promptValues ?? prompts);
    }
    if (url.pathname.startsWith('/api/sessions/')) {
      const scenario = (url.searchParams.get('scenario') ?? 'base') as Scenario;
      const session = options.sessionValue ?? createSession({
        scenario,
        scenarioResult: scenario === 'pessimistic' ? scenarioResult(0.5) : scenario === 'optimistic' ? scenarioResult(2) : scenarioResult(1)
      });
      return jsonResponse(session);
    }
    return jsonResponse({ detail: 'not found' }, 404);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function renderRoute(path: string) {
  window.history.replaceState({}, '', path);
  const user = userEvent.setup();
  render(<App />);
  return user;
}

beforeEach(() => {
  window.localStorage.clear();
});

describe('overall workflow', () => {
  test('groups modeled ROI by exact session model cohort without splitting mixed sessions', async () => {
    const singleModel = createSession();
    const mixedModel = createSession({
      experiment: 'session-mixed',
      aiCostUsd: 2,
      aiCredits: 200,
      usage: {
        ...createUsage(754, 450, 200, 2),
        models: [
          { ...createUsage().models![0], model: 'model-a', requests: 2, aiCredits: 100, aiCostUsd: 1 },
          { ...createUsage().models![0], model: 'model-b', requests: 3, aiCredits: 100, aiCostUsd: 1 }
        ]
      },
      scenarioResult: { ...scenarioResult(), netValueUsd: 6, estimatedMinutesSaved: 4, roi: 3 }
    });
    installFetch({ overviewSessions: [singleModel, mixedModel] });

    await renderRoute('/');

    const heading = await screen.findByRole('heading', { name: 'ROI by model cohort' });
    const table = heading.closest('section')!;
    const singleRow = within(table).getByText('model-a').closest('tr')!;
    const mixedRow = within(table).getByText('Mixed · model-a + model-b').closest('tr')!;
    expect(within(singleRow).getByText('+7.00x')).toBeVisible();
    expect(within(mixedRow).getByText('+3.00x')).toBeVisible();
    expect(within(mixedRow).getByText('5')).toBeVisible();

    await userEvent.setup().type(screen.getByRole('searchbox', { name: 'Filter sessions' }), 'mixed');
    expect(within(table).queryByText('model-a')).not.toBeInTheDocument();
    expect(within(table).getByText('Mixed · model-a + model-b')).toBeVisible();
  });

  test('filters, sorts, changes controls, and drills into a session', async () => {
    installFetch();
    const user = await renderRoute('/');
    expect(await screen.findByRole('heading', { name: 'Overall Copilot value' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Custom' })).toBeDisabled();
    expect(screen.getByText('session-a')).toBeVisible();
    expect(screen.getByText('Telemetry only')).toBeVisible();

    const filter = screen.getByRole('searchbox', { name: 'Filter sessions' });
    await user.type(filter, 'nothing');
    expect(await screen.findByText('No sessions match “nothing”.')).toBeVisible();
    await user.clear(filter);

    for (const label of ['Session', 'Started', 'Latest message', 'Prompts', 'AI time', 'AI cost', 'Saved', 'Net value', 'Return', 'Evidence']) {
      await user.click(screen.getByRole('button', { name: new RegExp(`Sort by ${label}`) }));
    }
    await user.click(screen.getByRole('button', { name: /Sort by Evidence/ }));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Time range' }), '90');
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Time range' })).toHaveValue('90'));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Auto-refresh interval' }), '60');
    expect(window.localStorage.getItem('algalon.autoRefreshSeconds')).toBe('60');
    await user.click(screen.getByRole('button', { name: 'Optimistic' }));
    expect(screen.getByText('Optimistic', { selector: 'td' }).closest('tr')).toHaveClass('selected-row');

    await user.click(screen.getByText('session-a'));
    expect(await screen.findByRole('heading', { name: 'Session detail' })).toBeVisible();
    expect(window.location.pathname).toBe('/sessions/session-a');
  });

  test('renders empty sessions', async () => {
    installFetch({ overviewSessions: [] });
    await renderRoute('/');
    expect(await screen.findByRole('heading', { name: 'No measured sessions yet' })).toBeVisible();
  });

  test('shows an API error and retries successfully', async () => {
    installFetch({ failOnce: true });
    const user = await renderRoute('/');
    expect(await screen.findByRole('alert')).toHaveTextContent('temporary API failure');
    await user.click(screen.getByRole('button', { name: 'Retry now' }));
    expect(await screen.findByRole('heading', { name: 'Overall Copilot value' })).toBeVisible();
  });
});

describe('session and prompt routes', () => {
  test('renders prompt counters above JavaScript safe integer exactly', async () => {
    const exactPrompts: Prompt[] = [
      ['prompt-exact-a', 'Exact A', '9007199254740993', '9007199254740994', '9007199254740995'],
      ['prompt-exact-b', 'Exact B', '9007199254740994', '9007199254740995', '9007199254740993'],
      ['prompt-exact-c', 'Exact C', '9007199254740995', '9007199254740993', '9007199254740994']
    ].map(([promptId, content, requests, input, output], index) => ({
      ...prompts[0],
      promptId,
      ordinal: index + 1,
      ordinalExact: String(index + 1),
      content,
      capturedContentLength: null,
      capturedContentLengthExact: String(9007199254741001n + BigInt(index)),
      modelRequests: null,
      modelRequestsExact: requests,
      toolCalls: null,
      toolCallsExact: '9007199254740998',
      inputTokens: null,
      inputTokensExact: input,
      cacheReadTokens: null,
      cacheReadTokensExact: String(BigInt(input) - 1n),
      outputTokens: null,
      outputTokensExact: output,
      reasoningTokens: null,
      reasoningTokensExact: String(BigInt(output) + 1n),
      models: { 'model-a': null },
      modelsExact: { 'model-a': requests }
    }));
    installFetch({ promptValues: exactPrompts });
    const user = await renderRoute('/sessions/session-a/prompts');

    expect(await screen.findByRole('heading', { name: 'Prompts in this session' })).toBeVisible();
    expect(screen.getAllByText('9007199254740993').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('9007199254740995').length).toBeGreaterThanOrEqual(2);
    expect(within(screen.getByText('Model requests').closest('.kpi')!).getByText('27021597764222982')).toBeVisible();
    expect(within(screen.getByText('Tool calls').closest('.kpi')!).getByText('27021597764222994')).toBeVisible();

    for (const [label, expected] of [
      ['Requests', 'Exact C'],
      ['Input', 'Exact B'],
      ['Output', 'Exact A']
    ]) {
      await user.click(screen.getByRole('button', { name: new RegExp(`Sort by ${label}, descending`) }));
      const rows = screen.getByRole('table').querySelectorAll('tbody tr');
      expect(within(rows[0] as HTMLElement).getByText(expected)).toBeVisible();
    }

    await user.click(screen.getByText('Exact A'));

    expect(await screen.findByRole('heading', { name: 'Prompt detail' })).toBeVisible();
    expect(screen.getByText(/9007199254740993 model requests/)).toBeVisible();
    expect(within(screen.getByText('Tool calls').closest('.kpi')!).getByText('9007199254740998')).toBeVisible();
    expect(within(screen.getByText('Input tokens').closest('.kpi')!).getByText('9007199254740994')).toBeVisible();
    expect(within(screen.getByText('Output tokens').closest('.kpi')!).getByText('9007199254740995')).toBeVisible();
    expect(screen.getByText(/Captured request length: 9007199254741001 characters/)).toBeVisible();
    expect(screen.getByText('9007199254740994 / 9007199254740993')).toBeVisible();
    expect(screen.getByText('9007199254740995 / 9007199254740996')).toBeVisible();
    expect(screen.getByText('model-a ×9007199254740993')).toBeVisible();
  });

  test('sorts the session prompt table from its headers', async () => {
    installFetch();
    const user = await renderRoute('/sessions/session-a');
    expect(await screen.findByRole('heading', { name: 'Session detail' })).toBeVisible();

    const cacheSort = screen.getByRole('button', { name: 'Sort by Cache, descending' });
    await user.click(cacheSort);

    expect(cacheSort.closest('th')).toHaveAttribute('aria-sort', 'descending');
    const promptRows = within(cacheSort.closest('table')!).getAllByRole('row').slice(1);
    expect(within(promptRows[0]).getByText('Prompt content storage disabled')).toBeVisible();
    expect(within(promptRows[1]).getByText('Build the feature')).toBeVisible();
  });

  test('renders session evidence and navigates through prompts to prompt detail', async () => {
    installFetch();
    const user = await renderRoute('/sessions/session-a');
    expect(await screen.findByRole('heading', { name: 'Session detail' })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Source evidence' })).toBeVisible();
    expect(screen.getByText('Source + OTel')).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Token and model evidence' })).toBeVisible();
    expect(screen.getAllByText('-1.0m').some((element) => element.classList.contains('negative'))).toBe(true);

    await user.click(screen.getByRole('button', { name: /Level 3.*Prompts/ }));
    expect(await screen.findByRole('heading', { name: 'Prompts in this session' })).toBeVisible();
    expect(screen.getByText('Prompt content storage disabled')).toBeVisible();
    await user.click(screen.getByText('Build the feature'));
    expect(await screen.findByRole('heading', { name: 'Prompt detail' })).toBeVisible();
    expect(screen.getByText('VS Code turn log')).toBeVisible();

    await user.click(screen.getByRole('link', { name: 'Prompts' }));
    expect(await screen.findByRole('heading', { name: 'Prompts in this session' })).toBeVisible();
    await user.click(screen.getByRole('link', { name: 'Overall' }));
    expect(await screen.findByRole('heading', { name: 'Overall Copilot value' })).toBeVisible();
  });

  test('renders disabled prompt content and OTel fallback details', async () => {
    installFetch();
    await renderRoute('/sessions/session-a/prompts/prompt-2');
    expect(await screen.findByRole('heading', { name: 'Prompt detail' })).toBeVisible();
    expect(screen.getByText(/Prompt content storage is disabled/)).toBeVisible();
    expect(screen.getByText('OTel trace fallback')).toBeVisible();
    expect(screen.getByText('Unknown')).toBeVisible();
  });

  test('renders an empty prompt chain', async () => {
    installFetch({ promptValues: [] });
    await renderRoute('/sessions/session-a/prompts');
    expect(await screen.findByText('No prompts have been indexed for this session.')).toBeVisible();
  });
});

describe('methodology and navigation', () => {
  test('keeps overflowed custom calibration totals unavailable and JSON-safe', () => {
    const result = calibratedOverview(createOverview('base', [createSession({
      retainedSourceCharacters: Number.MAX_VALUE
    })]), {
      loadedHourlyRateUsd: Number.MAX_VALUE,
      capacityRealization: 0.5,
      typingWordsPerMinute: 52,
      charactersPerWord: 5,
      scenarios: {
        pessimistic: pessimisticCalibration,
        base: calibration,
        optimistic: optimisticCalibration
      }
    }, 'base');

    expect(result.totals.estimatedManualMinutes).toBe(0);
    expect(result.totals.estimatedManualLaborCostUsd).toBe(0);
    expect(result.totals.netValueUsd).toBe(0);
    expect(result.totals.taskTimeReduction).toBeNull();
    expect(result.totals.roi).toBeNull();
    expect(result.modelingStatus).toBe('invalid');
    for (const value of Object.values(result.totals)) {
      if (typeof value === 'number') expect(Number.isFinite(value)).toBe(true);
    }
  });

  test('rejects nonnumeric custom calibration and session evidence at runtime', () => {
    const malformedDraft = {
      loadedHourlyRateUsd: '92',
      capacityRealization: 0.5,
      typingWordsPerMinute: 52,
      charactersPerWord: 5,
      scenarios: {
        pessimistic: pessimisticCalibration,
        base: calibration,
        optimistic: optimisticCalibration
      }
    } as unknown as Parameters<typeof calibratedOverview>[1];
    const malformedSessions = [true, null, undefined].map((allocatedSeconds) => createSession({
        usage: {
          ...createSession().usage,
          phases: {
            ...createSession().usage.phases,
            planning: {
              ...createSession().usage.phases!.planning,
              allocatedSeconds: allocatedSeconds as unknown as number
            }
          }
        }
      }));
    malformedSessions.push(createSession({
      benchmark: true as unknown as Session['benchmark']
    }));
    malformedSessions.push(createSession({
      usage: {
        ...createSession().usage,
        phases: {
          ...createSession().usage.phases,
          planning: {
            ...createSession().usage.phases!.planning,
            modelSpans: 0,
            outputTokens: 1
          }
        }
      }
    }));
    malformedSessions.push(createSession({
      usage: {
        ...createSession().usage,
        phases: {
          ...createSession().usage.phases,
          research: {
            ...createSession().usage.phases!.research,
            toolActiveSeconds: 755
          }
        }
      }
    }));
    malformedSessions.push(createSession({
      aiCredits: 0,
      aiCostUsd: 1e-12,
      usage: createUsage(754, 450, 0, 1e-12)
    }));

    expect(completeWorkerUsage(createSession())).toBe(true);
    for (const session of malformedSessions.slice(-3)) {
      expect(completeWorkerUsage(session)).toBe(false);
    }

    for (const result of [
      calibratedOverview(createOverview('base', [createSession()]), malformedDraft, 'base'),
      ...malformedSessions.map((session) => calibratedOverview(
        createOverview('base', [session]), validCalibrationDraft, 'base'
      ))
    ]) {
      expect(result.totals.estimatedManualMinutes).toBe(0);
      expect(result.totals.netValueUsd).toBe(0);
      expect(result.totals.taskTimeReduction).toBeNull();
      expect(result.totals.roi).toBeNull();
      expect(result.modelingStatus).toBe('invalid');
    }
  });

  test('drops malformed saved calibrations instead of coercing them', () => {
    const valid = {
      id: 'valid',
      name: 'Valid profile',
      updatedAt: '2026-08-05T12:00:00Z',
      customScenario: 'base',
      draft: validCalibrationDraft
    };
    window.localStorage.setItem(savedCalibrationStorageKey, JSON.stringify([
      valid,
      { ...valid, id: 'string-rate', draft: { ...validCalibrationDraft, loadedHourlyRateUsd: '92' } },
      { ...valid, id: 'boolean-capacity', draft: { ...validCalibrationDraft, capacityRealization: true } },
      { ...valid, id: 'missing-scenario', draft: { ...validCalibrationDraft, scenarios: { base: calibration } } },
      { ...valid, id: 'invalid-custom-scenario', customScenario: 'other' },
      { ...valid, id: 'empty-capacity-band', draft: { ...validCalibrationDraft, capacityRealizationBand: [] } },
      { ...valid, id: 'invalid-capacity-band', draft: { ...validCalibrationDraft, capacityRealizationBand: [0.5, 1.1] } },
      {
        ...valid,
        id: 'invalid-reasoning-weight',
        draft: {
          ...validCalibrationDraft,
          scenarios: {
            ...validCalibrationDraft.scenarios,
            base: {
              ...calibration,
              planning: { ...calibration.planning, reasoningTokenWeight: 2 }
            }
          }
        }
      },
      {
        ...valid,
        id: 'unordered-reasoning-weight',
        draft: {
          ...validCalibrationDraft,
          scenarios: {
            pessimistic: {
              ...pessimisticCalibration,
              planning: { ...pessimisticCalibration.planning, reasoningTokenWeight: 0.5 }
            },
            base: {
              ...calibration,
              planning: { ...calibration.planning, reasoningTokenWeight: 0.25 }
            },
            optimistic: {
              ...optimisticCalibration,
              planning: { ...optimisticCalibration.planning, reasoningTokenWeight: 0.75 }
            }
          }
        }
      }
    ]));

    expect(loadSavedCalibrations().map((profile) => profile.id)).toEqual(['valid']);
  });

  test('shows invalid custom modeling while preserving observed portfolio evidence', async () => {
    const saved = {
      id: 'runtime-profile',
      name: 'Runtime profile',
      updatedAt: '2026-08-05T12:00:00Z',
      customScenario: 'base',
      draft: validCalibrationDraft
    };
    window.localStorage.setItem(savedCalibrationStorageKey, JSON.stringify([saved]));
    window.localStorage.setItem(activeCalibrationStorageKey, saved.id);
    const malformedSession = createSession({
      usage: {
        ...createSession().usage,
        phases: {
          ...createSession().usage.phases,
          planning: {
            ...createSession().usage.phases!.planning,
            allocatedSeconds: null as unknown as number
          }
        }
      }
    });
    installFetch({ overviewSessions: [malformedSession] });
    const user = await renderRoute('/');
    await screen.findByRole('heading', { name: 'Overall Copilot value' });

    await user.click(screen.getByRole('button', { name: 'Custom' }));

    expect(await screen.findByRole('status')).toHaveTextContent('Modeled values are unavailable');
    const manualTime = screen.getByText('Manual-only time').closest('.kpi');
    const assistedTime = screen.getByText('AI-assisted time').closest('.kpi');
    expect(manualTime).not.toBeNull();
    expect(assistedTime).not.toBeNull();
    expect(within(manualTime as HTMLElement).getByText('—')).toBeVisible();
    expect(within(assistedTime as HTMLElement).getByText('7.5m')).toBeVisible();
    expect(screen.getByText('session-a')).toBeVisible();
    expect(screen.getAllByText('$1.00').length).toBeGreaterThan(0);
  });

  test('withholds preset modeled values when the API reports invalid modeling', async () => {
    installFetch({ overviewStatus: 'invalid' });
    await renderRoute('/');

    expect(await screen.findByRole('status')).toHaveTextContent(
      'selected model inputs or session evidence are invalid'
    );
    const manualTime = screen.getByText('Manual-only time').closest('.kpi');
    expect(manualTime).not.toBeNull();
    expect(within(manualTime as HTMLElement).getByText('—')).toBeVisible();
    const sessionRow = screen.getByText('session-a').closest('tr');
    expect(sessionRow).not.toBeNull();
    expect(within(sessionRow as HTMLElement).getAllByText('—').length).toBeGreaterThanOrEqual(3);
    const modeledCoverage = screen.getByText('Sessions with modeled ROI').closest('div');
    expect(modeledCoverage).not.toBeNull();
    expect(within(modeledCoverage as HTMLElement).getByText('0 / 2')).toBeVisible();
    const cohortPanel = screen.getByRole('heading', { name: 'ROI by model cohort' }).closest('.panel');
    expect(cohortPanel).not.toBeNull();
    const cohortRow = within(cohortPanel as HTMLElement).getByText('model-a').closest('tr');
    expect(cohortRow).not.toBeNull();
    expect(within(cohortRow as HTMLElement).getByText('0 / 2')).toBeVisible();
    expect(within(cohortRow as HTMLElement).getAllByText('—')).toHaveLength(3);
    expect(screen.getAllByText('$1.00').length).toBeGreaterThan(0);
  });

  test('withholds invalid preset modeling throughout session detail', async () => {
    const invalidSession = createSession({
      modelingStatus: 'invalid',
      usage: { ...createSession().usage, engagedSeconds: 0 },
      scenarioResult: scenarioResult(1),
      benchmark: {
        scenarios: {
          pessimistic: scenarioResult(0.5),
          base: scenarioResult(1),
          optimistic: scenarioResult(2)
        }
      }
    });
    installFetch({ sessionValue: invalidSession });
    await renderRoute('/sessions/session-a');

    expect(await screen.findByRole('status')).toHaveTextContent(
      'selected model inputs or session evidence are invalid'
    );
    const manualTime = screen.getByText('Manual-only time').closest('.kpi');
    const assistedTime = screen.getByText('AI-assisted time').closest('.kpi');
    expect(manualTime).not.toBeNull();
    expect(assistedTime).not.toBeNull();
    expect(within(manualTime as HTMLElement).getByText('—')).toBeVisible();
    expect(within(assistedTime as HTMLElement).getByText('0.0m')).toBeVisible();
    const baseRow = screen.getByText('Base', { selector: 'td' }).closest('tr');
    expect(baseRow).not.toBeNull();
    expect(within(baseRow as HTMLElement).getAllByText('—').length).toBeGreaterThan(0);
    const phasePanel = screen.getByRole('heading', { name: 'Base phase impact' }).closest('.panel');
    expect(phasePanel).not.toBeNull();
    expect(within(phasePanel as HTMLElement).getAllByText('—').length).toBeGreaterThan(0);
    const sourcePanel = screen.getByRole('heading', { name: 'Source evidence' }).closest('.panel');
    expect(sourcePanel).not.toBeNull();
    expect(within(sourcePanel as HTMLElement).getByText('—')).toBeVisible();
    expect(screen.getAllByText('$1.00').length).toBeGreaterThan(0);
  });

  test('withholds custom modeled values when no modeled session is eligible', async () => {
    const saved = {
      id: 'unavailable-profile',
      name: 'Unavailable profile',
      updatedAt: '2026-08-05T12:00:00Z',
      customScenario: 'base',
      draft: validCalibrationDraft
    };
    window.localStorage.setItem(savedCalibrationStorageKey, JSON.stringify([saved]));
    window.localStorage.setItem(activeCalibrationStorageKey, saved.id);
    installFetch({ overviewSessions: [createSession({ aiCostUsd: 0, aiCredits: 0 })] });
    const user = await renderRoute('/');
    await screen.findByRole('heading', { name: 'Overall Copilot value' });

    await user.click(screen.getByRole('button', { name: 'Custom' }));

    expect(await screen.findByRole('status')).toHaveTextContent(
      'no eligible modeled session evidence is available'
    );
    const manualTime = screen.getByText('Manual-only time').closest('.kpi');
    expect(manualTime).not.toBeNull();
    expect(within(manualTime as HTMLElement).getByText('—')).toBeVisible();
    expect(screen.getByText('session-a')).toBeVisible();
  });

  test('keeps multi-session custom aggregation finite', () => {
    const extremeCalibration: BenchmarkScenario = {
      ...calibration,
      coding: { manualEntryFraction: 1, wordsPerMinute: 1 }
    };
    const validSession = createSession({
      retainedSourceCharacters: 9e307,
      startedAt: '2026-08-05T10:00:00Z',
      completedAt: '2026-08-05T10:10:00Z',
      durationSeconds: 600
    });
    const secondSession = createSession({
      experiment: 'session-b',
      retainedSourceCharacters: 9e307,
      startedAt: '2026-08-05T11:00:00Z',
      completedAt: '2026-08-05T12:00:00Z',
      durationSeconds: 3_600
    });
    const result = calibratedOverview(createOverview('base', [validSession, secondSession]), {
      loadedHourlyRateUsd: 1,
      capacityRealization: 0.5,
      typingWordsPerMinute: 1,
      charactersPerWord: 1,
      scenarios: {
        pessimistic: extremeCalibration,
        base: extremeCalibration,
        optimistic: extremeCalibration
      }
    }, 'base');

    expect(result.sessions.every((session) => session.scenarioResult !== null)).toBe(true);
    expect(result.totals.estimatedManualMinutes).toBe(0);
    expect(result.totals.taskTimeReduction).toBeNull();
    expect(result.totals.roi).toBeNull();
    for (const value of Object.values(result.totals)) {
      if (typeof value === 'number') expect(Number.isFinite(value)).toBe(true);
    }
  });

  test('preserves observed duration and excludes non-absolute bounds from custom modeling', () => {
    const validSession = createSession({
      startedAt: '2026-08-05T10:00:00Z',
      completedAt: '2026-08-05T10:01:00Z',
      durationSeconds: 60,
      usage: createUsage(60, 10)
    });
    const naiveSession = createSession({
      experiment: 'session-b',
      startedAt: '2026-08-05T11:00:00',
      completedAt: '2026-08-05T12:00:00Z',
      durationSeconds: 3_600
    });
    const dateOnlySession = createSession({
      experiment: 'session-c',
      startedAt: '2026-08-05Z',
      completedAt: '2026-08-05T13:00:00Z',
      durationSeconds: 3_600
    });
    const lowercaseZoneSession = createSession({
      experiment: 'session-d',
      startedAt: '2026-08-05T14:00:00z',
      completedAt: '2026-08-05T15:00:00Z',
      durationSeconds: 3_600
    });
    const underflowSession = createSession({
      experiment: 'session-e',
      startedAt: '0001-01-01T00:00:00+23:59',
      completedAt: '2026-08-05T16:00:00Z',
      durationSeconds: 3_600
    });
    const overflowSession = createSession({
      experiment: 'session-f',
      startedAt: '9999-12-31T23:59:59-23:59',
      completedAt: '9999-12-31T23:59:59Z',
      durationSeconds: 3_600
    });
    const overview = createOverview(
      'base', [
        validSession, naiveSession, dateOnlySession, lowercaseZoneSession,
        underflowSession, overflowSession
      ]
    );
    const result = calibratedOverview(overview, {
      loadedHourlyRateUsd: 60,
      capacityRealization: 0.5,
      typingWordsPerMinute: 52,
      charactersPerWord: 5,
      scenarios: {
        pessimistic: pessimisticCalibration,
        base: calibration,
        optimistic: optimisticCalibration
      }
    }, 'base');

    expect(result.modelingStatus).toBe('invalid');
    expect(result.totals.durationSeconds).toBe(overview.totals.durationSeconds);
    expect(result.totals.estimatedManualMinutes).toBe(0);
    expect(result.totals.estimatedAiAssistedLaborCostUsd).toBe(0);
    expect(result.totals.roi).toBeNull();
  });

  test('preserves explicit zero engagement in custom portfolio modeling', () => {
    const session = createSession({
      aiCostUsd: 0,
      aiCredits: 0,
      durationSeconds: 754,
      usage: createUsage(754, 0, 0, 0)
    });
    const result = calibratedOverview(
      createOverview('base', [session]), validCalibrationDraft, 'base'
    );

    expect(result.modelingStatus).toBe('unavailable');
    expect(result.totals.durationSeconds).toBe(754);
    expect(result.totals.estimatedAiAssistedLaborCostUsd).toBe(0);
    expect(result.totals.estimatedAiAssistedTotalCostUsd).toBe(0);
  });

  test('renders one category-grouped metric table with sources and operational signals', async () => {
    installFetch();
    await renderRoute('/insights');
    expect(await screen.findByRole('heading', { name: 'Session insights' })).toBeVisible();
    expect(screen.getByText(/authoritative settled-session usage/)).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Metrics and signals' })).toBeVisible();
    expect(screen.queryByRole('heading', { name: 'Evidence and workflow signals' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Usage measurements' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('table')).toHaveLength(1);
    expect(screen.queryByRole('columnheader', { name: 'Target' })).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Status' })).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Relevant basis' })).not.toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Signal' })).toBeVisible();
    expect(screen.getAllByText('4 eligible; median of latest 4 settled sessions').length).toBeGreaterThan(0);
    expect(screen.getByText('4 eligible; all are included')).toBeVisible();
    expect(screen.getByText(/Current values are medians of up to 5 eligible observations/)).toBeVisible();
    expect(screen.getByText('Output per request')).toBeVisible();
    for (const category of ['Context', 'Generation', 'Cost', 'Evidence']) {
      expect(screen.getByText(category, { selector: '.insight-group-row th' })).toBeVisible();
    }
    expect(screen.getByText('Danger', { selector: '.insight-signal' })).toBeVisible();
    expect(screen.getAllByText('No signal', { selector: '.insight-signal' }).length).toBeGreaterThan(0);
    expect(screen.getByText('Not rated', { selector: '.insight-signal' })).toBeVisible();
    expect(screen.getByText('Low below 50.0% · danger below 20.0%')).toBeVisible();
    expect(screen.getByText('High above 2,000 · danger above 8,000')).toBeVisible();
    const source = screen.getByRole('link', { name: /OpenAI prompt caching/ });
    expect(source).toHaveAttribute('href', 'https://developers.openai.com/api/docs/guides/prompt-caching');
    expect(source.closest('td')).toHaveTextContent('Source:');
    expect(screen.getByRole('button', { name: 'Methodology' })).toBeVisible();
    expect(screen.queryByRole('button', { name: /Open latest observation/ })).not.toBeInTheDocument();
  });

  test('withholds Insights when settled session usage is incomplete', async () => {
    installFetch({ insightsValue: degradedInsights });
    await renderRoute('/insights');

    expect(await screen.findByRole('status')).toHaveTextContent('Integrity gate failed: 3 of 4');
    expect(screen.getByText(/Every settled session must carry the complete authoritative usage contract/)).toBeVisible();
    const cacheRow = screen.getByText('Session cache reuse').closest('tr');
    expect(cacheRow).not.toBeNull();
    expect(within(cacheRow!).getByText('Withheld', { selector: '.insight-signal' })).toBeVisible();
    expect(within(cacheRow!).getByText('Value withheld by integrity gate')).toBeVisible();
    expect(within(cacheRow!).getByText('3 complete observations; value withheld by integrity gate')).toBeVisible();
    expect(within(cacheRow!).queryByText(/median of latest/)).not.toBeInTheDocument();
    expect(within(cacheRow!).getByText('Withheld: every settled session must carry complete authoritative usage.')).toBeVisible();
    const coverageRow = screen.getByText('Sessions with complete usage').closest('tr');
    expect(coverageRow).not.toBeNull();
    expect(within(coverageRow!).getByText('75.0%')).toBeVisible();
    expect(within(coverageRow!).getByText('Danger', { selector: '.insight-signal' })).toBeVisible();
    expect(within(coverageRow!).getByText('Low below 100.0% · danger below 95.0%')).toBeVisible();
  });

  test('renders formulas, configured tool patterns, and variable help', async () => {
    installFetch();
    const user = await renderRoute('/methodology');
    expect(await screen.findByRole('heading', { name: 'How we measure ROI' })).toBeVisible();
    expect(screen.getByText('apply_patch')).toBeVisible();
    expect(screen.getByText('No matching configured pattern')).toBeVisible();
    expect(screen.getByText('Base selected')).toBeVisible();
    expect(screen.getByText(/There is no separate without-AI stopwatch/)).toBeVisible();
    expect(screen.getByText(/At the current 50% setting/)).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Calibration workspace' })).toBeVisible();
    expect(screen.getByText('Reasoning tokens counted (P/V)')).toBeVisible();
    expect(screen.queryByRole('spinbutton', { name: 'Base research reasoning token weight' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Open source' })[0]).toHaveAttribute('href', 'https://arxiv.org/abs/2302.06590');
    const orderedHeadings = [
      screen.getByRole('heading', { name: 'Measure the work' }),
      screen.getByRole('heading', { name: 'Sort the work' }),
      screen.getByRole('heading', { name: 'Estimate manual time' }),
      screen.getByRole('heading', { name: 'Compare value with cost' }),
      screen.getByRole('heading', { name: 'Scenario assumption matrix' }),
      screen.getByRole('heading', { name: 'Calibration workspace' })
    ];
    for (let index = 0; index < orderedHeadings.length - 1; index += 1) {
      expect(orderedHeadings[index].compareDocumentPosition(orderedHeadings[index + 1]) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    }
    const phaseHandoff = screen.getByLabelText('Step 2 output becomes step 3 input');
    expect(phaseHandoff).toHaveTextContent('T_AI,p');
    expect(phaseHandoff).toHaveTextContent('T_manual,p,s');
    const phaseFormulaRows = document.querySelectorAll('.phase-formula-table tbody tr');
    expect(phaseFormulaRows[0]).toHaveTextContent('T_manual,planning,s =');
    expect(phaseFormulaRows[4]).toHaveTextContent('T_manual,unclassified,s =');
    expect(screen.getByLabelText('Step 3 output passed to step 4')).toHaveTextContent('T_manual,s = sum_p(T_manual,p,s)');
    const valueTree = screen.getByLabelText('Value formula dependency tree');
    expect(valueTree).toBeVisible();
    expect(valueTree).toHaveTextContent('From step 3');
    expect(valueTree).toHaveTextContent('T_saved,p,s = T_manual,p,s - T_AI,p');

    const saveCalibration = screen.getByRole('button', { name: 'Save calibration' });
    await user.click(saveCalibration);
    const profileBar = saveCalibration.closest('.calibration-profile-bar');
    expect(profileBar).not.toBeNull();
    expect(within(profileBar!).getByRole('alert')).toHaveTextContent('Enter a calibration name before saving.');

    const planningFraction = screen.getByRole('spinbutton', { name: 'Base planning relevant token fraction' });
    await user.clear(planningFraction);
    await user.type(planningFraction, '0.3');
    await user.type(screen.getByRole('textbox', { name: 'Calibration name' }), 'Backend bug fixes');
    await user.click(saveCalibration);
    expect(screen.getByRole('status')).toHaveTextContent('Preset values were not changed');
    expect(JSON.parse(window.localStorage.getItem('algalon.savedCalibrations.v1') ?? '[]')).toHaveLength(1);

    await user.click(screen.getByRole('button', { name: 'New from presets' }));
    expect(planningFraction).toHaveValue(0.25);
    const savedCalibration = screen.getByRole('combobox', { name: 'Saved calibration' });
    await user.selectOptions(savedCalibration, within(savedCalibration).getByRole('option', { name: 'Backend bug fixes' }));
    expect(planningFraction).toHaveValue(0.3);
    await user.click(screen.getByRole('button', { name: 'Copy config fragment' }));
    const copied = JSON.parse(await navigator.clipboard.readText());
    expect(copied.benchmark.scenarios.base.planning.relevantTokenFraction).toBe(0.3);
    expect(copied.benchmark.presetScenarios.base.planning.relevantTokenFraction).toBe(0.25);
    expect(copied.benchmark.calibrationSources[0].limitation).toContain('does not directly calibrate');

    await user.click(screen.getByRole('button', { name: 'Pessimistic' }));
    expect(screen.getByText('Pessimistic selected')).toBeVisible();
    const variable = screen.getAllByRole('term')[0];
    fireEvent.mouseEnter(variable);
    expect(await screen.findByRole('tooltip')).toBeInTheDocument();
    fireEvent.mouseLeave(variable);
    variable.focus();
    expect(await screen.findByRole('tooltip')).toBeInTheDocument();
    fireEvent.keyDown(variable, { key: 'Escape' });
  });

  test('uses safe fallbacks when optional methodology configuration is absent', async () => {
    installFetch({
      methodologyValue: {
        claim: 'Modeled AI Usage ROI',
        scenarios: ['pessimistic', 'base', 'optimistic'],
        config: {},
        formulas: { custom: 'plain text' }
      }
    });
    await renderRoute('/methodology');
    expect((await screen.findAllByText('Configuration unavailable')).length).toBe(5);
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  test('keeps source maintenance out of calibration and shows base evidence gaps', async () => {
    installFetch();
    await renderRoute('/methodology');
    await screen.findByRole('heading', { name: 'Calibration workspace' });
    expect(screen.queryByText('Add calibration source')).not.toBeInTheDocument();
    expect(screen.getByText('Curated source register')).toBeVisible();
    expect(screen.getByText('10 cited')).toBeVisible();
    expect(screen.getByRole('spinbutton', { name: 'Loaded labor rate' })).toHaveValue(92);
    expect(screen.getByRole('spinbutton', { name: 'Audit typing rate' })).toHaveValue(52);
    const anchorSummary = screen.getByText('Research-anchored shipped defaults').closest('.research-anchor-summary');
    expect(anchorSummary).not.toBeNull();
    expect(within(anchorSummary!).getByRole('link', { name: 'BLS wages' })).toHaveAttribute('href', '#calibration-source-6');
    expect(within(anchorSummary!).getByRole('link', { name: 'typing study' })).toHaveAttribute('href', '#calibration-source-5');
    expect(within(anchorSummary!).getByRole('link', { name: 'Copilot interaction study' })).toHaveAttribute('href', '#calibration-source-9');
    expect(screen.getAllByText('Local evidence needed')).toHaveLength(4);
    const evidenceRegister = screen.getByText('Base input evidence coverage').closest('.base-evidence-register');
    expect(evidenceRegister).not.toBeNull();
    const reasoningWeightRow = within(evidenceRegister!).getByText('Reasoning token weight (P/V)').closest('tr');
    expect(reasoningWeightRow).not.toBeNull();
    expect(within(reasoningWeightRow!).getByText('Local evidence needed')).toBeVisible();
    const reviewRateRow = within(evidenceRegister!).getByText('Human review rate').closest('tr');
    expect(reviewRateRow).not.toBeNull();
    expect(within(reviewRateRow!).getByText('Proxy benchmark')).toBeVisible();
    const interactionRow = within(evidenceRegister!).getByText('Tool interaction overhead').closest('tr');
    expect(interactionRow).not.toBeNull();
    expect(within(interactionRow!).getByText('Proxy benchmark')).toBeVisible();
    expect(within(interactionRow!).getByRole('link', { name: '[9] ACM CHI' })).toHaveAttribute('href', '#calibration-source-9');
    const charactersRow = within(evidenceRegister!).getByText('Characters per word').closest('tr');
    expect(charactersRow).not.toBeNull();
    expect(within(charactersRow!).getByText('Direct support')).toBeVisible();
    const scenarioRow = within(evidenceRegister!).getByText('Overall scenario envelope').closest('tr');
    expect(scenarioRow).not.toBeNull();
    expect(within(scenarioRow!).getByText('Context only')).toBeVisible();
    expect(within(scenarioRow!).getByRole('link', { name: '[1] arXiv' })).toHaveAttribute('href', '#calibration-source-1');
    expect(within(scenarioRow!).getByRole('link', { name: '[3] METR' })).toHaveAttribute('href', '#calibration-source-3');
    expect(within(scenarioRow!).getByRole('link', { name: '[8] Management Science' })).toHaveAttribute('href', '#calibration-source-8');
    fireEvent.click(screen.getByRole('button', { name: 'Pessimistic' }));
    expect(screen.getByRole('spinbutton', { name: 'Pessimistic planning tool interaction overhead' })).toHaveValue(0.12);
  });

  test('applies a saved calibration to portfolio data and can return to presets', async () => {
    installFetch({ overviewSessions: [
      createSession({
        startedAt: '2026-08-05T10:00:00Z',
        completedAt: '2026-08-05T10:30:00Z',
        durationSeconds: 30 * 60
      }),
      createSession({
        experiment: 'session-b',
        startedAt: '2026-08-05T10:10:00Z',
        completedAt: '2026-08-05T10:40:00Z',
        durationSeconds: 30 * 60
      })
    ] });
    const user = await renderRoute('/methodology');
    await screen.findByRole('heading', { name: 'Calibration workspace' });
    const loadedRate = screen.getByRole('spinbutton', { name: 'Loaded labor rate' });
    await user.clear(loadedRate);
    await user.type(loadedRate, '240');
    await user.type(screen.getByRole('textbox', { name: 'Calibration name' }), 'High loaded rate');
    await user.click(screen.getByRole('button', { name: 'Save calibration' }));
    expect(screen.getByRole('button', { name: 'Custom' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Custom' }));

    await user.click(screen.getByRole('button', { name: /Level 1.*Overall/ }));
    expect(await screen.findByRole('heading', { name: 'Overall Copilot value' })).toBeVisible();
    const activeBand = document.querySelector('.active-calibration-band');
    expect(activeBand).not.toBeNull();
    expect(within(activeBand!).getByText('Custom · High loaded rate')).toBeVisible();
    const kpiGrid = document.querySelector('.kpi-grid');
    expect(kpiGrid).not.toBeNull();
    expect(within(kpiGrid as HTMLElement).getByText('15.0m')).toBeVisible();
    let sessionRow = screen.getByText('session-a').closest('tr');
    expect(sessionRow).not.toBeNull();
    expect(within(sessionRow!).getByText('7.5m')).toBeVisible();
    expect(within(sessionRow!).getByText('$1.00')).toBeVisible();
    expect(within(sessionRow!).getByText('-6.45x')).toBeVisible();

    await user.click(screen.getByRole('button', { name: 'Base' }));
    expect(document.querySelector('.active-calibration-band')).toBeNull();
    sessionRow = screen.getByText('session-a').closest('tr');
    expect(within(sessionRow!).getByText('+7.00x')).toBeVisible();

    await user.click(screen.getByRole('button', { name: 'Custom' }));
    await user.click(screen.getByRole('button', { name: 'Methodology' }));
    await screen.findByRole('heading', { name: 'Calibration workspace' });
    await user.click(screen.getByRole('button', { name: 'Delete saved calibration' }));
    expect(screen.getByRole('button', { name: 'Base' })).toHaveClass('active');
    expect(screen.getByRole('button', { name: 'Custom' })).toBeDisabled();
  });

  test('responds to browser history and brand navigation', async () => {
    installFetch();
    const user = await renderRoute('/');
    expect(await screen.findByRole('heading', { name: 'Overall Copilot value' })).toBeVisible();
    window.history.pushState({}, '', '/methodology');
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect(await screen.findByRole('heading', { name: 'Methodology and formula' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Algalon' }));
    expect(await screen.findByRole('heading', { name: 'Overall Copilot value' })).toBeVisible();
  });
});