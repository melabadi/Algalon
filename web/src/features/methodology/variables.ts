export type VariableSource = 'Observed' | 'Deterministic' | 'Configured' | 'Modeled';

export interface VariableDefinition {
  symbol: string;
  aliases?: string[];
  unit: string;
  source: VariableSource;
  definition: string;
}

export const economicVariables: VariableDefinition[] = [
  { symbol: 'p', unit: 'phase', source: 'Deterministic', definition: 'Planning, research, coding, validation, or unclassified work.' },
  { symbol: 'sum_p', unit: 'operation', source: 'Deterministic', definition: 'Sum the expression across every work phase p.' },
  { symbol: 's', unit: 'scenario', source: 'Configured', definition: 'Pessimistic, base, or optimistic sensitivity calibration.' },
  { symbol: 'T_AI,p', aliases: ['T_AI,unclassified'], unit: 'minutes', source: 'Deterministic', definition: 'Observed session engaged time allocated to the named phase using overlap-safe activity shares.' },
  { symbol: 'T_manual,p,s', aliases: ['T_manual,planning,s', 'T_manual,research,s', 'T_manual,coding,s', 'T_manual,validation,s', 'T_manual,unclassified,s'], unit: 'minutes', source: 'Modeled', definition: 'Manual-time estimate for phase p under scenario s, calculated from phase evidence and configured rates; it is not directly timed.' },
  { symbol: 'T_saved,p,s', unit: 'minutes', source: 'Modeled', definition: 'Phase savings: T_manual,p,s - T_AI,p. Negative values are retained.' },
  { symbol: 'T_saved,s', unit: 'minutes', source: 'Modeled', definition: 'Total modeled savings summed across all five phases.' },
  { symbol: 'T_manual,s', unit: 'minutes', source: 'Modeled', definition: 'Total modeled manual-only duration: the sum of the five phase estimates under scenario s.' },
  { symbol: 'T_AI', unit: 'minutes', source: 'Deterministic', definition: 'Total engaged session time, with overlapping activity counted once and idle beyond the configured gap excluded.' },
  { symbol: 'H', unit: 'USD/hour', source: 'Configured', definition: 'Fully loaded human labor rate.' },
  { symbol: 'rho', unit: 'ratio', source: 'Configured', definition: 'Capacity-realization fraction: the share of modeled saved labor assumed to become economic value. A value of 0.50 means 50%.' },
  { symbol: 'copilot_usage_nano_aiu', unit: 'nano-AIU', source: 'Observed', definition: 'Atomic local AI usage value emitted by Copilot telemetry.' },
  { symbol: 'C_AI', unit: 'USD', source: 'Observed', definition: 'Local AI usage value: sum(nano_aiu) / 10^11.' },
  { symbol: 'C_manual,s', unit: 'USD', source: 'Modeled', definition: 'Manual-only labor cost: (T_manual,s / 60) x H.' },
  { symbol: 'C_assisted', unit: 'USD', source: 'Modeled', definition: 'Measured AI-assisted labor time at H, plus observed AI usage.' },
  { symbol: 'Delta_C_gross,s', unit: 'USD', source: 'Modeled', definition: 'Manual-only cost minus AI-assisted delivery cost, before capacity realization.' },
  { symbol: 'R_s', unit: 'ratio', source: 'Modeled', definition: 'Delivery-cost reduction: Delta_C_gross,s / C_manual,s. Bounded above by 1 and unaffected by how cheap the model is.' },
  { symbol: 'B_s', unit: 'USD', source: 'Modeled', definition: 'Realized labor benefit: (T_saved,s / 60) x H x rho.' },
  { symbol: 'ROI_s', unit: 'ratio', source: 'Modeled', definition: 'Net benefit divided by observed AI credit spend: (B_s - C_AI) / C_AI. The denominator excludes labor and seat cost, so read it beside net value and break-even time.' }
];

