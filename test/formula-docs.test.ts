import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const formulaDocuments = [
  'README.md',
  'docs/methodology.md',
  'docs/roi-formula-evolution.md'
];

const correspondencePattern = /^\*\*(?:Code correspondence|Historical code correspondence|Historical code status):\*\*/;
const formulaTableHeaders = [
  { document: 'README.md', header: '| Calibration | Pessimistic | Base | Optimistic |' },
  { document: 'docs/methodology.md', header: '| Assumption | Pessimistic | Base | Optimistic |' },
  { document: 'docs/roi-formula-evolution.md', header: '| Phase $p$ | Pessimistic $s_p$ | Base $s_p$ | Optimistic $s_p$ |' },
  { document: 'docs/roi-formula-evolution.md', header: '| Assumption | Pessimistic | Base | Optimistic |' },
  { document: 'docs/roi-formula-evolution.md', header: '| Phase | Pessimistic | Base | Optimistic |' }
];

test('keeps worker and read-model formula versions aligned', () => {
  const benchmarkMatch = readFileSync(path.resolve('shared/benchmark.ts'), 'utf8')
    .match(/export const benchmarkFormulaVersion = (\d+);/);
  const readModelMatch = readFileSync(path.resolve('backend/app/store.py'), 'utf8')
    .match(/^CURRENT_FORMULA_VERSION = (\d+)$/m);
  assert.ok(benchmarkMatch, 'shared benchmark formula version is missing.');
  assert.ok(readModelMatch, 'backend formula version is missing.');
  assert.equal(readModelMatch[1], benchmarkMatch[1]);
});

test('keeps methodology portfolio time in minutes', () => {
  const methodology = readFileSync(path.resolve('docs/methodology.md'), 'utf8');
  const store = readFileSync(path.resolve('backend/app/store.py'), 'utf8');
  assert.ok(methodology.includes(String.raw`T_{AI,portfolio}=\frac{1}{60}\min`));
  assert.match(
    store,
    /"portfolioAiTime": "T_AI,portfolio = min\(sum_i\(W_engaged,i\), measure\(union_i\(\[t_start,i, t_end,i\]\)\)\) \/ 60"/
  );
});

test('links every displayed formula to matching code or explicit superseded status', () => {
  let formulaCount = 0;

  for (const relativePath of formulaDocuments) {
    const lines = readFileSync(path.resolve(relativePath), 'utf8').split(/\r?\n/);
    let openingLine: number | null = null;
    for (let index = 0; index < lines.length; index += 1) {
      if (lines[index]?.trim() !== '$$') continue;
      if (openingLine === null) {
        openingLine = index;
        continue;
      }

      formulaCount += 1;
      let annotationLine = index + 1;
      while (annotationLine < lines.length && !lines[annotationLine]?.trim()) annotationLine += 1;
      assert.match(
        lines[annotationLine] ?? '',
        correspondencePattern,
        `${relativePath}:${openingLine + 1}-${index + 1} has no adjacent code correspondence.`
      );
      openingLine = null;
    }
    assert.equal(openingLine, null, `${relativePath} has an unclosed display formula.`);
  }

  assert.ok(formulaCount > 0);
});

test('links formula-bearing tables to their configuration or evaluator', () => {
  for (const { document, header } of formulaTableHeaders) {
    const lines = readFileSync(path.resolve(document), 'utf8').split(/\r?\n/);
    const headerLine = lines.indexOf(header);
    assert.notEqual(headerLine, -1, `${document} is missing formula table '${header}'.`);
    let annotationLine = headerLine + 1;
    while (annotationLine < lines.length && lines[annotationLine]?.startsWith('|')) annotationLine += 1;
    while (annotationLine < lines.length && !lines[annotationLine]?.trim()) annotationLine += 1;
    assert.match(
      lines[annotationLine] ?? '',
      correspondencePattern,
      `${document}:${headerLine + 1} formula table has no adjacent code correspondence.`
    );
  }
});

test('keeps formula documentation source-line links in bounds', () => {
  const sourceLinkPattern = /\]\(([^)#]+)#L(\d+)(?:-L(\d+))?\)/g;
  let linkCount = 0;

  for (const relativePath of formulaDocuments) {
    const documentPath = path.resolve(relativePath);
    const content = readFileSync(documentPath, 'utf8');
    for (const match of content.matchAll(sourceLinkPattern)) {
      const linkedPath = match[1];
      const startLine = Number(match[2]);
      const endLine = Number(match[3] ?? match[2]);
      assert.ok(linkedPath);
      const targetPath = path.resolve(path.dirname(documentPath), decodeURIComponent(linkedPath));
      const targetLineCount = readFileSync(targetPath, 'utf8').split(/\r?\n/).length;
      assert.ok(
        startLine >= 1 && endLine >= startLine && endLine <= targetLineCount,
        `${relativePath} links outside ${linkedPath} (${startLine}-${endLine} of ${targetLineCount}).`
      );
      linkCount += 1;
    }
  }

  assert.ok(linkCount > 0);
});