import assert from 'node:assert/strict';
import test from 'node:test';
import {
  combinedMode,
  combinedSurface,
  identifierTokens,
  isContainerTool,
  parseMode,
  parseSurface,
  spanOperation,
  toolMatchesPattern,
  toolNameFromSpan
} from '../src/runtime-telemetry.js';

test('detects GenAI operations from the attribute first and the span name second', () => {
  assert.equal(spanOperation('anything', 'chat'), 'chat');
  assert.equal(spanOperation('chat gpt', 'execute_hook'), undefined);
  assert.equal(spanOperation('invoke_agent', undefined), 'invoke_agent');
  assert.equal(spanOperation('invoke_agent explore', undefined), 'invoke_agent');
  assert.equal(spanOperation('chat gpt-6.1-sol', undefined), 'chat');
  assert.equal(spanOperation('execute_tool view', undefined), 'execute_tool');
  assert.equal(spanOperation('session.timing.model_resolution', undefined), undefined);
  assert.equal(spanOperation(undefined, undefined), undefined);
});

test('reads tool names from attributes or the runtime span name', () => {
  assert.equal(toolNameFromSpan('execute_tool powershell pwsh.exe', undefined), 'powershell');
  assert.equal(toolNameFromSpan('execute_tool view', 'view'), 'view');
  assert.equal(toolNameFromSpan('chat model', undefined), undefined);
  assert.equal(toolNameFromSpan(undefined, undefined), undefined);
});

test('matches whole identifier tokens instead of substrings', () => {
  assert.deepEqual(identifierTokens('readFile_v2-tool'), ['read', 'file', 'v2', 'tool']);
  assert.equal(toolMatchesPattern('rg', 'rg'), true);
  assert.equal(toolMatchesPattern('github-mcp-server-merge_pull_request', 'rg'), false);
  assert.equal(toolMatchesPattern('read_powershell', 'powershell'), true);
  assert.equal(toolMatchesPattern('read_powershell', 'read_file'), false);
  assert.equal(toolMatchesPattern('apply_patch', 'apply_patch'), true);
  assert.equal(toolMatchesPattern('web_fetch', 'fetch'), true);
  assert.equal(toolMatchesPattern('view', ''), false);
  assert.equal(toolMatchesPattern('view', 'view_image'), false);
});

test('identifies delegation and wait tools as containers', () => {
  assert.equal(isContainerTool('task'), true);
  assert.equal(isContainerTool('read_agent'), true);
  assert.equal(isContainerTool('readAgent'), true);
  assert.equal(isContainerTool('view'), false);
  assert.equal(isContainerTool(undefined), false);
});

test('combines surfaces and modes conservatively', () => {
  assert.equal(parseSurface('vscode'), 'vscode');
  assert.equal(parseSurface('mixed'), 'other');
  assert.equal(parseSurface(undefined), 'other');
  assert.equal(parseMode('automated'), 'automated');
  assert.equal(parseMode('anything'), 'interactive');
  assert.equal(combinedSurface(['cli', 'cli']), 'cli');
  assert.equal(combinedSurface(['cli', 'vscode']), 'mixed');
  assert.equal(combinedSurface([]), 'other');
  assert.equal(combinedMode(['automated', 'automated']), 'automated');
  assert.equal(combinedMode(['automated', 'interactive']), 'interactive');
  assert.equal(combinedMode([]), 'interactive');
});
