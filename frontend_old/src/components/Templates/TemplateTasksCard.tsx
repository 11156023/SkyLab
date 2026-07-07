import { useQuery } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { TEMPLATE_TASK_LABEL, TemplatesAPI } from "@/services/templates"
import { TaskProgressBar, TaskStatusBadge } from "./TemplateBadges"

export function TemplateTasksCard() {
  const { data } = useQuery({
    queryKey: ["template-tasks"],
    queryFn: () => TemplatesAPI.listTasks(20),
    refetchInterval: (query) => {
      const tasks = query.state.data?.data ?? []
      return tasks.some((t) => t.status === "queued" || t.status === "running")
        ? 3000
        : 15000
    },
  })

  const tasks = data?.data ?? []
  if (tasks.length === 0) return null

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">最近任務</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {tasks.slice(0, 8).map((task) => {
          const resultVmid = task.resource_vmid
          const done = task.status === "succeeded"
          return (
            <div key={task.id} className="space-y-1.5">
              <div className="flex items-center justify-between gap-2 text-sm">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="truncate">
                    {TEMPLATE_TASK_LABEL[task.task_type] ?? task.task_type}
                  </span>
                  {done &&
                    resultVmid &&
                    task.task_type === "template.clone" && (
                      <Link
                        to="/my-resources"
                        className="shrink-0 text-xs text-sky-600 hover:underline dark:text-sky-400"
                      >
                        VMID {resultVmid} → 前往資源頁
                      </Link>
                    )}
                </div>
                <TaskStatusBadge status={task.status} />
              </div>
              <TaskProgressBar progress={task.progress} status={task.status} />
              {task.error && (
                <p className="text-xs text-red-600 dark:text-red-400">
                  {task.error}
                </p>
              )}
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}
