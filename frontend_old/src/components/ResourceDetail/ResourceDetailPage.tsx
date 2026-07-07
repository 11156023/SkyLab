import { useNavigate } from "@tanstack/react-router"
import { ArrowLeft, Users } from "lucide-react"
import { Suspense, useState } from "react"
import { useTranslation } from "react-i18next"

import { ClassroomWatchDialog } from "@/components/Classroom/ClassroomWatchDialog"
import PairInviteDialog from "@/components/Teaching/PairInviteDialog"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

import AdvancedSettingsTab from "./AdvancedSettingsTab"
import AuditLogsTab from "./AuditLogsTab"
import MonitoringTab from "./MonitoringTab"
import OverviewTab from "./OverviewTab"
import SnapshotsTab from "./SnapshotsTab"
import SpecificationsTab from "./SpecificationsTab"

interface ResourceDetailPageProps {
  vmid: number
  backTo?: "/resources" | "/my-resources"
}

export default function ResourceDetailPage({
  vmid,
  backTo = "/resources",
}: ResourceDetailPageProps) {
  const { t } = useTranslation("resourceDetail")
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState("overview")
  const [inviteOpen, setInviteOpen] = useState(false)
  const [pairSessionId, setPairSessionId] = useState<string | null>(null)

  const handleBack = () => {
    navigate({ to: backTo })
  }

  return (
    <div className="container mx-auto py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Button
          variant="ghost"
          size="icon"
          onClick={handleBack}
          className="hover:bg-muted"
        >
          <ArrowLeft className="h-5 w-5" />
        </Button>
        <div className="flex-1">
          <h1 className="text-3xl font-bold tracking-tight">
            {t("title")} <span className="text-primary">#{vmid}</span>
          </h1>
        </div>
        <Button variant="outline" onClick={() => setInviteOpen(true)}>
          <Users className="h-4 w-4 mr-1" />
          邀請協作
        </Button>
      </div>

      <PairInviteDialog
        vmid={vmid}
        open={inviteOpen}
        onOpenChange={setInviteOpen}
        onCreated={(sessionId) => setPairSessionId(sessionId)}
      />
      <ClassroomWatchDialog
        sessionId={pairSessionId}
        title={`協作 VM ${vmid}`}
        pair
        open={pairSessionId !== null}
        onOpenChange={(open) => {
          if (!open) setPairSessionId(null)
        }}
      />

      {/* Tabs */}
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="space-y-6"
      >
        <TabsList className="grid w-full grid-cols-6 h-auto p-1">
          <TabsTrigger
            value="overview"
            className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            {t("tabs.overview")}
          </TabsTrigger>
          <TabsTrigger
            value="monitoring"
            className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            {t("tabs.monitoring")}
          </TabsTrigger>
          <TabsTrigger
            value="specifications"
            className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            {t("tabs.specifications")}
          </TabsTrigger>
          <TabsTrigger
            value="snapshots"
            className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            {t("tabs.snapshots")}
          </TabsTrigger>
          <TabsTrigger
            value="auditLogs"
            className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            {t("tabs.auditLogs")}
          </TabsTrigger>
          <TabsTrigger
            value="advanced"
            className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            {t("tabs.advanced")}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <Suspense
            fallback={
              <div className="flex items-center justify-center py-12 text-muted-foreground">
                Loading...
              </div>
            }
          >
            <OverviewTab vmid={vmid} />
          </Suspense>
        </TabsContent>

        <TabsContent value="monitoring">
          <Suspense
            fallback={
              <div className="flex items-center justify-center py-12 text-muted-foreground">
                Loading...
              </div>
            }
          >
            <MonitoringTab vmid={vmid} />
          </Suspense>
        </TabsContent>

        <TabsContent value="specifications">
          <Suspense
            fallback={
              <div className="flex items-center justify-center py-12 text-muted-foreground">
                Loading...
              </div>
            }
          >
            <SpecificationsTab vmid={vmid} />
          </Suspense>
        </TabsContent>

        <TabsContent value="snapshots">
          <Suspense
            fallback={
              <div className="flex items-center justify-center py-12 text-muted-foreground">
                Loading...
              </div>
            }
          >
            <SnapshotsTab vmid={vmid} />
          </Suspense>
        </TabsContent>

        <TabsContent value="auditLogs">
          <Suspense
            fallback={
              <div className="flex items-center justify-center py-12 text-muted-foreground">
                Loading...
              </div>
            }
          >
            <AuditLogsTab vmid={vmid} />
          </Suspense>
        </TabsContent>

        <TabsContent value="advanced">
          <Suspense
            fallback={
              <div className="flex items-center justify-center py-12 text-muted-foreground">
                Loading...
              </div>
            }
          >
            <AdvancedSettingsTab vmid={vmid} />
          </Suspense>
        </TabsContent>
      </Tabs>
    </div>
  )
}
