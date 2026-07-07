import { useQuery } from "@tanstack/react-query"
import { Clock3 } from "lucide-react"
import {
  startTransition,
  useDeferredValue,
  useEffect,
  useMemo,
  useState,
} from "react"

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { queryKeys } from "@/lib/queryKeys"
import { cn } from "@/lib/utils"
import {
  type VmRequestAvailabilityDay,
  type VmRequestAvailabilityRequest,
  type VmRequestAvailabilityResponse,
  VmRequestAvailabilityService,
  type VmRequestAvailabilitySlot,
} from "@/services/vmRequestAvailability"

type DraftModeProps = {
  mode: "draft"
  draft: VmRequestAvailabilityRequest
  enabled?: boolean
  compact?: boolean
}

type RequestModeProps = {
  mode: "request"
  requestId: string
  enabled?: boolean
  compact?: boolean
}

type AvailabilityRangeValue = {
  start_at: string | null
  end_at: string | null
}

type SharedProps = {
  enabled?: boolean
  compact?: boolean
  value?: AvailabilityRangeValue
  onChange?: (value: AvailabilityRangeValue) => void
}

type Props = (DraftModeProps | RequestModeProps) & SharedProps

const slotTone: Record<
  VmRequestAvailabilitySlot["status"],
  {
    button: string
  }
> = {
  available: {
    button:
      "border-emerald-400/70 bg-emerald-500/10 text-emerald-700 hover:bg-emerald-500/15",
  },
  limited: {
    button:
      "border-amber-400/70 bg-amber-500/10 text-amber-700 hover:bg-amber-500/15",
  },
  unavailable: {
    button:
      "border-rose-300/60 bg-rose-500/10 text-rose-700 opacity-60 cursor-not-allowed",
  },
  policy_blocked: {
    button:
      "border-border bg-muted/40 text-muted-foreground opacity-70 cursor-not-allowed",
  },
}

function isSelectable(slot: VmRequestAvailabilitySlot) {
  return slot.status === "available" || slot.status === "limited"
}

function formatHour(hour: number) {
  return `${String(hour).padStart(2, "0")}:00`
}

function isDraftReady(draft: VmRequestAvailabilityRequest) {
  if (!draft.resource_type || !draft.cores || !draft.memory) return false
  if (draft.resource_type === "vm") return Boolean(draft.disk_size)
  return Boolean(draft.rootfs_size)
}

function getInitialDay(data: VmRequestAvailabilityResponse) {
  return data.days[0]?.date ?? null
}

function getInitialSlot(day: VmRequestAvailabilityDay | undefined) {
  if (!day) return null
  const best = day.slots.find(
    (slot) => day.best_hours.includes(slot.hour) && isSelectable(slot),
  )
  if (best) return best.start_at
  return day.slots.find(isSelectable)?.start_at ?? null
}

function getAllSlots(data: VmRequestAvailabilityResponse | undefined) {
  if (!data) return []
  return data.days
    .flatMap((day) => day.slots)
    .sort(
      (a, b) => new Date(a.start_at).getTime() - new Date(b.start_at).getTime(),
    )
}

function getSelectableRange(
  data: VmRequestAvailabilityResponse | undefined,
  startAt: string | null,
  endAt: string | null,
) {
  if (!data || !startAt) return []
  const slots = getAllSlots(data)
  const startIndex = slots.findIndex((slot) => slot.start_at === startAt)
  if (startIndex < 0) return []

  if (!endAt) return isSelectable(slots[startIndex]) ? [slots[startIndex]] : []

  const endIndex = slots.findIndex((slot) => slot.start_at === endAt)
  if (endIndex < startIndex) return []

  const range = slots.slice(startIndex, endIndex + 1)
  return range.every(isSelectable) ? range : []
}

function getDateFromStartAt(
  data: VmRequestAvailabilityResponse | undefined,
  startAt: string | null,
) {
  if (!data || !startAt) return null
  return (
    data.days.find((day) => day.slots.some((slot) => slot.start_at === startAt))
      ?.date ?? null
  )
}

