import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import styles from "./ResourceDetailPage.module.scss";
import MIcon from "../../../../components/MIcon";
import MachineKindBadge from "../../../../components/MachineKindBadge/MachineKindBadge";
import { ResourcesService } from "../../../../services/resources";
import OverviewTab from "./OverviewTab";
import MonitoringTab from "./MonitoringTab";
import SpecificationsTab from "./SpecificationsTab";
import SnapshotsTab from "./SnapshotsTab";
import AuditLogsTab from "./AuditLogsTab";
import AdvancedSettingsTab from "./AdvancedSettingsTab";
import PageHeader from "../../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../../components/SegmentedControl/SegmentedControl";

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
  const isGuideDemo = params.vmid === "demo";
  const vmid = isGuideDemo ? 100 : Number.parseInt(params.vmid, 10);
  const [tab, setTab] = useState("overview");
  /* 分頁列右側的工具槽：分頁元件把自己的控制項（時間範圍、快照按鈕）portal 進來，
     與分頁切換器同列；用 state 存節點，掛載完成後子元件才拿得到 portal 目標 */
  const [tabToolbar, setTabToolbar] = useState(null);
  const [access, setAccess] = useState(null); // { access_role, can_manage, owner_email }

  useEffect(() => {
    if (isGuideDemo) {
      setAccess({ access_role: "owner", can_manage: true, owner_email: null });
      return undefined;
    }
    let cancelled = false;
    ResourcesService.get(vmid)
      .then((r) => !cancelled && setAccess({
        access_role: r?.access_role ?? "owner",
        can_manage: r?.can_manage !== false,
        owner_email: r?.owner_email ?? null,
        machine_kind: r?.machine_kind ?? "personal",
        class_relation: r?.class_relation ?? null,
        owner_name: r?.owner_name ?? null,
        teaching_class_name: r?.teaching_class_name ?? null,
      }))
      .catch(() => !cancelled && setAccess({ access_role: "owner", can_manage: true, owner_email: null }));
    return () => { cancelled = true; };
  }, [isGuideDemo, vmid]);

  const isShared = access?.access_role === "shared";
  const visibleTabs = TABS.filter((tabDef) => !isShared || tabDef.sharedOnly);

  return (
    <div className={styles.page}>
      <PageHeader
        title={<>
          {t("ResourceDetailPage.title")} <span className={styles.vmidText}>#{isGuideDemo ? "DEMO" : vmid}</span>
          {access && (
            <MachineKindBadge
              kind={access.machine_kind}
              classRelation={access.class_relation}
              ownerName={access.owner_name ?? access.owner_email}
              teachingClassName={access.teaching_class_name}
              className={styles.titleKind}
            />
          )}
        </>}
      >
        <button
          type="button"
          className={`${styles.btnSecondary} ${styles.backBtn}`}
          onClick={() => navigate(backTo)}
        >
          <MIcon name="arrow_back" size={18} />
          {t("ResourceDetailPage.backToList")}
        </button>
      </PageHeader>

      {isGuideDemo && (
        <div className={styles.demoNotice}>
          <MIcon name="visibility" size={17} />
          <span><strong>{t("ResourceDetailPage.guideDemoTitle")}</strong>{t("ResourceDetailPage.guideDemoDesc")}</span>
        </div>
      )}

      {isShared && (
        <p className={styles.rpHint}>
          <MIcon name="group" size={14} />
          {t("ResourceDetailPage.sharedNotice", { email: access?.owner_email ?? "—" })}
        </p>
      )}

      <div className={styles.tabsRow} data-guide="resource-detail-tabs">
        <SegmentedControl
          className={styles.tabs}
          options={visibleTabs.map((tabDef) => ({
            value: tabDef.key,
            label: t(tabDef.labelKey),
            icon: tabDef.icon,
            buttonProps: { "data-guide-tab": `resource-${tabDef.key}` },
          }))}
          value={tab}
          onChange={setTab}
          ariaLabel={t("ResourceDetailPage.tabsAriaLabel")}
        />
        <div className={styles.tabsToolbar} ref={setTabToolbar} />
      </div>

      <div className={styles.content} data-guide={`resource-detail-${tab}`}>
        {isGuideDemo ? <ResourceDetailGuideDemo tab={tab} /> : (
          <>
            {tab === "overview"       && <OverviewTab vmid={vmid} />}
            {tab === "monitoring"     && <MonitoringTab vmid={vmid} toolbar={tabToolbar} />}
            {tab === "specifications" && <SpecificationsTab vmid={vmid} />}
            {tab === "snapshots"      && <SnapshotsTab vmid={vmid} toolbar={tabToolbar} />}
            {tab === "auditLogs"      && <AuditLogsTab vmid={vmid} />}
            {tab === "advanced"       && <AdvancedSettingsTab vmid={vmid} backTo={backTo} />}
          </>
        )}
      </div>
    </div>
  );
}

