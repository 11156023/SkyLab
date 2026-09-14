import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import styles from "./ResourceDetailPage.module.scss";
import MIcon from "../../../../components/MIcon";
import { ResourcesService } from "../../../../services/resources";
import OverviewTab from "./OverviewTab";
import MonitoringTab from "./MonitoringTab";
import SpecificationsTab from "./SpecificationsTab";
import SnapshotsTab from "./SnapshotsTab";
import AuditLogsTab from "./AuditLogsTab";
import AdvancedSettingsTab from "./AdvancedSettingsTab";
import PageHeader from "../../../../components/PageHeader/PageHeader";

/* sharedOnly=false 的分頁只有擁有者／管理員看得到；被分享的使用者只能看總覽、監控與進階設定裡的唯讀卡片 */
const TABS = [
  { key: "overview",       labelKey: "ResourceDetailPage.tabOverview", icon: "info", sharedOnly: true },
  { key: "monitoring",     labelKey: "ResourceDetailPage.tabMonitoring", icon: "monitor_heart", sharedOnly: true },
  { key: "specifications", labelKey: "ResourceDetailPage.tabSpecifications", icon: "tune", sharedOnly: false },
  { key: "snapshots",      labelKey: "ResourceDetailPage.tabSnapshots", icon: "photo_camera", sharedOnly: false },
  { key: "auditLogs",      labelKey: "ResourceDetailPage.tabAuditLogs", icon: "receipt_long", sharedOnly: false },
  { key: "advanced",       labelKey: "ResourceDetailPage.tabAdvanced", icon: "settings", sharedOnly: true },
];

/**
 * 資源詳情頁。backTo 由路由決定（/my-resources 或 /resource-mgmt）。
 */
export default function ResourceDetailPage({ backTo = "/my-resources" }) {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();
  const params = useParams();
  const vmid = Number.parseInt(params.vmid, 10);
  const [tab, setTab] = useState("overview");
  const [access, setAccess] = useState(null); // { access_role, can_manage, owner_email }

  useEffect(() => {
    let cancelled = false;
    ResourcesService.get(vmid)
      .then((r) => !cancelled && setAccess({
        access_role: r?.access_role ?? "owner",
        can_manage: r?.can_manage !== false,
        owner_email: r?.owner_email ?? null,
      }))
      .catch(() => !cancelled && setAccess({ access_role: "owner", can_manage: true, owner_email: null }));
    return () => { cancelled = true; };
  }, [vmid]);

  const isShared = access?.access_role === "shared";
  const visibleTabs = TABS.filter((tabDef) => !isShared || tabDef.sharedOnly);

  return (
    <div className={styles.page}>
      <PageHeader
        leading={
          <button
            type="button"
            className={styles.backBtn}
            onClick={() => navigate(backTo)}
            title={t("ResourceDetailPage.backToList")}
          >
            <MIcon name="arrow_back" size={20} />
          </button>
        }
        title={<>{t("ResourceDetailPage.title")} <span className={styles.vmidText}>#{vmid}</span></>}
      />

      {isShared && (
        <p className={styles.rpHint}>
          <MIcon name="group" size={14} />
          {t("ResourceDetailPage.sharedNotice", { email: access?.owner_email ?? "—" })}
        </p>
      )}

      <div className={styles.tabs}>
        {visibleTabs.map((tabDef) => (
          <button
            key={tabDef.key}
            type="button"
            className={`${styles.tab} ${tab === tabDef.key ? styles.tabActive : ""}`}
            onClick={() => setTab(tabDef.key)}
          >
            <MIcon name={tabDef.icon} size={16} />
            {t(tabDef.labelKey)}
          </button>
        ))}
      </div>

      <div className={styles.content}>
        {tab === "overview"       && <OverviewTab vmid={vmid} />}
        {tab === "monitoring"     && <MonitoringTab vmid={vmid} />}
        {tab === "specifications" && <SpecificationsTab vmid={vmid} />}
        {tab === "snapshots"      && <SnapshotsTab vmid={vmid} />}
        {tab === "auditLogs"      && <AuditLogsTab vmid={vmid} />}
        {tab === "advanced"       && <AdvancedSettingsTab vmid={vmid} backTo={backTo} />}
      </div>
    </div>
  );
}
