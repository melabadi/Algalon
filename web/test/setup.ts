import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';

Object.defineProperty(Element.prototype, 'scrollIntoView', {
  configurable: true,
  value: vi.fn()
});

Object.defineProperty(window, 'scrollTo', {
  configurable: true,
  value: vi.fn()
});

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem('algalon.autoRefreshSeconds', '0');
  window.history.replaceState({}, '', '/');
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});