import { useTranslation } from "react-i18next"

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

interface AdvancedSettingsTabProps {
  vmid: number
}

export default function AdvancedSettingsTab({
  vmid: _vmid,
}: AdvancedSettingsTabProps) {
  const { t } = useTranslation("resourceDetail")

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("advanced.title")}</CardTitle>
          <CardDescription>{t("advanced.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-center py-12">
            <p className="text-lg text-muted-foreground mb-2">
              {t("advanced.comingSoon")}
            </p>
            <p className="text-sm text-muted-foreground">
              {t("advanced.features")}
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
