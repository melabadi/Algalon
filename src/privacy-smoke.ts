import { randomBytes, randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const collectorEndpoint = 'http://127.0.0.1:4318';
const victoriaMetricsEndpoint = 'http://127.0.0.1:8428';
const appEndpoint = 'http://127.0.0.1:3000';
const marker = `privacy-smoke-${randomUUID()}`;
const forbidden = `FORBIDDEN-${randomUUID()}`;
const nowNanos = (BigInt(Date.now()) * 1_000_000n).toString();
const startNanos = (BigInt(nowNanos) - 1_000_000n).toString();

function attribute(key: string, value: string) {
  return { key, value: { stringValue: value } };
}

async function post(signal: 'traces' | 'logs' | 'metrics', body: unknown): Promise<void> {
  const response = await fetch(`${collectorEndpoint}/v1/${signal}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    throw new Error(`Synthetic ${signal} export failed (${response.status}): ${(await response.text()).slice(0, 500)}`);
  }
}

async function waitForFileMarker(filePath: string): Promise<string> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const content = await readFile(filePath, 'utf8');
      if (content.includes(marker)) {
        return content;
      }
    } catch {
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Timed out waiting for '${marker}' in ${filePath}.`);
}

async function waitForInboxMarker(): Promise<string> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    let cursor = 0;
    const serializedPages: string[] = [];
    do {
      const url = new URL('/api/internal/otel/records', appEndpoint);
      url.searchParams.set('after', String(cursor));
      url.searchParams.set('limit', '1000');
      const response = await fetch(url);
      if (!response.ok) break;
      const page = await response.json() as {
        records: unknown[];
        nextCursor: number;
        hasMore: boolean;
      };
      const serialized = JSON.stringify(page.records);
      serializedPages.push(serialized);
      if (serialized.includes(marker)) return serializedPages.join('\n');
      if (!page.hasMore || page.nextCursor <= cursor) break;
      cursor = page.nextCursor;
    } while (true);
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Timed out waiting for '${marker}' in the transactional OTLP inbox.`);
}

async function waitForMetric(): Promise<string> {
  const query = `dashboard_validation_privacy{probe="${marker}"}`;
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const url = new URL('/prometheus/api/v1/query', victoriaMetricsEndpoint);
    url.searchParams.set('query', query);
    const response = await fetch(url);
    const content = await response.text();
    if (response.ok && content.includes(marker)) {
      return content;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Timed out waiting for metric '${query}'.`);
}

async function waitForToolSpanMetric(): Promise<string> {
  const query = 'copilot_trace_calls{service_name="copilot-value-validation",gen_ai_tool_name="apply_patch"}';
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const url = new URL('/prometheus/api/v1/query', victoriaMetricsEndpoint);
    url.searchParams.set('query', query);
    const response = await fetch(url);
    const content = await response.text();
    if (response.ok && content.includes('apply_patch')) {
      return content;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Timed out waiting for metric '${query}'.`);
}

async function main(): Promise<void> {
  const traceId = randomBytes(16).toString('hex');
  const spanId = randomBytes(8).toString('hex');

  await Promise.all([
    post('traces', {
      resourceSpans: [
        {
          resource: { attributes: [
            attribute('service.name', 'copilot-chat'),
            attribute('session.id', `privacy-${marker}`),
            attribute('github.copilot.git.repository', forbidden)
          ] },
          scopeSpans: [{
            scope: { name: 'copilot-value-privacy-smoke' },
            spans: [{
              traceId,
              spanId,
              name: marker,
              kind: 'SPAN_KIND_INTERNAL',
              startTimeUnixNano: startNanos,
              endTimeUnixNano: nowNanos,
              attributes: [
                attribute('probe', marker),
                attribute('gen_ai.input.messages', forbidden),
                attribute('gen_ai.system_instructions', forbidden),
                attribute('github.copilot.tool.parameters.command', forbidden)
              ]
            }]
          }]
        },
        {
          resource: { attributes: [attribute('service.name', 'copilot-value-validation')] },
          scopeSpans: [{
            scope: { name: 'copilot-chat' },
            spans: [{
              traceId: randomBytes(16).toString('hex'),
              spanId: randomBytes(8).toString('hex'),
              name: 'execute_tool apply_patch',
              kind: 'SPAN_KIND_INTERNAL',
              startTimeUnixNano: startNanos,
              endTimeUnixNano: nowNanos,
              status: { code: 'STATUS_CODE_OK' },
              attributes: [
                attribute('probe', marker),
                attribute('gen_ai.operation.name', 'execute_tool'),
                attribute('gen_ai.tool.name', 'apply_patch'),
                attribute('gen_ai.tool.description', forbidden)
              ]
            }]
          }]
        }
      ]
    }),
    post('logs', {
      resourceLogs: [{
        resource: { attributes: [attribute('service.name', 'copilot-value-privacy-smoke')] },
        scopeLogs: [{
          scope: { name: 'copilot-value-privacy-smoke' },
          logRecords: [{
            timeUnixNano: nowNanos,
            severityNumber: 'SEVERITY_NUMBER_INFO',
            body: { stringValue: forbidden },
            attributes: [attribute('probe', marker), attribute('github.copilot.hook_input', forbidden)]
          }]
        }]
      }]
    }),
    post('metrics', {
      resourceMetrics: [{
        resource: { attributes: [attribute('service.name', 'copilot-value-privacy-smoke')] },
        scopeMetrics: [{
          scope: { name: 'copilot-value-privacy-smoke' },
          metrics: [{
            name: 'dashboard.validation.privacy',
            gauge: {
              dataPoints: [{
                timeUnixNano: nowNanos,
                asDouble: 1,
                attributes: [attribute('probe', marker), attribute('github.copilot.tool.parameters.file_path', forbidden)]
              }]
            }
          }]
        }]
      }]
    })
  ]);

  const [traceInbox, logArchive, metricResponse, toolSpanMetricResponse] = await Promise.all([
    waitForInboxMarker(),
    waitForFileMarker(path.join('data', 'otel', 'logs.json')),
    waitForMetric(),
    waitForToolSpanMetric()
  ]);

  for (const [surface, content] of [['trace inbox', traceInbox], ['log archive', logArchive], ['metric backend', metricResponse], ['tool span metric', toolSpanMetricResponse]] as const) {
    if (content.includes(forbidden)) {
      throw new Error(`Privacy validation failed: forbidden content reached the ${surface}.`);
    }
  }

  console.log('Privacy smoke passed: forbidden content was removed before SQLite, local logs, and VictoriaMetrics storage.');
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});