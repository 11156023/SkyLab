import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { BookCopy, Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import { toast } from "sonner"

import {
  type ApiError,
  GroupsService,
  type ResourcePublic,
  ResourcesService,
  type VmTemplatePublic,
  type VmTemplateVisibility,
} from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
import useAuth from "@/hooks/useAuth"
import { TemplatesAPI } from "@/services/templates"

function errorMessage(error: unknown, fallback: string) {
  const apiError = error as ApiError & { body?: { detail?: string } }
  return apiError.body?.detail ?? apiError.message ?? fallback
}

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 編輯模式時傳入既有範本；未傳為「從 VM 建立範本」 */
  template?: VmTemplatePublic | null
}

export function TemplateFormDialog({ open, onOpenChange, template }: Props) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const isEdit = Boolean(template)
  const isAdmin = user?.role === "admin" || user?.is_superuser === true

  const [sourceVmid, setSourceVmid] = useState("")
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [visibility, setVisibility] = useState<VmTemplateVisibility>("groups")
  const [groupIds, setGroupIds] = useState<string[]>([])
  const [defaultCores, setDefaultCores] = useState("")
  const [defaultMemory, setDefaultMemory] = useState("")
  const [defaultDisk, setDefaultDisk] = useState("")

  const resourcesQuery = useQuery({
    queryKey: ["template-source-options", isAdmin ? "all" : "mine"],
    queryFn: () =>
      isAdmin
        ? ResourcesService.listResources({})
        : ResourcesService.listMyResources(),
    enabled: open && !isEdit && !!user,
    staleTime: 30_000,
  })

  const groupsQuery = useQuery({
    queryKey: ["template-group-options"],
    queryFn: () => GroupsService.listGroups(),
    enabled: open && !!user,
    staleTime: 30_000,
  })

  useEffect(() => {
    if (!open) return
    if (template) {
      setName(template.name)
      setDescription(template.description ?? "")
      setVisibility(template.visibility)
      setGroupIds(template.group_ids ?? [])
      setDefaultCores(
        template.default_cores ? String(template.default_cores) : "",
      )
      setDefaultMemory(
        template.default_memory ? String(template.default_memory) : "",
      )
      setDefaultDisk(template.default_disk ? String(template.default_disk) : "")
      return
    }
    setSourceVmid("")
    setName("")
    setDescription("")
    setVisibility("groups")
    setGroupIds([])
    setDefaultCores("")
    setDefaultMemory("")
    setDefaultDisk("")
  }, [open, template])

  const mutation = useMutation({
    mutationFn: async (): Promise<unknown> => {
      const numOrNull = (v: string) => (v.trim() ? Number(v) : null)
      const common = {
        name: name.trim(),
        description: description.trim() || null,
        visibility,
        group_ids: visibility === "groups" ? groupIds : [],
        default_cores: numOrNull(defaultCores),
        default_memory: numOrNull(defaultMemory),
        default_disk: numOrNull(defaultDisk),
      }
      if (template) {
        return TemplatesAPI.update(template.id, common)
      }
      return TemplatesAPI.create({
        ...common,
        source_vmid: Number(sourceVmid),
      })
    },
    onSuccess: () => {
      toast.success(
        isEdit
          ? "範本已更新"
          : "已開始轉換範本，來源 VM 會先關機再轉為唯讀範本",
      )
      queryClient.invalidateQueries({ queryKey: ["templates"] })
      queryClient.invalidateQueries({ queryKey: ["template-tasks"] })
      onOpenChange(false)
    },
    onError: (error: unknown) => {
      toast.error(errorMessage(error, isEdit ? "更新範本失敗" : "建立範本失敗"))
    },
  })

  const handleSubmit = () => {
    if (!isEdit && !sourceVmid) {
      toast.error("請選擇要轉換的來源 VM")
      return
    }
    if (!name.trim()) {
      toast.error("請輸入範本名稱")
      return
    }
    if (visibility === "groups" && groupIds.length === 0) {
      toast.error("群組可見模式需要至少選擇一個群組")
      return
    }
    mutation.mutate()
  }

  const resources = (resourcesQuery.data ?? []) as ResourcePublic[]
  const groups = groupsQuery.data?.data ?? []

  const toggleGroup = (groupId: string, checked: boolean) => {
    setGroupIds((prev) =>
      checked ? [...prev, groupId] : prev.filter((id) => id !== groupId),
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <BookCopy className="h-5 w-5 text-sky-500" />
            {isEdit ? "編輯範本" : "把 VM 轉為範本"}
          </DialogTitle>
          <DialogDescription>
            {isEdit
              ? "更新範本的名稱、說明、可見範圍與克隆預設規格。"
              : "選擇一台已裝好環境的母機。轉換會先關機，完成後原 VM 變成唯讀範本，無法再直接開機。"}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {!isEdit && (
            <div className="space-y-2">
              <Label>來源母機</Label>
              <Select value={sourceVmid} onValueChange={setSourceVmid}>
                <SelectTrigger>
                  <SelectValue placeholder="選擇要轉換的 VM/LXC..." />
                </SelectTrigger>
                <SelectContent>
                  {resources
                    .filter((r) => r.vmid != null && !r.is_placeholder)
                    .map((r) => (
                      <SelectItem key={r.vmid} value={String(r.vmid)}>
                        {r.name} (VMID {r.vmid} · {r.type})
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
              {!resourcesQuery.isLoading && resources.length === 0 && (
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  找不到可用的 VM，請先建立並設定好一台母機。
                </p>
              )}
            </div>
          )}

          <div className="space-y-2">
            <Label>範本名稱</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如 Ubuntu 22.04 + Docker 實驗環境"
              maxLength={255}
            />
          </div>

          <div className="space-y-2">
            <Label>說明（選填）</Label>
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="描述這個範本裝了什麼、適合哪些課程使用"
              rows={3}
              maxLength={1000}
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>全域可見（所有使用者都能克隆）</Label>
              <Switch
                checked={visibility === "global"}
                onCheckedChange={(checked) =>
                  setVisibility(checked ? "global" : "groups")
                }
              />
            </div>
            {visibility === "groups" && (
              <div className="rounded-lg border border-border/60 p-3">
                <p className="mb-2 text-xs text-muted-foreground">
                  勾選可以看到這個範本的群組：
                </p>
                {groups.length === 0 ? (
                  <p className="text-xs text-amber-600 dark:text-amber-400">
                    你目前沒有任何群組，請先到 Groups 頁建立。
                  </p>
                ) : (
                  <div className="grid max-h-40 gap-2 overflow-y-auto sm:grid-cols-2">
                    {groups.map((group) => (
                      <div
                        key={group.id}
                        className="flex items-center gap-2 text-sm"
                      >
                        <Checkbox
                          id={`tpl-group-${group.id}`}
                          checked={groupIds.includes(group.id)}
                          onCheckedChange={(checked) =>
                            toggleGroup(group.id, checked === true)
                          }
                        />
                        <Label
                          htmlFor={`tpl-group-${group.id}`}
                          className="cursor-pointer truncate font-normal"
                        >
                          {group.name}
                        </Label>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-2">
              <Label>預設 CPU 核數</Label>
              <Input
                type="number"
                min={1}
                max={64}
                value={defaultCores}
                onChange={(e) => setDefaultCores(e.target.value)}
                placeholder="沿用範本"
              />
            </div>
            <div className="space-y-2">
              <Label>預設記憶體 (MB)</Label>
              <Input
                type="number"
                min={128}
                value={defaultMemory}
                onChange={(e) => setDefaultMemory(e.target.value)}
                placeholder="沿用範本"
              />
            </div>
            <div className="space-y-2">
              <Label>預設磁碟 (GB)</Label>
              <Input
                type="number"
                min={1}
                value={defaultDisk}
                onChange={(e) => setDefaultDisk(e.target.value)}
                placeholder="沿用範本"
              />
            </div>
          </div>
        </div>

        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleSubmit} disabled={mutation.isPending}>
            {mutation.isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            {isEdit ? "儲存變更" : "開始轉換"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
