import { apiDelete, apiGet, apiPatch, apiPost, apiPostMultipart, apiPut } from "./api";
import { formatDate } from "../utils/formatDate";

const EDITOR_FIELDS = ["name", "description", "usageScope", "nodes", "edges", "publications"];
function editorFields(item) {
  return Object.fromEntries(EDITOR_FIELDS.filter((key) => key in item).map((key) => [key, item[key]]));
}

export function courseNodeHasUsableSource(node) {
  return node?.sourceType === "custom"
    ? Boolean(node.customImageRef)
    : Boolean(node?.sourceTemplateId);
}

function normalizeNode(node) {
  return {
    ...node,
    id: node.node_key ?? String(node.id),
    sourceTemplateId: node.source_template_id,
    sourceType: node.source_type ?? "template",
    customImageRef: node.custom_image_ref ?? "",
    customUsername: node.custom_username ?? "student",
    customUnprivileged: node.custom_unprivileged ?? true,
    type: node.resource_type,
    memory: Math.max(1, Math.round(Number(node.memory_mb ?? 1024) / 1024)),
    disk: Number(node.disk_gb ?? 0),
    positionX: Number(node.position_x ?? 80),
    positionY: Number(node.position_y ?? 120),
    image: node.name,
    icon: "dns",
  };
}

export function normalizeCourseEnvironment(item) {
  return {
    ...item,
    id: String(item.id),
    versionId: String(item.version_id),
    files: (item.files ?? []).map((file) => ({
      ...file,
      id: String(file.id),
      sizeBytes: Number(file.size_bytes ?? 0),
    })),
    updatedAt: formatDate(item.updated_at, ""),
    usageScope: item.usage_scope ?? "course",
    nodes: (item.nodes ?? []).map(normalizeNode),
    edges: (item.edges ?? []).map((edge) => ({
      ...edge,
      id: String(edge.id ?? `${edge.source_node_key}-${edge.target_node_key}`),
      source: edge.source_node_key,
      target: edge.target_node_key,
      direction: edge.direction ?? "one_way",
      protocol: edge.protocol ?? "tcp",
      port: edge.protocol === "any" ? null : Number(edge.port ?? 22),
    })),
    publications: (item.publications ?? []).map((publication, index) => ({
      ...publication,
      id: String(publication.id ?? `publication-${index + 1}`),
      nodeKey: publication.node_key,
      mode: publication.mode ?? "domain",
      port: Number(publication.port ?? 80),
      protocol: publication.protocol ?? "tcp",
      hostnamePrefix: publication.hostname_prefix ?? "",
      zoneId: publication.zone_id ?? "",
      enableHttps: publication.enable_https !== false,
    })),
    ...(item.status === "draft" && item.draft_data?.editor ? editorFields(item.draft_data.editor) : {}),
  };
}

export function environmentPayload(item) {
  return {
    name: item.name.trim(),
    description: item.description?.trim() || null,
    usage_scope: item.usageScope ?? "course",
    /* 沒有「學生可見對象」這個欄位了：提供為快速練習就代表全校學生都拿得到，
       名額仍由每人同時一組與 24 小時上限擋著。 */
    audience: "campus",
    audience_class_ids: [],
    max_concurrent_sessions: null,
    nodes: item.nodes.map((node, index) => ({
      node_key: String(node.id || `node-${index + 1}`),
      source_type: node.sourceType ?? "template",
      source_template_id: node.sourceType === "custom" ? null : node.sourceTemplateId,
      custom_image_ref: node.sourceType === "custom" ? node.customImageRef : null,
      custom_username: node.sourceType === "custom" && String(node.type).toLowerCase() !== "lxc" ? (node.customUsername || "student") : null,
      custom_unprivileged: node.sourceType === "custom" ? node.customUnprivileged !== false : true,
      name: node.name.trim(),
      role: node.role.trim(),
      resource_type: String(node.type).toLowerCase() === "lxc" ? "lxc" : "qemu",
      cpu: Number(node.cpu),
      memory_mb: Number(node.memory) * 1024,
      disk_gb: Number(node.disk),
      network: node.network?.trim() || "lab-net",
      position_x: Number(node.positionX ?? (80 + index * 260)),
      position_y: Number(node.positionY ?? (120 + (index % 2) * 45)),
    })),
    edges: (item.edges ?? []).map((edge) => ({
      source_node_key: String(edge.source ?? edge.source_node_key),
      target_node_key: String(edge.target ?? edge.target_node_key),
      direction: edge.direction ?? "one_way",
      protocol: edge.protocol ?? "tcp",
      port: edge.protocol === "any" ? null : Number(edge.port ?? 22),
    })),
    publications: (item.publications ?? []).map((publication) => ({
      node_key: String(publication.nodeKey ?? publication.node_key),
      mode: publication.mode ?? "domain",
      port: Number(publication.port),
      protocol: publication.protocol ?? "tcp",
      hostname_prefix: publication.mode === "domain" ? (publication.hostnamePrefix || "").trim() : null,
      zone_id: publication.mode === "domain" ? (publication.zoneId || null) : null,
      enable_https: publication.enableHttps !== false,
    })),
  };
}

export const CourseEnvironmentsService = {
  async saveDraft(environmentId, item) {
    const body = { configuration: environmentPayload(item), editor: editorFields(item), draft_id: item.draftRequestId ?? null };
    return normalizeCourseEnvironment(await (environmentId
      ? apiPut(`/api/v1/course-environments/${environmentId}/draft`, body)
      : apiPost("/api/v1/course-environments/drafts", body)));
  },
  async list() {
    return (await apiGet("/api/v1/course-environments")).map(normalizeCourseEnvironment);
  },
  async listPublished() {
    return (await apiGet("/api/v1/course-environments/published")).map(normalizeCourseEnvironment);
  },
  async get(environmentId) {
    return normalizeCourseEnvironment(await apiGet(`/api/v1/course-environments/${environmentId}`));
  },
  async create(item) {
    return normalizeCourseEnvironment(await apiPost("/api/v1/course-environments", environmentPayload(item)));
  },
  async update(environmentId, item) {
    return normalizeCourseEnvironment(await apiPut(`/api/v1/course-environments/${environmentId}`, environmentPayload(item)));
  },
  async publish(environmentId) {
    return normalizeCourseEnvironment(await apiPost(`/api/v1/course-environments/${environmentId}/publish`, {}));
  },
  async saveBasics(environmentId, item) {
    return normalizeCourseEnvironment(await apiPatch(`/api/v1/course-environments/${environmentId}/basics`, {
      name: item.name.trim(),
      description: item.description?.trim() || null,
      usage_scope: item.usageScope ?? "course",
    }));
  },
  async uploadFile(environmentId, file) {
    const form = new FormData();
    form.append("file", file);
    return normalizeCourseEnvironment(await apiPostMultipart(`/api/v1/course-environments/${environmentId}/files`, form));
  },
  async removeFile(environmentId, fileId) {
    return normalizeCourseEnvironment(await apiDelete(`/api/v1/course-environments/${environmentId}/files/${fileId}`));
  },
  fileUrl(environmentId, fileId) {
    return `/api/v1/course-environments/${environmentId}/files/${fileId}`;
  },
  async remove(environmentId) {
    return apiDelete(`/api/v1/course-environments/${environmentId}`);
  },
};
