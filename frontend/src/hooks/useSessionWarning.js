/**
 * Polls the signed-in user's running machines for imminent auto-stop or expiry
 * warnings. The backend returns all owned machine statuses in one request.
 *
 * A warning is acknowledged as soon as it is shown. The acknowledgement is
 * persisted per user and per warning event, so refreshes and later sign-ins do
 * not reopen the same dialog. Multiple machines are still shown individually.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useMySessionStatusesQuery } from "../queries/resources";

const POLL_INTERVAL_MS = 30_000;
const EMPTY = Object.freeze([]);
const STORAGE_PREFIX = "skylab:session-warnings:v2";
const MAX_SEEN_WARNINGS = 200;

function storageKey(userId) {
  return `${STORAGE_PREFIX}:${encodeURIComponent(String(userId))}`;
}

function loadSeen(userId) {
  if (userId == null) return new Set();
  try {
    const value = JSON.parse(localStorage.getItem(storageKey(userId)) ?? "[]");
    return new Set(Array.isArray(value) ? value.filter((item) => typeof item === "string") : []);
  } catch {
    return new Set();
  }
}

// Only called once the hook has confirmed a signed-in userId.
function saveSeen(userId, seen) {
  try {
    const values = [...seen].slice(-MAX_SEEN_WARNINGS);
    localStorage.setItem(storageKey(userId), JSON.stringify(values));
  } catch {
    // Warnings still work for the current page when storage is unavailable.
  }
}

export function sessionWarningId(status) {
  const reason = status.warn_reason ?? "unknown";
  const deadline = reason === "expiry" ? status.expiry_at : status.auto_stop_at;
  return `${status.vmid}:${reason}:${deadline ?? "unknown"}`;
}

export default function useSessionWarning(userId) {
  const [active, setActive] = useState(null);
  const seenRef = useRef(new Set());

  useEffect(() => {
    seenRef.current = loadSeen(userId);
    setActive(null);
  }, [userId]);

  // Polled through TanStack Query (paused while the tab is hidden). Structural
  // sharing keeps the same array when the batch response has not changed, so the
  // layout does not redraw; a failed poll keeps the last statuses and the next
  // poll retries. The cache is cleared on sign-out, so another user never sees
  // the previous user's statuses.
  const statusesQuery = useMySessionStatusesQuery({
    enabled: userId != null,
    refetchInterval: POLL_INTERVAL_MS,
  });
  const statuses = userId == null ? EMPTY : statusesQuery.data ?? EMPTY;

  useEffect(() => {
    if (userId == null) return;

    if (active) {
      const activeId = sessionWarningId(active);
      const isStillCurrent = statuses.some(
        (status) => status.should_warn && sessionWarningId(status) === activeId,
      );
      if (!isStillCurrent) setActive(null);
      return;
    }

    const next = statuses.find(
      (status) => status.should_warn && !seenRef.current.has(sessionWarningId(status)),
    );
    if (!next) return;

    // Record on display so reloading before closing cannot repeat the warning.
    seenRef.current.add(sessionWarningId(next));
    saveSeen(userId, seenRef.current);
    setActive(next);
  }, [active, statuses, userId]);

  const dismiss = useCallback(() => setActive(null), []);

  return { active, dismiss };
}
