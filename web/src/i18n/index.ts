import { en, type Messages } from './en';

const catalogs: Record<string, Messages> = { en };

export function messagesFor(locale = navigator.language): Messages {
  return catalogs[locale.toLowerCase()] ?? catalogs[locale.split('-')[0]?.toLowerCase() ?? ''] ?? en;
}

export const messages = messagesFor();