export const phaseVariables: VariableDefinition[] = [
  { symbol: 'W', unit: 'seconds', source: 'Observed', definition: 'Full session wall-clock duration between authoritative start and end timestamps. Published as evidence; it is no longer the allocation base.' },
  { symbol: 'W_engaged', unit: 'seconds', source: 'Deterministic', definition: 'Span activity joined across gaps at or under G, with longer idle discarded. This is the duration phase allocation distributes.' },
  { symbol: 'G', unit: 'seconds', source: 'Configured', definition: 'Maximum idle gap treated as think time rather than absence. Operating policy, not a measured constant.' },
  { symbol: 'delta', unit: 'ratio', source: 'Deterministic', definition: 'Activity density: A_active / W. How much of the session window was directly observed rather than inferred.' },
  { symbol: 'j', unit: 'segment', source: 'Deterministic', definition: 'One non-overlapping interval between adjacent OTel span boundaries.' },
  { symbol: 'd_j', unit: 'seconds', source: 'Observed', definition: 'Wall-clock duration of segment j.' },
  { symbol: 'n_j', unit: 'count', source: 'Deterministic', definition: 'Number of distinct work phases active during segment j.' },
  { symbol: 'a_p', unit: 'seconds', source: 'Deterministic', definition: 'Overlap-safe active time assigned to phase p.' },
  { symbol: 'A_active', unit: 'seconds', source: 'Deterministic', definition: 'Total overlap-safe active time across all phases: sum_p(a_p).' },
  { symbol: 'X', unit: 'phase', source: 'Deterministic', definition: 'A shorthand for planning P, research R, or validation V.' },
  { symbol: 'alpha_X,s', aliases: ['alpha_P,s', 'alpha_R,s', 'alpha_V,s', 'alpha_P/R/V,s'], unit: 'ratio', source: 'Configured', definition: 'Fraction of phase tokens treated as relevant manual review work.' },
  { symbol: 'omega_P/V,s', aliases: ['omega_P,s', 'omega_V,s'], unit: 'ratio', source: 'Configured', definition: 'Share of planning or validation reasoning tokens entering the review term. Research uses uncached input tokens instead.' },
  { symbol: 'O_P, O_V', aliases: ['O_P', 'O_V'], unit: 'tokens', source: 'Observed', definition: 'Output tokens attributed to planning or validation.' },
  { symbol: 'Q_P, Q_V', aliases: ['Q_P', 'Q_V'], unit: 'tokens', source: 'Observed', definition: 'Reasoning tokens attributed to planning or validation.' },
  { symbol: 'U_R', unit: 'tokens', source: 'Observed', definition: 'Uncached input tokens attributed to research; cached context is excluded.' },
  { symbol: 'v_X,s', aliases: ['v_P,s', 'v_R,s', 'v_V,s', 'v_P/R/V,s'], unit: 'tokens/minute', source: 'Configured', definition: 'Human token-review rate for the named phase under scenario s.' },
  { symbol: 'N_X', aliases: ['N_P', 'N_R', 'N_V'], unit: 'executions', source: 'Observed', definition: 'Tool executions classified into the named phase.' },
  { symbol: 'tau_X,s', aliases: ['tau_P,s', 'tau_R,s', 'tau_V,s', 'tau_P/R/V,s'], unit: 'minutes/execution', source: 'Configured', definition: 'Manual interaction overhead per tool execution in the named phase.' },
  { symbol: 'f_s', unit: 'ratio', source: 'Configured', definition: 'Fraction of retained source assumed to require manual entry.' },
  { symbol: 'C', unit: 'characters', source: 'Deterministic', definition: 'Matched source characters added or modified and retained at session completion.' },
  { symbol: 'c', unit: 'characters/word', source: 'Configured', definition: 'Source-character to word conversion.' },
  { symbol: 'w_s', unit: 'words/minute', source: 'Configured', definition: 'Manual source-entry rate under scenario s.' },
  { symbol: 'D_V', unit: 'minutes', source: 'Deterministic', definition: 'Validation tool-active time after overlapping validation intervals are merged.' },
  { symbol: 'm_s', unit: 'multiplier', source: 'Configured', definition: 'Manual-time multiplier applied to measured unclassified engaged time.' }
];

const variableBySymbol = new Map<string, VariableDefinition>();
for (const variable of [...economicVariables, ...phaseVariables]) {
  for (const symbol of [variable.symbol, ...(variable.aliases ?? [])]) variableBySymbol.set(symbol, variable);
}
const symbols = [...variableBySymbol.keys()].sort((left, right) => right.length - left.length);

function isIdentifierCharacter(value: string | undefined): boolean {
  return Boolean(value && /[A-Za-z0-9_]/.test(value));
}

export function matchingVariable(expression: string, index: number): [string, VariableDefinition] | null {
  const symbol = symbols.find((candidate) => expression.startsWith(candidate, index)
    && !isIdentifierCharacter(expression[index - 1])
    && !isIdentifierCharacter(expression[index + candidate.length]));
  return symbol ? [symbol, variableBySymbol.get(symbol)!] : null;
}
