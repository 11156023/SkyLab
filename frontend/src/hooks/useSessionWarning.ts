/**
 * Polls every running VM the current user owns and surfaces the first one
 * whose backend reports ``should_warn=true``. Returns the SessionStatus +
 * dismiss callbacks so the layout can render a single shared dialog.
 *
 * Polling cadence is 30s (matches backend's ``practice_warning_minutes``
 * default of 30 — well within margin). The dismiss state is per-vmid so
 * snoozing one warning doesn't suppress others.
 *
 * ``dismissPermanent`` stores the current warning key (auto_stop_at or
 * expiry_at) in localStorage so the dialog won't re-appear after a page
 * refresh unless the underlying condition changes (e.g. auto_stop_at updated
 * after the user extends their session).
 */
import { useQueries, useQuery } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"

import { type ResourcePublic, ResourcesService } from "@/client"
import {
  type SessionStatus,
  SessionWarningService,
} from "@/services/sessionWarning"

const POLL_INTERVAL_MS = 30_000
const LS_KEY = "session_warning_dismissed"

type DismissedStore = Record<number, string> // vmid → warning key (ISO timestamp)

function loadDismissed(): DismissedStore {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) ?? "{}") as DismissedStore
  } catch {
    return {}
  }
}

function saveDismissed(store: DismissedStore) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(store))
  } catch {
    // Ignore write failures (e.g. storage quota exceeded or restricted context).
  }
}

function warningKey(status: SessionStatus): string {
  return status.auto_stop_at ?? status.expiry_at ?? ""
}

export function useSessionWarning(): {
  active: SessionStatus | null
  dismiss: () => void
  dismissPermanent: () => void
} {
  const { data: myResources = [] } = useQuery<ResourcePublic[]>({
    queryKey: ["sessionStatus", "myResources"],
    queryFn: () => ResourcesService.listMyResources(),
    refetchInterval: POLL_INTERVAL_MS * 4,
  })

  const runningVmids = useMemo(
    () => myResources.filter((r) => r.status === "running").map((r) => r.vmid),
    [myResources],
  )

  const sessionQueries = useQueries({
    queries: runningVmids.map((vmid) => ({
      queryKey: ["sessionStatus", vmid],
      queryFn: () => SessionWarningService.getStatus(vmid),
      refetchInterval: POLL_INTERVAL_MS,
    })),
  })

  // In-memory snooze (clears on refresh)
  const [dismissed, setDismissed] = useState<Set<number>>(new Set())
  // Permanent dismiss loaded from localStorage
  const [permanent, setPermanent] = useState<DismissedStore>(loadDismissed)

  const warnByVmid = useMemo(() => {
    const map = new Map<number, boolean>()
    for (const q of sessionQueries) {
      if (q.data) map.set(q.data.vmid, q.data.should_warn)
    }
    return map
  }, [sessionQueries])

  // Reset in-memory dismissals when should_warn goes false
  useEffect(() => {
    setDismissed((prev) => {
      if (prev.size === 0) return prev
      const next = new Set(prev)
      for (const vmid of prev) {
        if (warnByVmid.get(vmid) === false) next.delete(vmid)
      }
      return next.size === prev.size ? prev : next
    })
  }, [warnByVmid])

  // Clear stale permanent dismissals when the warning key changes (e.g. after extend)
  useEffect(() => {
    setPermanent((prev) => {
      let changed = false
      const next = { ...prev }
      for (const q of sessionQueries) {
        if (!q.data) continue
        const { vmid } = q.data
        if (vmid in next) {
          const currentKey = warningKey(q.data)
          if (next[vmid] !== currentKey) {
            delete next[vmid]
            changed = true
          }
        }
      }
      if (changed) saveDismissed(next)
      return changed ? next : prev
    })
  }, [sessionQueries])

  const active =
    sessionQueries.find((q) => {
      if (!q.data?.should_warn) return false
      const { vmid } = q.data
      if (dismissed.has(vmid)) return false
      if (permanent[vmid] === warningKey(q.data)) return false
      return true
    })?.data ?? null

  return {
    active,
    dismiss: () => {
      if (active) setDismissed((prev) => new Set(prev).add(active.vmid))
    },
    dismissPermanent: () => {
      if (active) {
        const key = warningKey(active)
        setPermanent((prev) => {
          const next = { ...prev, [active.vmid]: key }
          saveDismissed(next)
          return next
        })
        setDismissed((prev) => new Set(prev).add(active.vmid))
      }
    },
  }
}