function DemoCard({ guide, icon, title, children }) {
  return (
    <section className={styles.card} data-guide={guide}>
      <div className={styles.cardHeader}>
        <h2 className={styles.cardTitle}><MIcon name={icon} size={18} />{title}</h2>
      </div>
      <div className={styles.cardBody}>{children}</div>
    </section>
  );
}

function ResourceDetailGuideDemo({ tab }) {
  const { t } = useTranslation("personal");

  if (tab === "overview") return (
    <div className={styles.tabStack}>
      <DemoCard icon="dns" title="demo-web-01"><div className={styles.demoFacts}><span><small>Status</small><strong className={styles.demoSuccess}>Running</strong></span><span><small>IP</small><strong>10.20.0.24</strong></span><span><small>CPU</small><strong>2 cores</strong></span><span><small>RAM</small><strong>4 GB</strong></span></div></DemoCard>
      <DemoCard icon="terminal" title={t("ResourceDetailPage.guideDemoAccessTitle")}><p className={styles.demoText}>SSH · ssh student@10.20.0.24</p></DemoCard>
    </div>
  );

  if (tab === "monitoring") return (
    <DemoCard icon="monitor_heart" title={t("ResourceDetailPage.tabMonitoring")}><div className={styles.demoMeters}>{[["CPU", "36%"], ["RAM", "58%"], ["Disk", "42%"]].map(([label, value]) => <div key={label}><span>{label}<strong>{value}</strong></span><i><b style={{ width: value }} /></i></div>)}</div></DemoCard>
  );

  if (tab === "specifications") return (
    <DemoCard icon="tune" title={t("ResourceDetailPage.tabSpecifications")}><div className={styles.demoSpecs}>{[["CPU", "2 cores", 30], ["RAM", "4 GB", 45], ["Disk", "40 GB", 62]].map(([label, value, rangeValue]) => <label key={label}><span>{label}<strong>{value}</strong></span><input type="range" value={rangeValue} readOnly /></label>)}</div></DemoCard>
  );

  if (tab === "snapshots") return (
    <DemoCard icon="photo_camera" title={t("ResourceDetailPage.tabSnapshots")}><div className={styles.demoSnapshot}><MIcon name="history" size={18} /><span><strong>before-upgrade</strong><small>2026/09/10 14:30 · Ready to restore</small></span><button type="button">Restore</button></div></DemoCard>
  );

  if (tab === "auditLogs") return (
    <DemoCard icon="receipt_long" title={t("ResourceDetailPage.tabAuditLogs")}><div className={styles.demoAudit}><span>14:32</span><strong>VM started</strong><small>student@example.edu</small><span>13:58</span><strong>Firewall rule updated</strong><small>student@example.edu</small></div></DemoCard>
  );

  return (
    <div className={styles.tabStack}>
      <DemoCard guide="resource-setting-lifecycle" icon="event" title={t("ResourceDetailPage.guideDemoLifecycle")}><p className={styles.demoText}>{t("ResourceDetailPage.guideDemoLifecycleDesc")}</p></DemoCard>
      <DemoCard guide="resource-setting-firewall" icon="security" title={t("ResourceDetailPage.guideDemoFirewall")}><p className={styles.demoText}>TCP 22 · TCP 80/443</p></DemoCard>
      <DemoCard guide="resource-setting-boot" icon="power_settings_new" title={t("ResourceDetailPage.guideDemoBoot")}><p className={styles.demoText}>Disk → Network → ISO</p></DemoCard>
      <DemoCard guide="resource-setting-credentials" icon="key" title={t("ResourceDetailPage.guideDemoCredentials")}><p className={styles.demoText}>{t("ResourceDetailPage.guideDemoCredentialsDesc")}</p></DemoCard>
      <DemoCard guide="resource-setting-sharing" icon="group" title={t("ResourceDetailPage.guideDemoSharing")}><p className={styles.demoText}>{t("ResourceDetailPage.guideDemoSharingDesc")}</p></DemoCard>
    </div>
  );
}
