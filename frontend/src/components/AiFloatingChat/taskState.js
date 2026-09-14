/* Task facts are independent of the bounded transcript sent to the models. */
export function newTask() {
  return { goal: "", facts: {}, pendingKey: null, stage: "idle", returnFlow: null, requestId: null };
}

export const ACCEPT_PATTERN = /^(好|好的|好啊|可以|沒問題|繼續|下一步|然後呢|ok|yes)[。！!？?\s]*$/i;
export const FILL_PATTERN = /(幫我|替我|代我).{0,4}(填|申請機器|申請一台)|自動填|直接產生配置/i;
export const NO_MACHINE_PATTERN = /(沒有|還沒|尚未|沒)(有|申請|建立)?(一台)?(機器|主機|容器|虛擬機|vm|lxc)/i;

export function taskRoute(text, task, flowId, hasForm) {
  if (/^(取消|先不要|停止|算了)[。！!\s]*$/.test(text)) return "cancel";
  if (/^(直接產生配置|直接配置)[。！!\s]*$/.test(text)) return "planNow";
  if (NO_MACHINE_PATTERN.test(text) || FILL_PATTERN.test(text)) return "recommend";
  if (!ACCEPT_PATTERN.test(text)) return null;
  if (["planned", "filled", "submitted"].includes(task.stage)) return "continueTask";
  if (task.stage === "collecting" || flowId === "request_machine" || hasForm) return "recommend";
  if (flowId) return "continueTask";
  return null;
}

export function withTaskMemory(history, task) {
  const labels = { purpose: "用途", gpu: "加速需求", display: "作業環境", duration: "使用時間" };
  const facts = Object.entries(labels).flatMap(([key, label]) => {
    const value = task.facts?.[key];
    if (!value) return [];
    const source = task.facts.inferred?.includes(key) ? "建議預設，可調整" : "使用者已提供";
    return [`${label}（${source}）：${value}`];
  });
  if (!task.goal && !facts.length) return history;
  // Keep memory inside the planner's recent-message window. Latest user text wins.
  return [...history, {
    role: "user",
    content: `原始目標：${task.goal}\n${facts.join("\n")}\n本輪要求：${history.filter((m) => m.role === "user").at(-1)?.content ?? ""}`,
  }];
}

export function requestIsReady(request, resources) {
  return request?.status === "approved"
    && request.provisioning_status === "completed"
    && request.vmid != null
    && resources.some((resource) => String(resource.vmid) === String(request.vmid));
}

export function markStep(steps, active) {
  return steps.map((step, index) => ({
    ...step, status: index < active ? "done" : index === active ? "current" : "todo",
  }));
}
