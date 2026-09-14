import { apiGet, apiPost } from "./api";

function normalizeNode(node) {
  return {
    ...node,
    id: node.node_key ?? String(node.id),
    type: node.resource_type,
    memory: Math.max(1, Math.round(Number(node.memory_mb ?? 1024) / 1024)),
    disk: Number(node.disk_gb ?? 0),
  };
}

function normalizeTemplate(item) {
  return {
    ...item,
    id: String(item.id),
    versionId: String(item.version_id),
    usageScope: item.usage_scope,
    nodes: (item.nodes ?? []).map(normalizeNode),
  };
}

function normalizeSession(item) {
  return {
    ...item,
    id: String(item.id),
    kindLabel: item.kind_label,
    environmentId: String(item.environment_id),
    environmentVersionId: String(item.environment_version_id),
    expiresAt: item.expires_at,
    createdAt: item.created_at,
    machines: (item.machines ?? []).map((machine) => ({
      ...machine,
      id: String(machine.id),
      requestId: String(machine.request_id),
      type: machine.resource_type,
      ip: machine.ip_address,
      publicUrl: machine.public_url ?? null,
    })),
  };
}

export const QuickPracticeService = {
  async listTemplates(options) {
    return (await apiGet("/api/v1/quick-practice/templates", options)).map(normalizeTemplate);
  },
  async getTemplate(environmentId) {
    return normalizeTemplate(await apiGet(`/api/v1/quick-practice/templates/${environmentId}`));
  },
  async launch(environmentId) {
    return normalizeSession(await apiPost(`/api/v1/quick-practice/templates/${environmentId}/launch`, {}));
  },
  async endSession(sessionId) {
    return normalizeSession(await apiPost(`/api/v1/quick-practice/sessions/${sessionId}/end`, {}));
  },
  async listMySessions(options) {
    return (await apiGet("/api/v1/quick-practice/sessions/my", options)).map(normalizeSession);
  },
  async listAllSessions(options) {
    return (await apiGet("/api/v1/quick-practice/sessions", options)).map(normalizeSession);
  },
};
