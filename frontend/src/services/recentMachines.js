export const RECENT_MACHINES_EVENT = "skylab:recent-machines";
const storageKey = (userId) => `skylab:recent-machines:${userId}`;

export function readRecentMachines(userId) {
  if (!userId) return [];
  try {
    const entries = JSON.parse(localStorage.getItem(storageKey(userId)) ?? "[]");
    if (!Array.isArray(entries)) return [];
    return entries.filter((entry) => entry && Number.isInteger(entry.vmid)
      && entry.vmid > 0 && Number.isFinite(entry.usedAt) && entry.usedAt > 0)
      .sort((a, b) => b.usedAt - a.usedAt).slice(0, 20);
  } catch {
    return [];
  }
}

// Store identifiers only; names and availability always come from the current user's API.
export function recordMachineUse(userId, vmid) {
  const id = Number(vmid);
  if (!userId || !Number.isInteger(id) || id <= 0) return;
  try {
    const entries = readRecentMachines(userId).filter((entry) => entry.vmid !== id);
    localStorage.setItem(storageKey(userId), JSON.stringify([
      { vmid: id, usedAt: Date.now() }, ...entries,
    ].slice(0, 20)));
    window.dispatchEvent(new Event(RECENT_MACHINES_EVENT));
  } catch {
    // Storage restrictions must never prevent a console connection.
  }
}

export function selectRecentMachines(resources, history, limit = 4) {
  const byId = new Map(resources.map((resource) => [Number(resource.vmid), resource]));
  return history.filter((entry) => byId.has(entry.vmid)).slice(0, limit)
    .map((entry) => ({ ...byId.get(entry.vmid), usedAt: entry.usedAt }));
}
