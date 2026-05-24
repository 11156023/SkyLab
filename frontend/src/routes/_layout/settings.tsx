import { createFileRoute } from "@tanstack/react-router"
import { useTranslation } from "react-i18next"

import ChangePassword from "@/components/UserSettings/ChangePassword"
import DeleteAccount from "@/components/UserSettings/DeleteAccount"
import UserInformation from "@/components/UserSettings/UserInformation"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/settings")({
  component: UserSettings,
  head: () => ({
    meta: [
      {
        title: "Settings - SkyLab",
      },
    ],
  }),
})

function UserSettings() {
  const { user: currentUser } = useAuth()
  const { t } = useTranslation("settings")

  const tabsConfig = [
    {
      value: "my-profile",
      title: t("tabs.myProfile"),
      component: UserInformation,
    },
    { value: "password", title: t("tabs.password"), component: ChangePassword },
    {
      value: "danger-zone",
      title: t("tabs.dangerZone"),
      component: DeleteAccount,
    },
  ]

  const finalTabs = tabsConfig

  if (!currentUser) {
    return null
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t("page.title")}</h1>
        <p className="text-muted-foreground">{t("page.description")}</p>
      </div>

      <Tabs defaultValue="my-profile">
        <TabsList>
          {finalTabs.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {finalTabs.map((tab) => (
          <TabsContent key={tab.value} value={tab.value}>
            <tab.component />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}
