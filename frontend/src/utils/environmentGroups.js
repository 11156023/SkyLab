import { formatShortDateTime } from "./formatDate";

function formatDateTime(value) {
  return formatShortDateTime(value, "依環境政策");
}

function machineFromResource(resource, fallback = {}) {
  return {
    id: fallback.id ?? resource.request_id ?? `resource-${resource.vmid}`,
    requestId: fallback.requestId ?? resource.request_id,
    vmid: resource.vmid,
    name: fallback.name ?? resource.name,
    role: fallback.role ?? resource.environment_type ?? "機器",
    type: resource.type ?? fallback.type,
    os: resource.os_info ?? fallback.os ?? "—",
    status: resource.status ?? fallback.status ?? "unknown",
    ip: resource.ip_address ?? fallback.ip ?? "N/A",
    publicUrl: fallback.publicUrl ?? null,
    node: resource.node ?? fallback.node ?? "—",
    // 規格：讓環境內的機器也看得到 CPU/RAM，不必進詳情頁
    cpu: resource.maxcpu ?? fallback.cpu ?? null,
    memoryBytes: resource.maxmem ?? fallback.memoryBytes ?? null,
    resource,
  };
}

function quickPracticeGroups(resources, sessions) {
  const byRequest = new Map(
    resources
      .filter((resource) => resource.request_id)
      .map((resource) => [String(resource.request_id), resource]),
  );
  return sessions.map((session) => {
    const machines = session.machines.map((machine) => {
      const resource = byRequest.get(String(machine.requestId));
      const fallback = {
        id: machine.id,
        requestId: machine.requestId,
        vmid: machine.vmid,
        name: machine.name,
        role: machine.role,
        type: machine.type,
        os: machine.os_info,
        status: machine.status,
        ip: machine.ip,
        node: machine.node,
        publicUrl: machine.publicUrl ?? null,
      };
      return resource ? machineFromResource(resource, fallback) : fallback;
    });
    const nodes = new Set(machines.map((machine) => machine.node).filter(Boolean));
    return {
      id: session.id,
      kind: "quick_practice",
      kindLabel: session.kindLabel ?? "快速練習",
      title: session.title,
      status: session.status,
      timingLabel: `${formatDateTime(session.expiresAt)} 到期`,
      nodeLabel: nodes.size === 1 ? [...nodes][0] : nodes.size > 1 ? "多節點" : "配置中",
      preview: false,
      machines,
    };
  });
}

function courseGroups(resources, excludedRequestIds) {
  const grouped = new Map();
  for (const resource of resources) {
    if (!resource.teaching_class_id || excludedRequestIds.has(String(resource.request_id))) continue;
    const id = String(resource.teaching_class_id);
    if (!grouped.has(id)) grouped.set(id, []);
    grouped.get(id).push(resource);
  }
  return [...grouped.entries()].map(([classId, rows]) => {
    const machines = rows.map((resource) => machineFromResource(resource));
    const nodes = new Set(machines.map((machine) => machine.node).filter(Boolean));
    const title = rows.find((resource) => resource.environment_type)?.environment_type ?? `課程 ${classId.slice(0, 8)}`;
    return {
      id: `course-${classId}`,
      kind: "course",
      kindLabel: "課堂機器",
      title,
      status: machines.every((machine) => machine.status === "running") ? "running" : "active",
      timingLabel: "依課程時段管理",
      nodeLabel: nodes.size === 1 ? [...nodes][0] : nodes.size > 1 ? "多節點" : "配置中",
      preview: false,
      machines,
    };
  });
}

export function buildEnvironmentGroups(resources = [], quickSessions = []) {
  const quick = quickPracticeGroups(resources, quickSessions);
  const quickRequestIds = new Set(
    quick.flatMap((group) => group.machines.map((machine) => String(machine.requestId))),
  );
  return [...courseGroups(resources, quickRequestIds), ...quick];
}

export function groupedResourceKeys(groups = []) {
  return {
    requestIds: new Set(groups.flatMap((group) => group.machines.map((machine) => String(machine.requestId)))),
    vmids: new Set(groups.flatMap((group) => group.machines.map((machine) => machine.vmid).filter((vmid) => vmid != null))),
  };
}
