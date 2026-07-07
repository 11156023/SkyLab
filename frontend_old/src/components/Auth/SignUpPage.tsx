import { zodResolver } from "@hookform/resolvers/zod"
import { Link as RouterLink } from "@tanstack/react-router"
import { useMemo } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

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
import useAuth from "@/hooks/useAuth"

export function SignUpPage() {
  const { signUpMutation } = useAuth()
  const { t } = useTranslation(["auth", "validation"])

  const formSchema = useMemo(
    () =>
      z
        .object({
          email: z.email({ message: t("validation:email.invalid") }),
          full_name: z
            .string()
            .min(1, { message: t("validation:name.fullNameRequired") }),
          password: z
            .string()
            .min(1, { message: t("validation:password.required") })
            .min(8, {
              message: t("validation:password.minLength", { count: 8 }),
            }),
          confirm_password: z
            .string()
            .min(1, { message: t("validation:password.confirmRequired") }),
        })
        .refine((data) => data.password === data.confirm_password, {
          message: t("validation:password.mismatch"),
          path: ["confirm_password"],
        }),
    [t],
  )

  type FormData = z.infer<typeof formSchema>

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      email: "",
      full_name: "",
      password: "",
      confirm_password: "",
    },
  })

  const onSubmit = (data: FormData) => {
    if (signUpMutation.isPending) return

    const { confirm_password: _confirm_password, ...submitData } = data
    signUpMutation.mutate(submitData)
  }

  return (
    <AuthLayout>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-6"
        >
          <div className="flex flex-col items-center gap-2 text-center">
            <h1 className="text-2xl font-bold">{t("auth:signup.title")}</h1>
          </div>

          <div className="grid gap-4">
            <FormField
              control={form.control}
              name="full_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("auth:signup.fullName")}</FormLabel>
                  <FormControl>
                    <Input
                      data-testid="full-name-input"
                      placeholder="User"
                      type="text"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("auth:signup.email")}</FormLabel>
                  <FormControl>
                    <Input
                      data-testid="email-input"
                      placeholder="user@example.com"
                      type="email"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("auth:signup.password")}</FormLabel>
                  <FormControl>
                    <PasswordInput
                      data-testid="password-input"
                      placeholder={t("auth:signup.password")}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="confirm_password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("auth:signup.confirmPassword")}</FormLabel>
                  <FormControl>
                    <PasswordInput
                      data-testid="confirm-password-input"
                      placeholder={t("auth:signup.confirmPassword")}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <LoadingButton
              type="submit"
              className="w-full"
              loading={signUpMutation.isPending}
            >
              {t("auth:signup.submitButton")}
            </LoadingButton>
          </div>

          <div className="text-center text-sm">
            {t("auth:signup.hasAccount")}{" "}
            <RouterLink to="/login" className="underline underline-offset-4">
              {t("auth:signup.loginLink")}
            </RouterLink>
          </div>
        </form>
      </Form>
    </AuthLayout>
  )
}
