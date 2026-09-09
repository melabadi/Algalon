import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import test from 'node:test';
import {
  classifyEditDecision,
  classifyToolName,
  collectOtelSnapshot,
  measurementWindow,
  measurementWindowSince
} from '../src/otel.js';

test('classifies explicit coding, research, and planning tool proxies without forcing unknown tools', () => {
  const coding = ['apply_patch', 'create_file', 'rename_symbol'];
  const research = ['search', 'read', 'fetch', 'grep'];
  const planning = ['manage_todo_list', 'todo'];
  assert.equal(classifyToolName('apply_patch', coding, research, planning), 'coding_proxy');
  assert.equal(classifyToolName('file_search', coding, research, planning), 'research_proxy');
  assert.equal(classifyToolName('read_file', coding, research, planning), 'research_proxy');
  assert.equal(classifyToolName('manage_todo_list', coding, research, planning), 'planning_proxy');
  assert.equal(classifyToolName('run_in_terminal', coding, research, planning), 'unmapped');
});

test('classifies edit outcomes dynamically across common attribute names', () => {
  const accepted = ['accept', 'accepted', 'apply', 'applied', 'saved', 'keep', 'kept'];
  const rejected = ['reject', 'rejected', 'discard', 'discarded'];
  assert.equal(classifyEditDecision({ decision: 'accepted' }, accepted, rejected), 'accepted');
  assert.equal(classifyEditDecision({ copilot_chat_edit_outcome: 'rejected' }, accepted, rejected), 'rejected');
  assert.equal(classifyEditDecision({ accepted: 'true' }, accepted, rejected), 'accepted');
  assert.equal(classifyEditDecision({ source: 'inline_chat' }, accepted, rejected), 'unmapped');
});

test('measures complete historical UTC days and truncates the current day at now', () => {
  const now = new Date('2026-08-04T12:00:00Z');
  assert.equal(measurementWindow('2026-08-03', now).durationSeconds, 86_400);
  assert.equal(measurementWindow('2026-08-04', now).durationSeconds, 43_200);
  assert.throws(() => measurementWindow('2026-08-05', now), /future/);
});

test('measures an exact experiment window from an ISO start time', () => {
  const now = new Date('2026-08-04T12:30:00Z');
  assert.equal(measurementWindowSince('2026-08-04T12:00:00Z', now).durationSeconds, 1_800);
  assert.throws(() => measurementWindowSince('invalid', now), /Invalid experiment start time/);
  assert.throws(() => measurementWindowSince('2026-08-04T13:00:00Z', now), /Invalid experiment start time/);
});

