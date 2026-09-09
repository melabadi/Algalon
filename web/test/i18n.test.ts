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
});
