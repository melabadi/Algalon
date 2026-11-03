import { describe, expect, test } from 'vitest';
import { messagesFor } from '../src/i18n';


describe('message catalogs', () => {
  test('selects exact and base locales with an English fallback', () => {
    expect(messagesFor('en-US').shell.brand).toBe('Algalon');
    expect(messagesFor('fr-FR').shell.localEvidenceCurrent).toBe('Local evidence current');
  });

  test('keeps dynamic accessibility messages in the catalog', () => {
    const messages = messagesFor('en');
    expect(messages.a11y.sortBy('Cache', 'descending')).toBe('Sort by Cache, descending');
    expect(messages.shell.level(3)).toBe('Level 3');
  });

  test('describes Copilot runtime sessions and text-less prompts', () => {
    const messages = messagesFor('en');
    expect(messages.sessions.surfaces).toEqual({
      cli: 'CLI', vscode: 'VS Code', sdk: 'SDK', app: 'Copilot app', other: 'Other', mixed: 'Mixed'
    });
    expect(messages.sessions.automatedOverviewNote(1, '$0.42')).toBe('1 automated session excluded from modeled value · $0.42 observed AI usage');
    expect(messages.sessions.automatedOverviewNote(3, '$1.20')).toBe('3 automated sessions excluded from modeled value · $1.20 observed AI usage');
    expect(messages.prompts.messageLabel('12')).toBe('Message 12');
    expect(messages.prompts.textUnavailable).toBe('Prompt text is not captured from Copilot runtime telemetry. Each row is one user message with its model requests, tool calls, tokens and AI credits.');
    expect(messages.metricHelp.observedAiUsage).toContain('github.copilot.nano_aiu');
    expect(messages.metricHelp.observedAiUsage).not.toMatch(/VS Code|fallback/);
  });
});
