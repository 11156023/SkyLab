import { apiDelete, apiGet, apiPatch, apiPost, apiPostMultipart, apiPut } from "./api";

const RESOURCE_USAGE_CACHE_TTL_MS = 5_000;
const resourceUsageCache = new Map();

function getResourceUsage(classId) {
  const key = String(classId);
  const now = Date.now();
  resourceUsageCache.forEach((entry, cachedKey) => {
    if (!entry.pending && now - entry.cachedAt >= RESOURCE_USAGE_CACHE_TTL_MS) {
      resourceUsageCache.delete(cachedKey);
    }
  });
  const cached = resourceUsageCache.get(key);
  if (cached?.value && now - cached.cachedAt < RESOURCE_USAGE_CACHE_TTL_MS) {
    return Promise.resolve(cached.value);
  }
  if (cached?.pending) return cached.pending;

  const pending = apiGet(`/api/v1/teaching-classes/${classId}/resource-usage`)
    .then((value) => {
      resourceUsageCache.set(key, { value, cachedAt: Date.now(), pending: null });
      return value;
    })
    .catch((error) => {
      if (resourceUsageCache.get(key)?.pending === pending) {
        resourceUsageCache.delete(key);
      }
      throw error;
    });
  resourceUsageCache.set(key, {
    value: cached?.value,
    cachedAt: cached?.cachedAt ?? 0,
    pending,
  });
  return pending;
}

export const TeachingClassesService = {
  list() { return apiGet("/api/v1/teaching-classes"); },
  create(body) { return apiPost("/api/v1/teaching-classes", body); },
  get(classId) { return apiGet(`/api/v1/teaching-classes/${classId}`); },
  resourceUsage(classId) { return getResourceUsage(classId); },
  update(classId, body) { return apiPatch(`/api/v1/teaching-classes/${classId}`, body); },
  capacityPreview(classId) { return apiGet(`/api/v1/teaching-classes/${classId}/capacity-preview`); },
  addStudents(classId, emails) { return apiPost(`/api/v1/teaching-classes/${classId}/students`, { emails }); },
  removeStudent(classId, studentId) { return apiDelete(`/api/v1/teaching-classes/${classId}/students/${studentId}`); },
  importStudents(classId, file) {
    const body = new FormData();
    body.append("file", file);
    return apiPostMultipart(`/api/v1/teaching-classes/${classId}/students/import-csv`, body);
  },
  generateWeeks(classId) { return apiPost(`/api/v1/teaching-classes/${classId}/generate-weeks`, {}); },
  replaceMachines(classId, nodes) { return apiPut(`/api/v1/teaching-classes/${classId}/machines`, nodes); },
  selectCourse(classId, courseVersionId) { return apiPut(`/api/v1/teaching-classes/${classId}/course`, { course_version_id: courseVersionId }); },
  replaceWeeks(classId, weeks) { return apiPut(`/api/v1/teaching-classes/${classId}/weeks`, weeks); },
  uploadWeekFile(classId, weekId, file) {
    const body = new FormData();
    body.append("file", file);
    return apiPostMultipart(`/api/v1/teaching-classes/${classId}/weeks/${weekId}/files`, body);
  },
  deleteWeekFile(classId, weekId, fileId) { return apiDelete(`/api/v1/teaching-classes/${classId}/weeks/${weekId}/files/${fileId}`); },
  provision(classId) { return apiPost(`/api/v1/teaching-classes/${classId}/provision`, {}); },
  retryFailed(classId) { return apiPost(`/api/v1/teaching-classes/${classId}/retry-failed`, {}); },
  resetFailed(classId) { return apiPost(`/api/v1/teaching-classes/${classId}/reset-failed`, {}); },
  extend(classId, endDate) { return apiPost(`/api/v1/teaching-classes/${classId}/extend`, { end_date: endDate }); },
  archive(classId, options = {}) {
    return apiPost(`/api/v1/teaching-classes/${classId}/archive`, {
      reclaim_resources: options.reclaimResources ?? true,
      force: options.force ?? false,
    });
  },
  reclaim(classId, options = {}) {
    return apiPost(`/api/v1/teaching-classes/${classId}/reclaim`, {
      reclaim_resources: true,
      force: options.force ?? false,
    });
  },
  provisionStatus(classId) { return apiGet(`/api/v1/teaching-classes/${classId}/provision-status`); },
  // 把建機結果寫回班級（會呼叫 PVE），只在輪詢時低頻呼叫，不要每次都打
  reconcile(classId) { return apiPost(`/api/v1/teaching-classes/${classId}/reconcile`, {}); },
};
