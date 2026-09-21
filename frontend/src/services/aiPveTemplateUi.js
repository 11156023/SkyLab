/** Pure helpers shared by the AI PVE template page and its tests. */

export function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;',
  })[char]);
}

export function findPendingTool(data) {
  return (data?.tools_called || []).find((tool) => tool?.result?.pending) || null;
}

export function validateTargets(rawTargets, maxCount = 3) {
  const rows = (rawTargets || []).map((target) => ({
    vmidText: String(target?.vmid ?? '').trim(),
    template_key: String(target?.template_key ?? '').trim(),
  }));
  // A slot is enabled by its VMID. A template left selected in an otherwise
  // blank row must not turn that optional row into an invalid target.
  const activeRows = rows.filter((row) => row.vmidText);
  if (!activeRows.length) {
    return { targets: [], error: '至少填寫 1 台測試機器。' };
  }
  if (activeRows.length > maxCount) {
    return { targets: [], error: `最多只能測試 ${maxCount} 台機器。` };
  }
  const targets = activeRows.map((row) => ({
    vmid: Number(row.vmidText),
    template_key: row.template_key,
  }));
  if (targets.some((target) => !Number.isInteger(target.vmid) || target.vmid < 1)) {
    return { targets, error: '每個 VMID 必須是大於零的整數。' };
  }
  if (targets.some((target) => !target.template_key)) {
    return { targets, error: '每台機器都必須選擇 AI 機器模板。' };
  }
  if (new Set(targets.map((target) => target.vmid)).size !== targets.length) {
    return { targets, error: '已填入的 VMID 不得重複。' };
  }
  return { targets, error: null };
}

export function groupToolCallsByVmid(data) {
  const groups = new Map();
  for (const tool of data?.tools_called || []) {
    const vmid = tool?.args?.vmid ?? tool?.result?.vmid;
    const key = vmid == null ? 'unknown' : String(vmid);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(tool);
  }
  return groups;
}

export function getConfirmationDetails(data) {
  const pending = findPendingTool(data);
  if (!pending && !data?.needs_confirmation) return null;

  const result = pending?.result || {};
  return {
    command: pending?.args?.command || result.command || '',
    reason: result.reason || '這個指令不在 template 的唯讀 smoke command 清單，需要人工確認。',
    token: result.confirm_token || null,
    vmid: data?.vmid ?? pending?.args?.vmid ?? result.vmid ?? data?.targets?.[0]?.vmid ?? null,
  };
}

export function getResponseStatus(data) {
  if (data?.error) return { kind: 'error', message: data.error };
  if (getConfirmationDetails(data)) {
    return { kind: 'confirmation', message: 'AI 已提出需要人工確認的指令。' };
  }
  if (data?.confirmation_result) {
    const result = data.confirmation_result;
    if (result.error?.includes('使用者已拒絕')) {
      return { kind: 'complete', message: '你已拒絕執行指令，AI 已根據此決定繼續處理。' };
    }
    if (result.blocked) {
      return { kind: 'error', message: `指令已被安全規則攔截：${result.block_reason || '不允許執行'}` };
    }
    if (result.error) {
      return { kind: 'error', message: `指令確認後執行失敗：${result.error}` };
    }
    return { kind: 'complete', message: '指令已獲同意並執行，AI 已接續整理結果。' };
  }
  return { kind: 'complete', message: 'AI 已完成本次分析。' };
}

export function getToolDisplayData(tool) {
  const result = { ...(tool?.result || {}) };
  // The confirmation token is only for the in-memory confirm request; never
  // put it into the visible tool transcript or a copied result payload.
  delete result.confirm_token;
  return { args: tool?.args || {}, result };
}
