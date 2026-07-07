import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import { UsersService, type UserUpdateMe } from "@/client"
import UserAvatar from "@/components/Common/UserAvatar"
import { Button } from "@/components/ui/button"
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
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"
import { cn } from "@/lib/utils"
import { handleError } from "@/utils"

const UserInformation = () => {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [editMode, setEditMode] = useState(false)
  const { user: currentUser } = useAuth()
  const { t } = useTranslation(["settings", "validation", "messages"])

  const formSchema = useMemo(
    () =>
      z.object({
        full_name: z.string().max(30).optional(),
        email: z.email({ message: t("validation:email.invalid") }),
        avatar_url: z
          .union([
            z.url({ message: t("validation:url.invalid") }),
            z.literal(""),
          ])
          .optional(),
      }),
    [t],
  )

  type FormData = z.infer<typeof formSchema>

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      full_name: currentUser?.full_name ?? undefined,
      email: currentUser?.email,
      avatar_url: currentUser?.avatar_url ?? "",
    },
  })

  const previewName = editMode
    ? form.watch("full_name") || currentUser?.full_name
    : currentUser?.full_name
  const previewAvatarUrl = editMode
    ? form.watch("avatar_url") || undefined
    : currentUser?.avatar_url || undefined

  const toggleEditMode = () => {
    setEditMode(!editMode)
  }

  const mutation = useMutation({
    mutationFn: (data: UserUpdateMe) =>
      UsersService.updateUserMe({ requestBody: data }),
    onSuccess: (updatedUser) => {
      queryClient.setQueryData(queryKeys.auth.currentUser, updatedUser)
      showSuccessToast(t("messages:success.userUpdated"))
      toggleEditMode()
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.auth.currentUser })
    },
  })

  const onSubmit = (data: FormData) => {
    const updateData: UserUpdateMe = {}

    // only include fields that have changed
    if (data.full_name !== currentUser?.full_name) {
      updateData.full_name = data.full_name
    }
    if (data.email !== currentUser?.email) {
      updateData.email = data.email
    }
    if (
      (data.avatar_url || undefined) !== (currentUser?.avatar_url || undefined)
    ) {
      updateData.avatar_url = data.avatar_url || null
    }

    mutation.mutate(updateData)
  }

  const onCancel = () => {
    form.reset({
      full_name: currentUser?.full_name ?? undefined,
      email: currentUser?.email,
      avatar_url: currentUser?.avatar_url ?? "",
    })
    toggleEditMode()
  }

  return (
    <div className="max-w-md">
      <h3 className="text-lg font-semibold py-4">
        {t("settings:profile.title")}
      </h3>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-4"
        >
          <div className="flex items-start gap-4 rounded-lg border p-4">
            <UserAvatar
              avatarUrl={previewAvatarUrl}
              className="size-16 shrink-0"
              email={currentUser?.email}
              fullName={previewName}
            />
            <div className="min-w-0">
              <p className="font-medium">{t("settings:profile.avatar")}</p>
              <p className="text-sm text-muted-foreground">
                {t("settings:profile.avatarHint")}
              </p>
            </div>
          </div>

          <FormField
            control={form.control}
            name="full_name"
            render={({ field }) =>
              editMode ? (
                <FormItem>
                  <FormLabel>{t("settings:profile.fullName")}</FormLabel>
                  <FormControl>
                    <Input type="text" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              ) : (
                <FormItem>
                  <FormLabel>{t("settings:profile.fullName")}</FormLabel>
                  <p
                    className={cn(
                      "py-2 truncate max-w-sm",
                      !field.value && "text-muted-foreground",
                    )}
                  >
                    {field.value || t("settings:profile.na")}
                  </p>
                </FormItem>
              )
            }
          />

          <FormField
            control={form.control}
            name="email"
            render={({ field }) =>
              editMode ? (
                <FormItem>
                  <FormLabel>{t("settings:profile.email")}</FormLabel>
                  <FormControl>
                    <Input type="email" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              ) : (
                <FormItem>
                  <FormLabel>{t("settings:profile.email")}</FormLabel>
                  <p className="py-2 truncate max-w-sm">{field.value}</p>
                </FormItem>
              )
            }
          />

          <FormField
            control={form.control}
            name="avatar_url"
            render={({ field }) =>
              editMode ? (
                <FormItem>
                  <FormLabel>{t("settings:profile.avatarUrl")}</FormLabel>
                  <FormControl>
                    <Input
                      type="url"
                      placeholder="https://example.com/avatar.png"
                      {...field}
                      value={field.value ?? ""}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              ) : (
                <FormItem>
                  <FormLabel>{t("settings:profile.avatarUrl")}</FormLabel>
                  <p
                    className={cn(
                      "py-2 truncate max-w-sm",
                      !field.value && "text-muted-foreground",
                    )}
                  >
                    {field.value || t("settings:profile.na")}
                  </p>
                </FormItem>
              )
            }
          />

          <div className="flex gap-3">
            {editMode ? (
              <>
                <LoadingButton
                  type="submit"
                  loading={mutation.isPending}
                  disabled={!form.formState.isDirty}
                >
                  {t("settings:profile.save")}
                </LoadingButton>
                <Button
                  type="button"
                  variant="outline"
                  onClick={onCancel}
                  disabled={mutation.isPending}
                >
                  {t("settings:profile.cancel")}
                </Button>
              </>
            ) : (
              <Button type="button" onClick={toggleEditMode}>
                {t("settings:profile.edit")}
              </Button>
            )}
          </div>
        </form>
      </Form>
    </div>
  )
}

export default UserInformation
