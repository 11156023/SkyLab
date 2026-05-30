/**
 * RubricCard - Structured editable card for a single rubric item
 *
 * Layout:
 *   Row 1: #index + detectable badge (read-only) + delete
 *   Row 2: Title input (user-editable)
 *   Row 3: Description input (user-editable)
 *   Row 4: Detection info section (AI-managed, read-only)
 */

import { GripVertical, Shield, Trash2 } from "lucide-react"
import { useCallback } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

import type { RubricItem } from "../api"
import { getDetectableInfo, getTemplateLabel } from "../api"

type RubricCardProps = {
  item: RubricItem
  index: number
  onChange: (item: RubricItem) => void
  onDelete: () => void
  disabled?: boolean
}

export function RubricCard({
  item,
  index,
  onChange,
  onDelete,
  disabled = false,
}: RubricCardProps) {
  const detectableInfo = getDetectableInfo(item.detectable)
  const checkSteps = item.check_steps ?? []

  const handleFieldChange = useCallback(
    (field: keyof RubricItem, value: string | null) => {
      onChange({ ...item, [field]: value })
    },
    [item, onChange],
  )

  const borderColor =
    item.detectable === "auto"
      ? "border-l-green-500"
      : item.detectable === "partial"
        ? "border-l-yellow-500"
        : "border-l-red-500"

  return (
    <div
      className={cn(
        "group relative rounded-lg border border-l-4 px-5 py-4 transition-all",
        "bg-card/50 hover:bg-card/80",
        borderColor,
      )}
    >
      {/* ── Row 1: Index + Badge (read-only) + Delete ── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <GripVertical className="h-4 w-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-60" />

          <span className="text-sm font-semibold text-muted-foreground">
            #{index + 1}
          </span>

          <span
            className={cn(
              "shrink-0 rounded-full px-2.5 py-1 text-xs font-medium leading-none",
              detectableInfo.className,
            )}
          >
            {detectableInfo.label}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 shrink-0 text-destructive opacity-0 transition-opacity group-hover:opacity-100"
            onClick={onDelete}
            disabled={disabled}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* ── Row 2: Title (user-editable) ── */}
      <div className="mt-3 space-y-1 pl-9">
        <label
          htmlFor={`rubric-title-${item.id}`}
          className="text-xs font-medium text-muted-foreground"
        >
          主題
        </label>
        <Input
          id={`rubric-title-${item.id}`}
          value={item.title}
          onChange={(e) => handleFieldChange("title", e.target.value)}
          placeholder="評分項目名稱"
          disabled={disabled}
          className="h-9 text-sm font-medium"
        />
      </div>

      {/* ── Row 3: Description (user-editable) ── */}
      <div className="mt-2.5 space-y-1 pl-9">
        <label
          htmlFor={`rubric-desc-${item.id}`}
          className="text-xs font-medium text-muted-foreground"
        >
          說明
        </label>
        <Input
          id={`rubric-desc-${item.id}`}
          value={item.description}
          onChange={(e) => handleFieldChange("description", e.target.value)}
          placeholder="評分說明"
          disabled={disabled}
          className="h-9 text-sm"
        />
      </div>

      {/* ── Row 4: Detection info (AI-managed, read-only) ── */}
      {(item.detection_method || item.fallback || checkSteps.length > 0) && (
        <div className="ml-9 mt-3 space-y-2.5 rounded-md border border-dashed bg-muted/20 px-4 py-3">
          <div className="flex items-center gap-1.5">
            <Shield className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-[11px] font-medium tracking-wide text-muted-foreground">
              AI 偵測判斷（僅由 AI 更新）
            </span>
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            {/* Detection method */}
            {item.detection_method && (
              <div className="space-y-0.5">
                <span className="text-[11px] font-medium text-muted-foreground">
                  偵測方式
                </span>
                <p className="rounded bg-background/60 px-2.5 py-1.5 text-xs leading-relaxed">
                  {item.detection_method}
                </p>
              </div>
            )}

            {/* Fallback */}
            {item.fallback && (
              <div className="space-y-0.5">
                <span className="text-[11px] font-medium text-muted-foreground">
                  替代建議
                </span>
                <p className="rounded bg-background/60 px-2.5 py-1.5 text-xs leading-relaxed">
                  {item.fallback}
                </p>
              </div>
            )}
          </div>

          {checkSteps.length > 0 && (
            <div className="space-y-1">
              <span className="text-[11px] font-medium text-muted-foreground">
                評分計劃書
              </span>
              <div className="flex flex-wrap gap-1.5">
                {checkSteps.map((step) => (
                  <span
                    key={`${step.template_key}-${step.command_key}`}
                    className="rounded bg-background/70 px-2 py-1 text-[11px] text-muted-foreground"
                  >
                    {getTemplateLabel(step.template_key)} /{" "}
                    {step.command_label ?? step.command_key}
                    <span className="ml-1 font-mono text-[10px]">
                      {step.command_key}
                    </span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
