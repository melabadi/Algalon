/** Algalon-owned resource attributes that FastAPI ingestion stamps on every accepted Copilot runtime span. */
export const sessionKeyAttribute = 'algalon.session.key';
export const surfaceAttribute = 'algalon.source.surface';
export const modeAttribute = 'algalon.session.mode';

export type SessionSurface = 'cli' | 'vscode' | 'sdk' | 'app' | 'other' | 'mixed';
export type SessionMode = 'interactive' | 'automated';
export type SpanOperation = 'invoke_agent' | 'chat' | 'execute_tool';

const singleSurfaces = new Set<SessionSurface>(['cli', 'vscode', 'sdk', 'app', 'other']);

/**
 * Delegation and wait tools whose span covers nested subagent work that is already captured as its own
 * spans, so they never contribute phase time or tool interactions.
 */
export const containerToolNames: ReadonlySet<string> = new Set(['task', 'read_agent', 'write_agent', 'list_agents']);

export function spanOperation(name: string | undefined, operation: string | undefined): SpanOperation | undefined {
  if (operation === 'invoke_agent' || operation === 'chat' || operation === 'execute_tool') return operation;
  if (operation) return undefined;
  const spanName = name ?? '';
  if (spanName === 'invoke_agent' || spanName.startsWith('invoke_agent ')) return 'invoke_agent';
  if (spanName.startsWith('chat ')) return 'chat';
  if (spanName.startsWith('execute_tool ')) return 'execute_tool';
  return undefined;
}

/** The runtime names shell spans `execute_tool powershell pwsh.exe`; the tool is the first token. */
export function toolNameFromSpan(name: string | undefined, toolName: string | undefined): string | undefined {
  if (toolName) return toolName;
  return /^execute_tool\s+(\S+)/.exec(name ?? '')?.[1];
}

export function identifierTokens(value: string): string[] {
  return value
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

/** Matches whole identifier tokens so short names such as `rg` never match `merge_pull_request`. */
export function toolMatchesPattern(toolName: string, pattern: string): boolean {
  const tool = identifierTokens(toolName);
  const wanted = identifierTokens(pattern);
  if (wanted.length === 0 || wanted.length > tool.length) return false;
  for (let index = 0; index + wanted.length <= tool.length; index += 1) {
    if (wanted.every((token, offset) => tool[index + offset] === token)) return true;
  }
  return false;
}

export function isContainerTool(toolName: string | undefined): boolean {
  return toolName !== undefined && containerToolNames.has(identifierTokens(toolName).join('_'));
}

export function parseSurface(value: string | undefined): SessionSurface {
  return value && singleSurfaces.has(value as SessionSurface) ? value as SessionSurface : 'other';
}

export function parseMode(value: string | undefined): SessionMode {
  return value === 'automated' ? 'automated' : 'interactive';
}

export function combinedSurface(surfaces: Iterable<string>): SessionSurface {
  const distinct = [...new Set(surfaces)];
  if (distinct.length === 0) return 'other';
  return distinct.length === 1 ? parseSurface(distinct[0]) : 'mixed';
}

/** A session is automated only when every observed span carried the explicit automated marker. */
export function combinedMode(modes: Iterable<string>): SessionMode {
  const distinct = [...new Set(modes)];
  return distinct.length > 0 && distinct.every((mode) => mode === 'automated') ? 'automated' : 'interactive';
}
