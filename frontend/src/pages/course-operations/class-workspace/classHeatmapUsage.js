export const RESOURCE_METRICS = {
  cpu: { label: "CPU", icon: "memory", field: "cpu_usage_pct" },
  ram: { label: "RAM", icon: "storage", field: "ram_usage_pct" },
};

const USAGE_FIELDS = [
  "status",
  "cpu_usage_pct",
  "ram_usage_pct",
  "mem_used_bytes",
  "mem_total_bytes",
];

function sameUsage(left, right) {
  return left === right || USAGE_FIELDS.every((field) => left?.[field] === right?.[field]);
}

export function mergeResourceUsageByVmid(current = {}, items = []) {
  const next = {};
  let changed = Object.keys(current).length !== items.length;
  items.forEach((item) => {
    const key = String(item.vmid);
    const previous = current[key];
    next[key] = sameUsage(previous, item) ? previous : item;
    if (next[key] !== previous) changed = true;
  });
  return changed ? next : current;
}

export function machineRuntimeState(machine, runtime) {
  if (!machine?.vmid || !["completed", "running"].includes(machine.status)) return "unavailable";
  const status = String(runtime?.status ?? "").toLowerCase();
  if (["stopped", "offline", "shutdown", "off"].includes(status)) return "off";
  if (["running", "online", "started", "on"].includes(status)) return "on";
  return "unavailable";
}

export function usageForMetric(runtime, metric) {
  const field = RESOURCE_METRICS[metric]?.field;
  const raw = field ? runtime?.[field] : null;
  if (raw === null || raw === undefined || raw === "" || !Number.isFinite(Number(raw))) return null;
  return Math.round(Math.max(0, Math.min(100, Number(raw))));
}
