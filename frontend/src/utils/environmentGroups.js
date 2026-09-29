import i18n from "../i18n";
import { formatShortDateTime } from "./formatDate";

/* 顯示字串在每次 build 時依目前語系產生；頁面每次 render 都重算，切換語言會跟著變 */
const label = (key, options) => i18n.t(`EnvironmentGroups.${key}`, { ns: "common", ...options });

function expiryLabel(value) {
  const time = formatShortDateTime(value, "");
  return time ? label("expiresAt", { time }) : label("expiresByPolicy");
}

function nodeSummary(nodes) {
  if (nodes.size === 1) return [...nodes][0];
  return nodes.size > 1 ? label("multiNode") : label("provisioning");
}

function machineFromResource(resource, fallback = {}) {
  return {
    id: fallback.id ?? resource.request_id ?? `resource-${resource.vmid}`,
    requestId: fallback.requestId ?? resource.request_id,
    vmid: resource.vmid,
    name: fallback.name ?? resource.name,
    role: fallback.role ?? resource.environment_type ?? label("machine"),
    type: resource.type ?? fallback.type,
    os: resource.os_info ?? fallback.os ?? "—",
    status: resource.status ?? fallback.status ?? "unknown",
    ip: resource.ip_address ?? fallback.ip ?? "N/A",
    publicUrl: fallback.publicUrl ?? resource.public_urls?.[0] ?? null,
    forwardEndpoints: fallback.forwardEndpoints ?? [],
    node: resource.node ?? fallback.node ?? "—",
    // 規格：讓環境內的機器也看得到 CPU/RAM，不必進詳情頁
    cpu: resource.maxcpu ?? fallback.cpu ?? null,
    memoryBytes: resource.maxmem ?? fallback.memoryBytes ?? null,
    /* 老師看學生的班級機：一列一位學生，名字放在機器名旁邊 */
    ownerName: resource.owner_name ?? null,
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
        forwardEndpoints: machine.forwardEndpoints ?? [],
      };
      return resource ? machineFromResource(resource, fallback) : fallback;
    });
    const nodes = new Set(machines.map((machine) => machine.node).filter(Boolean));
    return {
      id: session.id,
      kind: "quick_practice",
      title: session.title,
      status: session.status,
      timingLabel: expiryLabel(session.expiresAt),
      nodeLabel: nodeSummary(nodes),
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
    /* 老師取的機器名沿 os_info 帶出來（與快速練習同一個欄位）。舊機器沒有這個
       值，退回主機名——總比顯示空白好。 */
    const machines = rows.map((resource) => machineFromResource(resource, {
      name: resource.os_info || undefined,
    }));
    const nodes = new Set(machines.map((machine) => machine.node).filter(Boolean));
    const title = rows.find((resource) => resource.teaching_class_name)?.teaching_class_name
      ?? rows.find((resource) => resource.environment_type)?.environment_type
      ?? `#${classId.slice(0, 8)}`;
    return {
      id: `course-${classId}`,
      kind: "course",
      /* 這組是「我的班級機器」還是「我教的班的學生機器」，徽章據此換色 */
      classRelation: rows.find((resource) => resource.class_relation)?.class_relation ?? null,
      title,
      status: machines.every((machine) => machine.status === "running") ? "running" : "active",
      timingLabel: label("byCourseSchedule"),
      nodeLabel: nodeSummary(nodes),
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
