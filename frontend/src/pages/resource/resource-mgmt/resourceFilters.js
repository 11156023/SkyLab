/**
 * 資源管理的搜尋與狀態篩選。管理員清單本來就一次載入全部機器，在前端篩就好；
 * 機器多到要分頁時再改成後端查詢。
 */

/* 狀態切換的分組；「全部」不在這裡，代表不過濾 */
export const STATUS_FILTERS = {
  running: ["running"],
  stopped: ["stopped"],
};

/* 空白分開的每個詞都要命中，例如「王 192.168」＝王同學那台 192.168 開頭的 */
function words(query) {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

function hits(fields, terms) {
  if (!terms.length) return true;
  const text = fields.filter((value) => value != null && value !== "").join(" ").toLowerCase();
  return terms.every((term) => text.includes(term));
}

function statusMatches(status, filter) {
  const allowed = STATUS_FILTERS[filter];
  return !allowed || allowed.includes(status);
}

/** 單台列：比對名稱、VMID、IP、擁有者姓名與信箱 */
export function filterResources(resources, { query = "", status = "all" } = {}) {
  const terms = words(query);
  return resources.filter((r) => statusMatches(r.status, status)
    && hits([r.name, r.vmid, r.ip_address, r.owner_name, r.owner_email], terms));
}

/**
 * 環境群組：群組名稱也算進比對（搜班級名整組都出來），狀態看每台機器。
 * 只留有機器符合的群組，回傳 [{ group, machines }]，machines 是符合的那幾台。
 */
export function filterGroups(groups, { query = "", status = "all" } = {}) {
  const terms = words(query);
  return groups.flatMap((group) => {
    const machines = group.machines.filter((m) => statusMatches(m.status, status)
      && hits([group.title, m.name, m.vmid, m.ip, m.ownerName, m.resource?.name, m.resource?.owner_email], terms));
    return machines.length ? [{ group, machines }] : [];
  });
}

/** 狀態切換上的台數徽章（不受搜尋影響，跟班級管理頁一樣） */
export function statusCounts(resources) {
  const counts = { all: resources.length };
  Object.entries(STATUS_FILTERS).forEach(([key, statuses]) => {
    counts[key] = resources.filter((r) => statuses.includes(r.status)).length;
  });
  return counts;
}
