import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { KeyRound, Loader2, PlugZap, Save, ShieldCheck } from "lucide-react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"

import { LdapConfigService, type LdapConfigUpdate } from "@/client"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import { Switch } from "@/components/ui/switch"

interface LdapFormValues {
  enabled: boolean
  server_uri: string
  use_starttls: boolean
  bind_dn: string
  bind_password: string
  user_search_base: string
  user_filter_template: string
  email_attribute: string
  name_attribute: string
  teacher_group_dn: string
  admin_group_dn: string
  auto_create_users: boolean
  connect_timeout_seconds: number
}

function getApiErrorMessage(error: unknown): string {
  if (error && typeof error === "object") {
    const maybe = error as { detail?: string; message?: string }
    if (typeof maybe.detail === "string") return maybe.detail
    if (typeof maybe.message === "string") return maybe.message
  }
  return "未知錯誤"
}

/** 表單值 → API partial update payload（bind_password 留空表示不變更） */
function toUpdatePayload(values: LdapFormValues): LdapConfigUpdate {
  return {
    enabled: values.enabled,
    server_uri: values.server_uri,
    use_starttls: values.use_starttls,
    bind_dn: values.bind_dn,
    bind_password: values.bind_password || null,
    user_search_base: values.user_search_base,
    user_filter_template: values.user_filter_template,
    email_attribute: values.email_attribute,
    name_attribute: values.name_attribute,
    teacher_group_dn: values.teacher_group_dn || null,
    admin_group_dn: values.admin_group_dn || null,
    auto_create_users: values.auto_create_users,
    connect_timeout_seconds: values.connect_timeout_seconds,
  }
}