test('collects and reconciles VictoriaMetrics activity evidence', async (context) => {
  const seriesByMetric: Record<string, Array<Record<string, string>>> = {
    copilot_chat_tool_call_count: [
      { __name__: 'copilot_chat_tool_call_count', success: 'true', tool_name: 'apply_patch' },
      { __name__: 'copilot_chat_tool_call_count', success: 'false', tool_name: 'search' },
      { __name__: 'copilot_chat_tool_call_count', tool_name: 'custom_tool' }
    ],
    copilot_trace_calls: [
      { __name__: 'copilot_trace_calls', gen_ai_operation_name: 'execute_tool', gen_ai_tool_name: 'apply_patch', service_name: 'copilot-chat' },
      { __name__: 'copilot_trace_calls', span_name: 'execute_tool manage_todo_list', service_name: 'copilot-chat' },
      { __name__: 'copilot_trace_calls', span_name: 'execute_tool noisy_validation', service_name: 'copilot-value-validation' },
      { __name__: 'copilot_trace_calls', span_name: 'chat request', service_name: 'copilot-chat' }
    ],
    copilot_chat_edit_acceptance_count: [
      { __name__: 'copilot_chat_edit_acceptance_count', decision: 'accepted' },
      { __name__: 'copilot_chat_edit_acceptance_count', outcome: 'rejected' },
      { __name__: 'copilot_chat_edit_acceptance_count', kind: 'unknown' }
    ],
    copilot_chat_lines_of_code_count: [
      { __name__: 'copilot_chat_lines_of_code_count', language: 'typescript' }
    ],
    copilot_chat_user_action_count: [
      { __name__: 'copilot_chat_user_action_count', user_action: 'apply' },
      { __name__: 'copilot_chat_user_action_count', user_action: 'view' }
    ]
  };
  const server = createServer((request, response) => {
    const url = new URL(request.url ?? '/', 'http://127.0.0.1');
    response.writeHead(200, { 'Content-Type': 'application/json' });
    if (url.pathname.endsWith('/series')) {
      response.end(JSON.stringify({
        status: 'success',
        data: seriesByMetric[url.searchParams.get('match[]') ?? ''] ?? []
      }));
      return;
    }

    const expression = url.searchParams.get('query') ?? '';
    let value = 0;
    if (expression.includes('copilot_chat_session_count')) value = 2;
    else if (expression.includes('copilot_chat_agent_invocation_duration_count')) value = 1;
    else if (expression.includes('copilot_chat_agent_turn_count_sum')) value = 3;
    else if (expression.includes('copilot_chat_edit_survival_four_gram_sum')) value = 3;
    else if (expression.includes('copilot_chat_edit_survival_four_gram_count')) value = 2;
    else if (expression.includes('copilot_chat_edit_survival_no_revert_sum')) value = 1;
    else if (expression.includes('copilot_chat_edit_survival_no_revert_count')) value = 0;
    else if (expression.includes('copilot_chat_tool_call_count')) {
      if (expression.includes('apply_patch')) value = 2;
      else if (expression.includes('search')) value = 1;
      else if (expression.includes('custom_tool')) value = 4;
    } else if (expression.includes('copilot_trace_calls')) {
      if (expression.includes('apply_patch')) value = 3;
      else if (expression.includes('manage_todo_list')) value = 5;
      else if (expression.includes('noisy_validation')) value = 99;
      else value = 88;
    } else if (expression.includes('copilot_chat_edit_acceptance_count')) {
      if (expression.includes('accepted')) value = 2;
      else if (expression.includes('rejected')) value = 1;
      else value = 3;
    } else if (expression.includes('copilot_chat_lines_of_code_count')) value = 7;
    else if (expression.includes('copilot_chat_user_action_count')) {
      value = expression.includes('apply') ? 4 : 8;
    }
    response.end(JSON.stringify({
      status: 'success',
      data: { result: [{ metric: {}, value: [0, String(value)] }] }
    }));
  });
  server.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.once('listening', resolve));
  context.after(() => new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve())));
  const address = server.address();
  assert.ok(address && typeof address === 'object');

  const snapshot = await collectOtelSnapshot('2026-08-04', {
    victoriaMetricsUrl: `http://127.0.0.1:${address.port}`,
    codingToolPatterns: ['apply_patch'],
    researchToolPatterns: ['search'],
    planningToolPatterns: ['manage_todo_list'],
    acceptedDecisionValues: ['accepted'],
    rejectedDecisionValues: ['rejected']
  }, new Date('2026-08-04T12:00:00Z'));

  assert.deepEqual(snapshot, {
    day: '2026-08-04',
    activeDay: 1,
    sessions: 2,
    agentInvocations: 1,
    agentTurns: 3,
    toolCalls: 13,
    successfulToolCalls: 12,
    codingToolCalls: 3,
    researchToolCalls: 0,
    planningToolCalls: 5,
    unmappedToolCalls: 4,
    acceptedEditDecisions: 2,
    rejectedEditDecisions: 1,
    unmappedEditDecisions: 3,
    agentEditLoc: 7,
    appliedUserActions: 4,
    editSurvivalFourGram: 1,
    editSurvivalNoRevert: null
  });
});

test('rejects failed VictoriaMetrics requests', async (context) => {
  const server = createServer((_request, response) => {
    response.writeHead(503, { 'Content-Type': 'text/plain' });
    response.end('temporarily unavailable');
  });
  server.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.once('listening', resolve));
  context.after(() => new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve())));
  const address = server.address();
  assert.ok(address && typeof address === 'object');

  await assert.rejects(collectOtelSnapshot('2026-08-04', {
    victoriaMetricsUrl: `http://127.0.0.1:${address.port}`,
    codingToolPatterns: [],
    researchToolPatterns: [],
    planningToolPatterns: [],
    acceptedDecisionValues: [],
    rejectedDecisionValues: []
  }, new Date('2026-08-04T12:00:00Z')), /VictoriaMetrics request failed \(503\)/);
});