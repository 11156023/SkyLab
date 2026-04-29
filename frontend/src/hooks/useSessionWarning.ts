/**
 * Polls every running VM the current user owns and surfaces the first one
 * whose backend reports ``should_warn=true``. Returns the SessionStatus +
 * dismiss callback so the layout can render a single shared dialog.
 *
 * Polling cadence is 30s (matches backend's ``practice_warning_minutes``
 * default of 30 — well within margin). The dismiss state is per-vmid so
 * snoozing one warning doesn't suppress others.
 */
import { useQueries, useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"

import { ResourcesService } from "@/client"
import {
  type SessionStatus,
  SessionWarningService,
} from "@/services/sessionWarning"

const POLL_INTERVAL_MS = 30_000

export function useSessionWarning(): {
  active: SessionStatus | null
  dismiss: () => void
} {
  // Pull the current user's VMs so we know which vmids to poll.
  const { data: myResources = [] } = useQuery({
    queryKey: ["sessionStatus", "myResources"],
    queryFn: () => ResourcesService.listMyResources(),
    refetchInterval: POLL_INTERVAL_MS * 4, // less frequent than per-VM poll
  })

  const runningVmids = myResources
    .filter((r: any) => r.status === "running")
    .map((r: any) => r.vmid as number)

  const sessionQueries = useQueries({
    queries: runningVmids.map((vmid) => ({
      queryKey: ["sessionStatus", vmid],
      queryFn: () => SessionWarningService.getStatus(vmid),
      refetchInterval: POLL_INTERVAL_MS,
    })),
  })

  const [dismissed, setDismissed] = useState<Set<number>>(new Set())

  // Reset dismissals when the corresponding warning is no longer active —
  // otherwise the student can't see future warnings on the same VM.
  useEffect(() => {
    setDismissed((prev) => {
      const next = new Set(prev)
      for (const vmid of prev) {
        const q = sessionQueries.find((q) => q.data?.vmid === vmid)
        if (!q?.data?.should_warn) next.delete(vmid)
      }
      return next.size === prev.size ? prev : next
    })
  }, [sessionQueries])

  const active =
    sessionQueries.find(
      (q) =>
        q.data?.should_warn === true &&
        q.data.can_extend &&
        !dismissed.has(q.data.vmid),
    )?.data ?? null

  return {
    active,
    dismiss: () => {
      if (active) {
        setDismissed((prev) => new Set(prev).add(active.vmid))
      }
    },
  }
}
