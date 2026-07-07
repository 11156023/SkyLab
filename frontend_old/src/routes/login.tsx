import { zodResolver } from "@hookform/resolvers/zod"
import { useQuery } from "@tanstack/react-query"
import {
  createFileRoute,
  Link as RouterLink,
  redirect,
  useNavigate,
  useSearch,
} from "@tanstack/react-router"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import type { Body_login_login_access_token as AccessToken } from "@/client"
import { LoginService, OpenAPI } from "@/client"
import { AuthLayout } from "@/components/Common/AuthLayout"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import { PasswordInput } from "@/components/ui/password-input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useAuth, { isLoggedIn } from "@/hooks/useAuth"
import { AuthSessionService } from "@/services/authSession"
import { isTokenExpiredOrNearExpiry } from "@/services/openApiAuth"

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string
            callback: (response: { credential: string }) => void
          }) => void
          renderButton: (
            element: HTMLElement,
            options: Record<string, unknown>,
          ) => void
        }
      }
    }
  }
}

const loginSearchSchema = z.object({
  device_code: z.string().optional(),
})

export const Route = createFileRoute("/login")({
  component: Login,
  validateSearch: loginSearchSchema,
  beforeLoad: async ({ search }) => {
    // If device_code is present and user is already logged in,
    // don't redirect — let the component approve the device code first
    if (isLoggedIn() && !(search as { device_code?: string }).device_code) {
      throw redirect({
        to: "/",
      })
    }
  },
  head: () => ({
    meta: [
      {
        title: "Log In - SkyLab",
      },
    ],
  }),
})

async function approveDeviceCode(deviceCode: string): Promise<boolean> {
  try {
    const resolvedToken =
      typeof OpenAPI.TOKEN === "function"
        ? await (
            OpenAPI.TOKEN as (options: {
              method: string
              url: string
            }) => Promise<string>
          )({
            method: "POST",
            url: "/api/v1/desktop-client/auth/approve",
          })
        : (OpenAPI.TOKEN as string | undefined)
    let token = resolvedToken || AuthSessionService.getAccessToken()
    if (token && isTokenExpiredOrNearExpiry(token, Date.now(), 0)) {
      const refreshed = await AuthSessionService.refreshAccessToken()
      token = refreshed ? AuthSessionService.getAccessToken() : null
    }
    if (!token) {
      console.warn("[approveDeviceCode] no access_token in localStorage")
      return false
    }
    const resp = await fetch(
      `${OpenAPI.BASE}/api/v1/desktop-client/auth/approve`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ device_code: deviceCode }),
      },
    )
    if (!resp.ok) {
      const body = await resp.text()
      console.warn("[approveDeviceCode] failed", resp.status, body)
    }
    return resp.ok
  } catch (err) {
    console.warn("[approveDeviceCode] error", err)
    return false
  }
}

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID as
  | string
  | undefined