function getRangeEndSlotStartAt(
  data: VmRequestAvailabilityResponse | undefined,
  endAt: string | null,
) {
  if (!data || !endAt) return null
  return (
    getAllSlots(data).find((slot) => slot.end_at === endAt)?.start_at ?? null
  )
}

function RequestAvailabilitySkeleton({ compact }: { compact?: boolean }) {
  return (
    <Card className={cn(compact && "gap-4 py-4")}>
      <CardHeader className={cn(compact && "px-4 pb-0")}>
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-4 w-64" />
      </CardHeader>
      <CardContent className={cn("space-y-3", compact && "px-4")}>
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-24 w-full" />
      </CardContent>
    </Card>
  )
}

export function RequestAvailabilityPanel(props: Props) {
  const enabled = props.enabled ?? true
  const deferredDraft = useDeferredValue(
    props.mode === "draft" ? props.draft : null,
  )
  const draftReady =
    props.mode === "draft"
      ? isDraftReady(props.draft)
      : Boolean(props.requestId)

  const query = useQuery<VmRequestAvailabilityResponse>({
    queryKey:
      props.mode === "draft"
        ? queryKeys.vmRequests.availability.draft(deferredDraft)
        : queryKeys.vmRequests.availability.byRequest(props.requestId),
    queryFn: () => {
      if (props.mode === "draft") {
        return VmRequestAvailabilityService.preview({
          requestBody: deferredDraft as VmRequestAvailabilityRequest,
        })
      }
      return VmRequestAvailabilityService.getByRequestId({
        requestId: props.requestId,
      })
    },
    enabled: enabled && draftReady,
    staleTime: 30_000,
  })

  const data = query.data
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [rangeStartAt, setRangeStartAt] = useState<string | null>(null)
  const [rangeEndAt, setRangeEndAt] = useState<string | null>(null)

  const selectedDay = useMemo(
    () => data?.days.find((day) => day.date === selectedDate) ?? data?.days[0],
    [data?.days, selectedDate],
  )

  const selectedRange = useMemo(
    () => getSelectableRange(data, rangeStartAt, rangeEndAt),
    [data, rangeStartAt, rangeEndAt],
  )

  useEffect(() => {
    if (!data) return

    const nextDate =
      getDateFromStartAt(data, props.value?.start_at ?? null) ??
      getInitialDay(data)
    const nextDay = data.days.find((day) => day.date === nextDate)
    const nextSlot = props.value?.start_at ?? getInitialSlot(nextDay)
    setSelectedDate(nextDate)
    setRangeStartAt(nextSlot)
    setRangeEndAt(getRangeEndSlotStartAt(data, props.value?.end_at ?? null))
  }, [data, props.value?.start_at, props.value?.end_at])

  useEffect(() => {
    if (!props.onChange) return

    const first = selectedRange[0]
    const last = selectedRange[selectedRange.length - 1]
    props.onChange({
      start_at: first?.start_at ?? null,
      end_at: last?.end_at ?? null,
    })
  }, [props.onChange, selectedRange])

  if (!draftReady) {
    return (
      <Card className={cn(props.compact && "gap-4 py-4")}>
        <CardHeader className={cn(props.compact && "px-4 pb-0")}>
          <CardTitle className="flex items-center gap-2 text-base">
            <Clock3 className="h-4 w-4" />
            可申請時段
          </CardTitle>
          <CardDescription>
            先填完基本規格後，再選日期與連續時段。
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }

  if (query.isLoading) {
    return <RequestAvailabilitySkeleton compact={props.compact} />
  }

  if (query.isError || !data) {
    return (
      <Card className={cn(props.compact && "gap-4 py-4")}>
        <CardHeader className={cn(props.compact && "px-4 pb-0")}>
          <CardTitle className="flex items-center gap-2 text-base">
            <Clock3 className="h-4 w-4" />
            可申請時段
          </CardTitle>
          <CardDescription>目前無法取得時段資料，請稍後再試。</CardDescription>
        </CardHeader>
      </Card>
    )
  }

  return (
    <Card className={cn(props.compact && "gap-4 py-4")}>
      <CardHeader className={cn(props.compact && "px-4 pb-0")}>
        <CardTitle className="flex items-center gap-2 text-base">
          <Clock3 className="h-4 w-4" />
          可申請時段
        </CardTitle>
        <CardDescription>
          先選起始時段，再到任意日期選結束時段，可跨天選擇連續時段。
        </CardDescription>
      </CardHeader>

      <CardContent className={cn("space-y-4", props.compact && "px-4")}>
        <div className="space-y-3">
          <div className="space-y-2">
            <div className="text-sm font-medium">日期</div>
            <div className="flex flex-wrap gap-2">
              {data.days.map((day) => {
                const active = day.date === selectedDay?.date
                return (
                  <button
                    key={day.date}
                    type="button"
                    className={cn(
                      "rounded-lg border px-3 py-2 text-left text-sm transition",
                      active
                        ? "border-primary bg-primary/10 text-primary"
                        : "hover:bg-accent",
                    )}
                    onClick={() =>
                      startTransition(() => {
                        setSelectedDate(day.date)
                      })
                    }
                  >
                    <div className="font-medium">{day.date}</div>
                  </button>
                )
              })}
            </div>
          </div>

          {selectedDay && (
            <div className="space-y-2">
              <div className="text-sm font-medium">時段</div>
              <div className="grid grid-cols-4 gap-2 sm:grid-cols-6 lg:grid-cols-8">
                {selectedDay.slots.map((slot) => {
                  const selectable = isSelectable(slot)
                  const canCompleteRange =
                    rangeStartAt && !rangeEndAt
                      ? (() => {
                          if (!selectable) return false
                          if (slot.start_at === rangeStartAt) return true
                          const startTime = new Date(rangeStartAt).getTime()
                          const endTime = new Date(slot.start_at).getTime()
                          if (endTime <= startTime) return true
                          return (
                            getSelectableRange(
                              data,
                              rangeStartAt,
                              slot.start_at,
                            ).length > 0
                          )
                        })()
                      : selectable
                  const clickable = Boolean(canCompleteRange)
                  const inSelectedRange = selectedRange.some(
                    (item) => item.start_at === slot.start_at,
                  )
                  const isRangeStart = rangeStartAt === slot.start_at
                  const isRangeEnd = rangeEndAt === slot.start_at

                  return (
                    <button
                      key={slot.start_at}
                      type="button"
                      disabled={!clickable}
                      className={cn(
                        "rounded-lg border px-2 py-2 text-center text-sm font-medium transition",
                        slotTone[slot.status].button,
                        clickable && "hover:-translate-y-0.5",
                        inSelectedRange && "ring-2 ring-primary ring-offset-2",
                        isRangeStart &&
                          "shadow-[inset_0_0_0_1px_rgba(255,255,255,0.4)]",
                        isRangeEnd &&
                          "shadow-[inset_0_0_0_1px_rgba(255,255,255,0.4)]",
                      )}
                      onClick={() => {
                        if (!clickable) return

                        if (!rangeStartAt || rangeEndAt) {
                          setRangeStartAt(slot.start_at)
                          setRangeEndAt(null)
                          return
                        }

                        const currentStart =
                          selectedDay.slots.find(
                            (item) => item.start_at === rangeStartAt,
                          ) ??
                          getAllSlots(data).find(
                            (item) => item.start_at === rangeStartAt,
                          )

                        const selectedSlotTime = new Date(
                          slot.start_at,
                        ).getTime()
                        const currentStartTime = currentStart
                          ? new Date(currentStart.start_at).getTime()
                          : Number.NaN

                        if (
                          !currentStart ||
                          selectedSlotTime <= currentStartTime
                        ) {
                          setRangeStartAt(slot.start_at)
                          setRangeEndAt(null)
                          return
                        }

                        const nextRange = getSelectableRange(
                          data,
                          rangeStartAt,
                          slot.start_at,
                        )

                        if (nextRange.length > 0) {
                          setRangeEndAt(slot.start_at)
                        } else {
                          setRangeStartAt(slot.start_at)
                          setRangeEndAt(null)
                        }
                      }}
                    >
                      <div>{formatHour(slot.hour)}</div>
                      <div className="mt-0.5 text-[11px] opacity-80">
                        {isRangeStart ? "起點" : isRangeEnd ? "終點" : "\u00A0"}
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export default RequestAvailabilityPanel
