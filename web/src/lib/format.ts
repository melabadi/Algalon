import { messages } from '../i18n';

const locale = messages.locale;

export function scenarioLabel(value: string): string {
  return value[0]?.toLocaleUpperCase(locale) + value.slice(1);
}

export function money(value: number | null | undefined, precision = 2): string {
  if (value === null || value === undefined) return '—';
  const absolute = new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: precision,
    maximumFractionDigits: precision
  }).format(Math.abs(value));
  return `${value > 0 ? '+' : value < 0 ? '-' : ''}${absolute}`;
}

export function minutes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(1)}m`;
}

export function ratio(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}x`;
}

export function elapsedMinutes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${value.toFixed(1)}m`;
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat(locale, {
    style: 'percent',
    minimumFractionDigits: 1,
    maximumFractionDigits: 1
  }).format(value);
}

export function compact(value: bigint | number | string): string {
  const normalized: bigint | number = typeof value === 'string'
    ? /^-?\d+$/.test(value) ? BigInt(value) : Number(value)
    : value;
  const compactNotation = typeof normalized === 'bigint'
    ? normalized >= 10_000n
    : Number(normalized) >= 10_000;
  return Intl.NumberFormat(locale, {
    notation: compactNotation ? 'compact' : 'standard',
    maximumFractionDigits: 1
  }).format(normalized);
}

export function exactInteger(exact: string, fallback: number | null): bigint {
  try {
    return BigInt(exact);
  } catch {
    return BigInt(fallback ?? 0);
  }
}

export function exactIntegerText(exact: string, fallback: number | null): string {
  return exactInteger(exact, fallback).toString();
}

export function credits(value: number): string {
  return `${Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(value)} cr`;
}

export function dateTime(value: string): string {
  return new Intl.DateTimeFormat(locale, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(value));
}

export function duration(seconds: number): string {
  return `${(seconds / 60).toFixed(1)}m`;
}