export default function LdapConfigTab() {
  const queryClient = useQueryClient()

  const { data: config, isLoading } = useQuery({
    queryKey: ["ldapConfig"],
    queryFn: () => LdapConfigService.getConfig(),
  })

  const form = useForm<LdapFormValues>({
    values: config
      ? {
          enabled: config.enabled,
          server_uri: config.server_uri,
          use_starttls: config.use_starttls,
          bind_dn: config.bind_dn,
          bind_password: "",
          user_search_base: config.user_search_base,
          user_filter_template: config.user_filter_template,
          email_attribute: config.email_attribute,
          name_attribute: config.name_attribute,
          teacher_group_dn: config.teacher_group_dn ?? "",
          admin_group_dn: config.admin_group_dn ?? "",
          auto_create_users: config.auto_create_users,
          connect_timeout_seconds: config.connect_timeout_seconds,
        }
      : undefined,
  })

  const saveMutation = useMutation({
    mutationFn: (data: LdapConfigUpdate) =>
      LdapConfigService.updateConfig({ requestBody: data }),
    onSuccess: () => {
      toast.success("LDAP 設定已儲存")
      form.setValue("bind_password", "")
      queryClient.invalidateQueries({ queryKey: ["ldapConfig"] })
    },
    onError: (error) => {
      toast.error(`儲存失敗：${getApiErrorMessage(error)}`)
    },
  })

  const testMutation = useMutation({
    mutationFn: (data: LdapConfigUpdate) =>
      LdapConfigService.testConnection({ requestBody: data }),
    onSuccess: (result) => {
      if (result.ok) {
        toast.success(result.message || "LDAP 連線測試成功")
      } else {
        toast.error(result.message || "LDAP 連線測試失敗")
      }
    },
    onError: (error) => {
      toast.error(`測試失敗：${getApiErrorMessage(error)}`)
    },
  })

  const onSave = (values: LdapFormValues) => {
    saveMutation.mutate(toUpdatePayload(values))
  }

  const onTest = (values: LdapFormValues) => {
    testMutation.mutate(toUpdatePayload(values))
  }

  if (isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const textField = (
    name: keyof LdapFormValues,
    label: string,
    opts: {
      placeholder?: string
      description?: string
      required?: boolean
      type?: string
    } = {},
  ) => (
    <FormField
      control={form.control}
      name={name}
      rules={opts.required ? { required: "必填" } : undefined}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            <Input
              type={opts.type ?? "text"}
              placeholder={opts.placeholder}
              {...field}
              value={field.value as string | number}
              onChange={(e) =>
                field.onChange(
                  opts.type === "number"
                    ? e.target.valueAsNumber
                    : e.target.value,
                )
              }
            />
          </FormControl>
          {opts.description && (
            <FormDescription>{opts.description}</FormDescription>
          )}
          <FormMessage />
        </FormItem>
      )}
    />
  )

  return (
    <Form {...form}>
      <div className="space-y-5">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheck className="h-4 w-4" />
              LDAP / Active Directory 登入
            </CardTitle>
            <CardDescription>
              啟用後登入頁會顯示「校園帳號」分頁，以校方目錄帳號驗證登入。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <FormField
              control={form.control}
              name="enabled"
              render={({ field }) => (
                <FormItem className="flex flex-row items-center justify-between rounded-lg border p-3">
                  <div className="space-y-0.5">
                    <FormLabel>啟用 LDAP 登入</FormLabel>
                    <FormDescription>
                      啟用前建議先以「測試連線」驗證設定
                    </FormDescription>
                  </div>
                  <FormControl>
                    <Switch
                      checked={field.value}
                      onCheckedChange={field.onChange}
                    />
                  </FormControl>
                </FormItem>
              )}
            />

            <div className="grid gap-4 md:grid-cols-2">
              {textField("server_uri", "伺服器 URI", {
                placeholder: "ldap://dc.example.edu:389 或 ldaps://...",
                required: true,
              })}
              {textField("connect_timeout_seconds", "連線逾時（秒）", {
                type: "number",
              })}
            </div>
            <FormField
              control={form.control}
              name="use_starttls"
              render={({ field }) => (
                <FormItem className="flex flex-row items-center justify-between rounded-lg border p-3">
                  <div className="space-y-0.5">
                    <FormLabel>使用 StartTLS</FormLabel>
                    <FormDescription>
                      在 ldap:// 連線上升級為加密連線（ldaps:// 不需要）
                    </FormDescription>
                  </div>
                  <FormControl>
                    <Switch
                      checked={field.value}
                      onCheckedChange={field.onChange}
                    />
                  </FormControl>
                </FormItem>
              )}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <KeyRound className="h-4 w-4" />
              服務帳號與使用者搜尋
            </CardTitle>
            <CardDescription>
              以服務帳號 bind 後搜尋使用者，再以使用者密碼驗證。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              {textField("bind_dn", "Bind DN", {
                placeholder: "CN=svc-skylab,OU=Service,DC=example,DC=edu",
              })}
              {textField("bind_password", "Bind 密碼", {
                type: "password",
                placeholder: config?.bind_password_set
                  ? "已設定（留空表示不變）"
                  : "輸入服務帳號密碼",
              })}
              {textField("user_search_base", "使用者搜尋 Base DN", {
                placeholder: "OU=Users,DC=example,DC=edu",
              })}
              {textField("user_filter_template", "使用者過濾範本", {
                placeholder: "(sAMAccountName={username}) 或 (uid={username})",
                description: "{username} 會代入登入時輸入的帳號",
              })}
              {textField("email_attribute", "Email 屬性", {
                placeholder: "mail",
              })}
              {textField("name_attribute", "姓名屬性", {
                placeholder: "displayName",
              })}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">帳號建立與角色對映</CardTitle>
            <CardDescription>
              首次登入自動建立帳號（預設 student），依群組 DN 對映角色。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <FormField
              control={form.control}
              name="auto_create_users"
              render={({ field }) => (
                <FormItem className="flex flex-row items-center justify-between rounded-lg border p-3">
                  <div className="space-y-0.5">
                    <FormLabel>自動建立帳號</FormLabel>
                    <FormDescription>
                      關閉後僅已存在的本地帳號可用 LDAP 登入
                    </FormDescription>
                  </div>
                  <FormControl>
                    <Switch
                      checked={field.value}
                      onCheckedChange={field.onChange}
                    />
                  </FormControl>
                </FormItem>
              )}
            />
            <div className="grid gap-4 md:grid-cols-2">
              {textField("teacher_group_dn", "教師群組 DN（選填）", {
                placeholder: "CN=Teachers,OU=Groups,DC=example,DC=edu",
                description: "使用者屬於此群組時建立為 teacher 角色",
              })}
              {textField("admin_group_dn", "管理員群組 DN（選填）", {
                placeholder: "CN=SkyLabAdmins,OU=Groups,DC=example,DC=edu",
                description: "使用者屬於此群組時建立為 admin 角色",
              })}
            </div>
          </CardContent>
        </Card>

        <div className="flex justify-end gap-2">
          <LoadingButton
            type="button"
            variant="outline"
            loading={testMutation.isPending}
            disabled={testMutation.isPending}
            onClick={form.handleSubmit(onTest)}
          >
            <PlugZap className="mr-2 h-4 w-4" />
            測試連線
          </LoadingButton>
          <LoadingButton
            type="button"
            loading={saveMutation.isPending}
            disabled={saveMutation.isPending}
            onClick={form.handleSubmit(onSave)}
          >
            <Save className="mr-2 h-4 w-4" />
            儲存 LDAP 設定
          </LoadingButton>
        </div>
      </div>
    </Form>
  )
}
