/**
 * UI for composing a recurring schedule (RRULE) used by group batch
 * provisioning. The picker exposes a "preset" mode that covers ~90% of
 * classroom scenarios (weekly on selected days) and an "advanced" mode where
 * the teacher can edit the raw RRULE string.
 *
 * The component is fully controlled — it raises an empty value when disabled
 * so the caller can persist `null` instead of a placeholder.
 */
import { useEffect, useState } from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"

export type RecurrenceValue = {
  /** RRULE string per RFC 5545 (no leading "RRULE:" prefix). */
  recurrence_rule: string | null
  /** Length of one occurrence window in minutes. */
  recurrence_duration_minutes: number | null
  /** IANA tz; the RRULE is interpreted in this tz. */
  schedule_timezone: string | null
}

const EMPTY: RecurrenceValue = {
  recurrence_rule: null,
  recurrence_duration_minutes: null,
  schedule_timezone: null,
}

const WEEKDAYS: { code: string; label: string }[] = [
  { code: "MO", label: "一" },
  { code: "TU", label: "二" },
  { code: "WE", label: "三" },
  { code: "TH", label: "四" },
  { code: "FR", label: "五" },
  { code: "SA", label: "六" },
  { code: "SU", label: "日" },
]

const TIMEZONES = ["Asia/Taipei", "UTC", "Asia/Tokyo", "America/Los_Angeles"]

type Mode = "preset_weekly" | "preset_daily" | "advanced"

export function RecurrenceSchedulePicker({
  value,
  onChange,
}: {
  value: RecurrenceValue
  onChange: (next: RecurrenceValue) => void
}) {
  const enabled = Boolean(value.recurrence_rule)
  const [mode, setMode] = useState<Mode>("preset_weekly")
  const [days, setDays] = useState<string[]>(["FR"])
  const [hour, setHour] = useState(13)
  const [minute, setMinute] = useState(0)
  const [durationHours, setDurationHours] = useState(4)
  const [timezone, setTimezone] = useState("Asia/Taipei")
  const [advancedRule, setAdvancedRule] = useState("")

  // Re-emit value whenever the underlying inputs change.
  useEffect(() => {
    if (!enabled) {
      onChange(EMPTY)
      return
    }
    let rule: string
    if (mode === "advanced") {
      rule = advancedRule.trim()
    } else if (mode === "preset_daily") {
      rule = `FREQ=DAILY;BYHOUR=${hour};BYMINUTE=${minute}`
    } else {
      const byDay = days.length ? days.join(",") : "FR"
      rule = `FREQ=WEEKLY;BYDAY=${byDay};BYHOUR=${hour};BYMINUTE=${minute}`
    }
    onChange({
      recurrence_rule: rule || null,
      recurrence_duration_minutes:
        rule && durationHours > 0 ? durationHours * 60 : null,
      schedule_timezone: rule ? timezone : null,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    enabled,
    mode,
    days,
    hour,
    minute,
    durationHours,
    timezone,
    advancedRule,
    onChange,
  ])

  return (
    <div className="space-y-4 rounded-md border p-4">
      <div className="flex items-center justify-between">
        <div>
          <Label className="text-base">啟用週期排程</Label>
          <p className="text-sm text-muted-foreground">
            到時間自動開機，課程結束後自動關機
          </p>
        </div>
        <Switch
          checked={enabled}
          onCheckedChange={(v) => {
            if (v) {
              // Trigger first emission with current preset values.
              onChange({
                recurrence_rule: `FREQ=WEEKLY;BYDAY=${days.join(",")};BYHOUR=${hour};BYMINUTE=${minute}`,
                recurrence_duration_minutes: durationHours * 60,
                schedule_timezone: timezone,
              })
            } else {
              onChange(EMPTY)
            }
          }}
        />
      </div>

      {enabled && (
        <>
          <div>
            <Label>排程模式</Label>
            <Select value={mode} onValueChange={(v) => setMode(v as Mode)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="preset_weekly">每週特定日</SelectItem>
                <SelectItem value="preset_daily">每天</SelectItem>
                <SelectItem value="advanced">進階 (RRULE)</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {mode === "preset_weekly" && (
            <div>
              <Label>星期</Label>
              <div className="mt-2 flex gap-2">
                {WEEKDAYS.map((d) => {
                  const active = days.includes(d.code)
                  return (
                    <button
                      type="button"
                      key={d.code}
                      onClick={() =>
                        setDays((prev) =>
                          active
                            ? prev.filter((x) => x !== d.code)
                            : [...prev, d.code],
                        )
                      }
                      className={`h-8 w-8 rounded-md border text-sm ${
                        active
                          ? "bg-primary text-primary-foreground"
                          : "bg-background"
                      }`}
                    >
                      {d.label}
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {mode !== "advanced" && (
            <div className="grid grid-cols-3 gap-3">
              <div>
                <Label>開始時 (24h)</Label>
                <Input
                  type="number"
                  min={0}
                  max={23}
                  value={hour}
                  onChange={(e) =>
                    setHour(Math.max(0, Math.min(23, Number(e.target.value))))
                  }
                />
              </div>
              <div>
                <Label>分鐘</Label>
                <Input
                  type="number"
                  min={0}
                  max={59}
                  value={minute}
                  onChange={(e) =>
                    setMinute(Math.max(0, Math.min(59, Number(e.target.value))))
                  }
                />
              </div>
              <div>
                <Label>持續 (小時)</Label>
                <Input
                  type="number"
                  min={1}
                  max={24}
                  value={durationHours}
                  onChange={(e) =>
                    setDurationHours(
                      Math.max(1, Math.min(24, Number(e.target.value))),
                    )
                  }
                />
              </div>
            </div>
          )}

          {mode === "advanced" && (
            <div className="space-y-3">
              <div>
                <Label>RRULE</Label>
                <Textarea
                  value={advancedRule}
                  onChange={(e) => setAdvancedRule(e.target.value)}
                  placeholder="FREQ=WEEKLY;BYDAY=FR;BYHOUR=13;BYMINUTE=0"
                  rows={2}
                />
              </div>
              <div>
                <Label>持續 (小時)</Label>
                <Input
                  type="number"
                  min={1}
                  max={24}
                  value={durationHours}
                  onChange={(e) =>
                    setDurationHours(
                      Math.max(1, Math.min(24, Number(e.target.value))),
                    )
                  }
                />
              </div>
            </div>
          )}

          <div>
            <Label>時區</Label>
            <Select value={timezone} onValueChange={setTimezone}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TIMEZONES.map((tz) => (
                  <SelectItem key={tz} value={tz}>
                    {tz}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </>
      )}
    </div>
  )
}
