export const en = {
  locale: 'en',
  shell: {
    brand: 'Algalon',
    localEvidenceCurrent: 'Local evidence current',
    scenario: 'Scenario',
    customScenario: (name: string) => `Scenario · Custom: ${name}`,
    roiScenario: 'ROI scenario',
    chooseCalibration: 'Choose a saved calibration in Methodology first.',
    timeRange: 'Time range',
    autoRefreshInterval: 'Auto-refresh interval',
    ranges: [
      { days: 7, label: 'Last 7 days' },
      { days: 30, label: 'Last 30 days' },
      { days: 90, label: 'Last 90 days' },
      { days: 365, label: 'Last year' }
    ],
    drillDownHierarchy: 'Drill-down hierarchy',
    level: (value: number) => `Level ${value}`,
    levels: {
      overall: 'Overall',
      session: 'Session',
      prompts: 'Prompts',
      promptDetail: 'Prompt detail'
    },
    insights: 'Insights',
    methodology: 'Methodology'
  },
  controls: {
    refreshOptions: [
      { seconds: 0, label: 'Auto-refresh off' },
      { seconds: 5, label: 'Refresh every 5s' },
      { seconds: 15, label: 'Refresh every 15s' },
      { seconds: 30, label: 'Refresh every 30s' },
      { seconds: 60, label: 'Refresh every minute' }
    ],
    exportAllData: 'Export sessions + prompts',
    retryNow: 'Retry now'
  },
  state: {
    loadingEvidence: 'Loading local evidence…',
    unableToLoadEvidence: (message: string) => `Unable to load local evidence: ${message}`,
    attributionGap: 'Attribution gap'
  },
  a11y: {
    sortBy: (label: string, direction: string) => `Sort by ${label}, ${direction}`,
    explain: (label: string) => `Explain ${label}`,
    comparisonEvidence: 'Comparison evidence'
  },
  badges: {
    observed: 'Observed directly from local evidence.',
    derived: 'Calculated deterministically from observed evidence.',
    modeled: 'Scenario-sensitive estimate using configured assumptions.',
    gap: 'Unavailable until the required attribution evidence exists.',
    watch: 'Near or modestly outside a fixed reference.',
    action: 'Materially outside a fixed reference.'
  },
  metricHelp: {
    manualOnlyTime: 'Modeled time a person would need without AI under the selected scenario. It is a counterfactual estimate, not observed time.',
    aiAssistedTime: 'Observed engaged time: span activity plus think-time gaps shorter than the configured idle threshold. Longer idle is excluded, and concurrent sessions count once in portfolio totals.',
    sessionWindow: 'Elapsed wall clock from the first to the last span in the session, before idle longer than the configured threshold is removed.',
    timeGained: 'Manual-only time minus AI-assisted time. A negative value means the AI-assisted session took longer than the modeled manual baseline.',
    manualOnlyCost: 'Manual-only time multiplied by the configured fully loaded hourly labor rate.',
    aiAssistedTotal: 'Unique AI-assisted wall-clock time valued at the loaded rate, plus all observed AI-credit usage.',
    realizedNetGain: 'Capacity-adjusted value of time gained minus observed AI usage. This is the numerator used for ROI.',
    timeReduction: 'Time gained divided by manual-only time. 58.8% means AI-assisted elapsed time was 58.8% lower than the modeled manual baseline.',
    aiLaborEquivalent: 'Observed AI-assisted time multiplied by the loaded hourly rate. This is developer labor during the session, not Copilot billing.',
    observedAiUsage: 'Direct VS Code turn credits converted at $0.01 per credit when available, with OTel trace usage as fallback.',
    grossDeliverySavings: 'Manual-only labor cost minus AI-assisted labor and AI usage. Capacity realization is not applied to this comparison.',
    realizedLaborBenefit: 'Time gained multiplied by the loaded hourly rate and the configured capacity-realization factor.',
    deliveryCostReduction: 'Gross delivery savings divided by modeled manual-only cost. Bounded at 100% and unaffected by how cheap the model is.',
    returnOnAiCost: 'Realized net gain divided by observed AI credit spend only. The denominator excludes labor and seat cost, so this ratio rises when a cheaper model is used for the same work. Read it beside net gain and break-even time.',
    breakEvenManualTime: 'Modeled manual duration required for zero return. Ask whether the same work would credibly have taken at least this long without AI.'
  },
  calibration: {
    invalidGlobalRates: 'Global rates must be positive and capacity realization must be at most 1.',
    invalidPhase: (scenario: string, phase: string) => `${scenario} ${phase} assumptions are outside their valid ranges.`,
    invalidCoding: (scenario: string) => `${scenario} coding and unclassified assumptions are outside their valid ranges.`,
    unorderedPhase: (phase: string) => `${phase} assumptions must stay ordered from pessimistic through optimistic.`,
    unorderedCoding: 'Coding and unclassified assumptions must stay ordered from pessimistic through optimistic.'
  },
  evidenceClasses: {
    controlled_experiment: 'Controlled experiment',
    literature_benchmark: 'Literature benchmark',
    official_statistic: 'Official statistic',
    survey_context: 'Survey context',
    local_measurement: 'Local measurement',
    other: 'Other evidence'
  }
} as const;

type WidenCatalog<T> =
  T extends (...arguments_: infer Arguments) => string
    ? (...arguments_: Arguments) => string
    : T extends readonly (infer Item)[]
      ? ReadonlyArray<WidenCatalog<Item>>
      : T extends object
        ? { readonly [Key in keyof T]: WidenCatalog<T[Key]> }
        : T extends string
          ? string
          : T extends number
            ? number
            : T;

export type Messages = WidenCatalog<typeof en>;
