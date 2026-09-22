import { describe, expect, test } from 'vitest';
import {
  escapeHtml,
  getConfirmationDetails,
  getResponseStatus,
  getToolDisplayData,
  groupToolCallsByVmid,
  validateTargets,
} from './aiPveTemplateUi.js';

describe('AI PVE template UI contract', () => {
  test('pending response exposes the command and VMID without changing the token', () => {
    const response = {
      vmid: 102,
      needs_confirmation: true,
      tools_called: [{
        name: 'ssh_exec',
        args: { vmid: 102, command: 'npm install n8n' },
        result: { pending: true, reason: '可能修改環境', confirm_token: 'secret-token' },
      }],
    };

    expect(getConfirmationDetails(response)).toEqual({
      command: 'npm install n8n',
      reason: '可能修改環境',
      token: 'secret-token',
      vmid: 102,
    });
    expect(getResponseStatus(response)).toEqual({
      kind: 'confirmation',
      message: 'AI 已提出需要人工確認的指令。',
    });
  });

  test('three selected targets produce a valid payload shape', () => {
    expect(validateTargets([
      { vmid: '102', template_key: 'n8n' },
      { vmid: '107', template_key: 'postgresql' },
      { vmid: '115', template_key: 'python' },
    ])).toEqual({
      targets: [
        { vmid: 102, template_key: 'n8n' },
        { vmid: 107, template_key: 'postgresql' },
        { vmid: 115, template_key: 'python' },
      ],
      error: null,
    });
  });

  test('target validation supports optional slots and rejects invalid VMIDs', () => {
    expect(validateTargets([
      { vmid: '102', template_key: 'n8n' },
      { vmid: '102', template_key: 'python' },
      { vmid: '', template_key: '' },
    ]).error).toBe('已填入的 VMID 不得重複。');
    expect(validateTargets([
      { vmid: '102', template_key: 'n8n' },
      { vmid: '107', template_key: 'python' },
      { vmid: '', template_key: '' },
    ])).toEqual({
      targets: [
        { vmid: 102, template_key: 'n8n' },
        { vmid: 107, template_key: 'python' },
      ],
      error: null,
    });
    expect(validateTargets([
      { vmid: '', template_key: '' },
      { vmid: '', template_key: '' },
      { vmid: '', template_key: '' },
    ]).error).toBe('至少填寫 1 台測試機器。');
    expect(validateTargets([
      { vmid: '102', template_key: '' },
      { vmid: '', template_key: '' },
      { vmid: '', template_key: '' },
    ]).error).toBe('每台機器都必須選擇 AI 機器模板。');
    expect(validateTargets([
      { vmid: '102', template_key: 'n8n' },
      { vmid: '', template_key: 'python' },
      { vmid: '', template_key: '' },
    ])).toEqual({
      targets: [{ vmid: 102, template_key: 'n8n' }],
      error: null,
    });
  });

  test('tool calls are grouped by their target VMID', () => {
    const groups = groupToolCallsByVmid({
      tools_called: [
        { name: 'ssh_exec', args: { vmid: 107 } },
        { name: 'ssh_exec', result: { vmid: 102 } },
        { name: 'get_resource_detail', args: {} },
      ],
    });
    expect([...groups.keys()]).toEqual(['107', '102', 'unknown']);
    expect(groups.get('107')).toHaveLength(1);
  });

  test('auto-executed response is complete and does not show confirmation', () => {
    const response = {
      vmid: 102,
      needs_confirmation: false,
      tools_called: [{ name: 'ssh_exec', result: { pending: false, exit_code: 0 } }],
      reply: 'n8n 正在監聽 5678。',
    };

    expect(getConfirmationDetails(response)).toBeNull();
    expect(getResponseStatus(response).kind).toBe('complete');
  });

  test('needs_confirmation without a token remains non-actionable', () => {
    const details = getConfirmationDetails({ vmid: 102, needs_confirmation: true });
    expect(details).toMatchObject({ vmid: 102, token: null, command: '' });
  });

  test('error takes priority over confirmation status', () => {
    expect(getResponseStatus({ error: 'token 已過期', needs_confirmation: true })).toEqual({
      kind: 'error',
      message: 'token 已過期',
    });
  });

  test('rejected confirmation is shown as a respected decision, not execution success', () => {
    expect(getResponseStatus({
      confirmation_result: { error: '使用者已拒絕執行此指令。' },
      reply: '已停止安裝，改以唯讀方式說明目前狀態。',
    })).toEqual({
      kind: 'complete',
      message: '你已拒絕執行指令，AI 已根據此決定繼續處理。',
    });
  });

  test('security-blocked confirmation remains visibly blocked', () => {
    expect(getResponseStatus({
      confirmation_result: { blocked: true, block_reason: '危險指令' },
    })).toEqual({
      kind: 'error',
      message: '指令已被安全規則攔截：危險指令',
    });
  });

  test('confirmation token is not included in visible tool details', () => {
    const display = JSON.stringify(getToolDisplayData({
      args: { command: 'df -h' },
      result: { pending: true, confirm_token: 'secret-token' },
    }));
    expect(display).not.toContain('secret-token');
  });

  test('HTML escaping protects tool output', () => {
    expect(escapeHtml('<script>alert("x")</script>')).toBe(
      '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;',
    );
  });
});