function Login() {
  const { device_code: deviceCode } = useSearch({ from: "/login" })
  const [deviceApproved, setDeviceApproved] = useState(false)
  const [deviceApprovalError, setDeviceApprovalError] = useState("")
  const [deviceApprovalPending, setDeviceApprovalPending] = useState(false)
  const approveDesktopClient = useCallback(async () => {
    if (!deviceCode || deviceApprovalPending) return false
    setDeviceApprovalPending(true)
    setDeviceApprovalError("")
    const ok = await approveDeviceCode(deviceCode)
    setDeviceApprovalPending(false)
    if (ok) {
      setDeviceApproved(true)
    } else {
      setDeviceApprovalError("桌面連線工具授權失敗，請重新登入或稍後再試。")
    }
    return ok
  }, [deviceCode, deviceApprovalPending])
  const { loginMutation, googleLoginMutation, ldapLoginMutation } = useAuth({
    onLoginSuccess: deviceCode
      ? async () => {
          await approveDesktopClient()
          return true // prevent navigate to "/"
        }
      : undefined,
  })

  // 依後端啟用的登入方式決定是否顯示「校園帳號」分頁（公開端點）
  const { data: loginMethods } = useQuery({
    queryKey: ["loginMethods"],
    queryFn: () => LoginService.loginMethods(),
    staleTime: 5 * 60_000,
    retry: false,
  })
  const ldapEnabled = Boolean(loginMethods?.ldap)
  const { t } = useTranslation(["auth", "validation"])
  const navigate = useNavigate()
  const googleButtonRef = useRef<HTMLDivElement>(null)
  const mutationRef = useRef(googleLoginMutation)
  mutationRef.current = googleLoginMutation

  // If user is already logged in and device_code is present, approve immediately
  useEffect(() => {
    if (!deviceCode || !isLoggedIn() || deviceApproved) return
    void approveDesktopClient()
  }, [deviceCode, deviceApproved, approveDesktopClient])

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return
    // Skip Google button setup on the success screen — the DOM node won't exist.
    if (deviceApproved) return

    const init = () => {
      if (!window.google || !googleButtonRef.current) return
      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: ({ credential }) => {
          mutationRef.current.mutate(credential)
        },
      })
      window.google.accounts.id.renderButton(googleButtonRef.current, {
        type: "standard",
        theme: "outline",
        size: "large",
        width: googleButtonRef.current.offsetWidth || 400,
        logo_alignment: "center",
      })
    }

    if (window.google) {
      init()
    } else {
      const script = document.querySelector(
        'script[src*="accounts.google.com/gsi/client"]',
      )
      script?.addEventListener("load", init)
      return () => script?.removeEventListener("load", init)
    }
  }, [deviceApproved])

  const formSchema = useMemo(
    () =>
      z.object({
        username: z.email({ message: t("validation:email.invalid") }),
        password: z
          .string()
          .min(1, { message: t("validation:password.required") })
          .min(8, {
            message: t("validation:password.minLength", { count: 8 }),
          }),
      }) satisfies z.ZodType<AccessToken>,
    [t],
  )

  type FormData = z.infer<typeof formSchema>

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      username: "",
      password: "",
    },
  })

  const onSubmit = (data: FormData) => {
    if (loginMutation.isPending) return
    loginMutation.mutate(data)
  }

  const ldapFormSchema = useMemo(
    () =>
      z.object({
        username: z.string().min(1, { message: "請輸入校園帳號" }),
        password: z
          .string()
          .min(1, { message: t("validation:password.required") }),
      }),
    [t],
  )

  type LdapFormData = z.infer<typeof ldapFormSchema>

  const ldapForm = useForm<LdapFormData>({
    resolver: zodResolver(ldapFormSchema),
    mode: "onBlur",
    defaultValues: {
      username: "",
      password: "",
    },
  })

  const onLdapSubmit = (data: LdapFormData) => {
    if (ldapLoginMutation.isPending) return
    ldapLoginMutation.mutate(data)
  }

  // Show success screen after device code approval.
  // IMPORTANT: Must be rendered AFTER all hooks above — an early return before
  // hooks would violate the Rules of Hooks and crash on re-render.
  if (deviceApproved) {
    return (
      <AuthLayout>
        <div className="flex flex-col items-center gap-4 text-center py-8">
          <div className="rounded-full bg-green-100 p-3 dark:bg-green-900">
            <svg
              aria-hidden="true"
              className="h-8 w-8 text-green-600 dark:text-green-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M5 13l4 4L19 7"
              />
            </svg>
          </div>
          <h2 className="text-xl font-bold">
            {t("auth:deviceApprovalSuccess.title", {
              defaultValue: "授權成功",
            })}
          </h2>
          <p className="text-muted-foreground">
            {t("auth:deviceApprovalSuccess.description", {
              defaultValue: "桌面連線工具已授權，你可以關閉此頁面。",
            })}
          </p>
          <button
            type="button"
            className="mt-4 text-sm text-muted-foreground underline"
            onClick={() => navigate({ to: "/" })}
          >
            {t("auth:deviceApprovalSuccess.goHome", {
              defaultValue: "前往主頁",
            })}
          </button>
        </div>
      </AuthLayout>
    )
  }

  const emailLoginForm = (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="grid gap-4">
        <FormField
          control={form.control}
          name="username"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("auth:login.email")}</FormLabel>
              <FormControl>
                <Input
                  data-testid="email-input"
                  placeholder="user@example.com"
                  type="email"
                  {...field}
                />
              </FormControl>
              <FormMessage className="text-xs" />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-center">
                <FormLabel>{t("auth:login.password")}</FormLabel>
                <RouterLink
                  to="/recover-password"
                  className="ml-auto text-sm underline-offset-4 hover:underline"
                >
                  {t("auth:login.forgotPassword")}
                </RouterLink>
              </div>
              <FormControl>
                <PasswordInput
                  data-testid="password-input"
                  placeholder={t("auth:login.password")}
                  {...field}
                />
              </FormControl>
              <FormMessage className="text-xs" />
            </FormItem>
          )}
        />

        <LoadingButton type="submit" loading={loginMutation.isPending}>
          {t("auth:login.submitButton")}
        </LoadingButton>
      </form>
    </Form>
  )

  const ldapLoginForm = (
    <Form {...ldapForm}>
      <form
        onSubmit={ldapForm.handleSubmit(onLdapSubmit)}
        className="grid gap-4"
      >
        <FormField
          control={ldapForm.control}
          name="username"
          render={({ field }) => (
            <FormItem>
              <FormLabel>校園帳號</FormLabel>
              <FormControl>
                <Input
                  data-testid="ldap-username-input"
                  placeholder="學號 / 教職員帳號"
                  autoComplete="username"
                  {...field}
                />
              </FormControl>
              <FormMessage className="text-xs" />
            </FormItem>
          )}
        />

        <FormField
          control={ldapForm.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("auth:login.password")}</FormLabel>
              <FormControl>
                <PasswordInput
                  data-testid="ldap-password-input"
                  placeholder={t("auth:login.password")}
                  {...field}
                />
              </FormControl>
              <FormMessage className="text-xs" />
            </FormItem>
          )}
        />

        <LoadingButton type="submit" loading={ldapLoginMutation.isPending}>
          {t("auth:login.submitButton")}
        </LoadingButton>
      </form>
    </Form>
  )

  return (
    <AuthLayout>
      <div className="flex flex-col gap-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <h1 className="text-2xl font-bold">{t("auth:login.title")}</h1>
        </div>

        {deviceCode && (
          <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-center text-sm text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200">
            {deviceApprovalPending
              ? "正在授權桌面連線工具..."
              : "請登入以授權桌面連線工具"}
          </div>
        )}

        {deviceApprovalError && (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 text-center text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
            <p>{deviceApprovalError}</p>
            {isLoggedIn() && (
              <button
                type="button"
                className="mt-2 underline underline-offset-4"
                disabled={deviceApprovalPending}
                onClick={() => void approveDesktopClient()}
              >
                重新授權
              </button>
            )}
          </div>
        )}

        {ldapEnabled ? (
          <Tabs defaultValue="password">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="password">Email</TabsTrigger>
              <TabsTrigger value="ldap">校園帳號</TabsTrigger>
            </TabsList>
            <TabsContent value="password" className="mt-4">
              {emailLoginForm}
            </TabsContent>
            <TabsContent value="ldap" className="mt-4">
              {ldapLoginForm}
            </TabsContent>
          </Tabs>
        ) : (
          emailLoginForm
        )}

        {GOOGLE_CLIENT_ID && (
          <>
            <div className="relative text-center text-sm after:absolute after:inset-0 after:top-1/2 after:z-0 after:flex after:items-center after:border-t after:border-border">
              <span className="relative z-10 bg-background px-2 text-muted-foreground">
                {t("auth:login.orContinueWith")}
              </span>
            </div>
            <div ref={googleButtonRef} className="flex justify-center" />
          </>
        )}

        <div className="text-center text-sm">
          {t("auth:login.noAccount")}{" "}
          <RouterLink to="/signup" className="underline underline-offset-4">
            {t("auth:login.signUpLink")}
          </RouterLink>
        </div>
      </div>
    </AuthLayout>
  )
}
