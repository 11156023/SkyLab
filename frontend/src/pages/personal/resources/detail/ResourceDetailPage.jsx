import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import styles from "./ResourceDetailPage.module.scss";
import MIcon from "../../../../components/MIcon";
import { ResourcesService } from "../../../../services/resources";
import { AuditLogsService } from "../../../../services/auditLogs";
import OverviewTab from "./OverviewTab";
import MonitoringTab from "./MonitoringTab";
import SpecificationsTab from "./SpecificationsTab";
import SnapshotsTab from "./SnapshotsTab";
import BackupsTab from "./BackupsTab";
import AuditLogsTab from "./AuditLogsTab";
import AdvancedSettingsTab from "./AdvancedSettingsTab";
import ResourceDetailGuideDemo from "./ResourceDetailGuideDemo";
import PageHeader from "../../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../../components/SegmentedControl/SegmentedControl";

/* sharedOnly=false 的分頁只有擁有者／管理員看得到；被分享的使用者只能看總覽、監控與進階設定裡的唯讀卡片 */
const TABS = [
  { key: "overview",       labelKey: "ResourceDetailPage.tabOverview", icon: "info", sharedOnly: true },
  { key: "monitoring",     labelKey: "ResourceDetailPage.tabMonitoring", icon: "monitor_heart", sharedOnly: true },
  { key: "specifications", labelKey: "ResourceDetailPage.tabSpecifications", icon: "tune", sharedOnly: false },
  { key: "snapshots",      labelKey: "ResourceDetailPage.tabSnapshots", icon: "photo_camera", sharedOnly: false },
  { key: "backups",        labelKey: "ResourceDetailPage.tabBackups", icon: "backup", sharedOnly: false },
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

  /* 快照可用性：後端即時問 PVE 這台機器「當下」能不能做快照（磁碟所在 storage 不支援時為否）。
     結果連同 vmid 一起存，換機器時不會沿用上一台的答案；checkSeq 遞增＝重新查一次 */
  const [snapshotCapability, setSnapshotCapability] = useState({ vmid: null, available: null });
  const [capabilityCheckSeq, setCapabilityCheckSeq] = useState(0);
  useEffect(() => {
    if (isGuideDemo) return undefined;
    let cancelled = false;
    ResourcesService.getSnapshotCapability(vmid)
      .then((r) => !cancelled && setSnapshotCapability({ vmid, available: r?.available === true }))
      /* 查不到（含被分享者無權限）一律當不可用：寧可少一個分頁，也不露出按了必失敗的功能 */
      .catch(() => !cancelled && setSnapshotCapability({ vmid, available: false }));
    return () => { cancelled = true; };
  }, [isGuideDemo, vmid, capabilityCheckSeq]);
  const recheckSnapshotCapability = useCallback(() => setCapabilityCheckSeq((n) => n + 1), []);
  /* 只有確定可用才顯示快照分頁；還沒查到（available 為 null）的期間先不顯示 */
  const snapshotAvailable = isGuideDemo
    || (snapshotCapability.vmid === vmid && snapshotCapability.available === true);

  /* 備份：快照「確定不能用」的機器才查。所屬叢集有設定備份 storage 時，以備份分頁
     取代快照分頁當還原點；每次重查快照可用性（snapshotCapability 換新物件）也跟著重查 */
  const snapshotUnavailable = !isGuideDemo
    && snapshotCapability.vmid === vmid && snapshotCapability.available === false;
  const [backupCapability, setBackupCapability] = useState({ vmid: null, data: null });
  useEffect(() => {
    if (!snapshotUnavailable) return undefined;
    let cancelled = false;
    ResourcesService.getBackupCapability(vmid)
      .then((r) => !cancelled && setBackupCapability({ vmid, data: r ?? null }))
      .catch(() => !cancelled && setBackupCapability({ vmid, data: null }));
    return () => { cancelled = true; };
  }, [snapshotUnavailable, vmid, snapshotCapability]);
  const backupInfo = snapshotUnavailable && backupCapability.vmid === vmid ? backupCapability.data : null;
  const backupAvailable = backupInfo?.available === true;

  const isShared = access?.access_role === "shared";
  const visibleTabs = TABS.filter((tabDef) => {
    if (isShared && !tabDef.sharedOnly) return false;
    /* 機器當下不能用快照時，整個快照分頁（建立／還原／刪除、一鍵重置、初始快照）都隱藏 */
    if (tabDef.key === "snapshots" && !snapshotAvailable) return false;
    if (tabDef.key === "backups" && !backupAvailable) return false;
    return true;
  });
  /* 目前分頁被藏起來時（例如停在快照分頁、重新查詢後變成不可用）退回總覽 */
  const activeTab = visibleTabs.some((tabDef) => tabDef.key === tab) ? tab : "overview";
  /* 進階設定的憑證卡「到總覽查看密碼」：切回總覽並捲到頂（正式頁與導覽示範頁共用） */
  const showOverview = () => {
    setTab("overview");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  /* 操作紀錄分頁的筆數 badge；count 是後端獨立的總數查詢，limit 1 只為省流量。
     被分享的使用者看不到這個分頁，等 access 回來確認身分後才抓 */
  const [auditCount, setAuditCount] = useState(null);
  useEffect(() => {
    if (isGuideDemo || !access || isShared) return undefined;
    let cancelled = false;
    AuditLogsService.listForResource(vmid, { skip: 0, limit: 1 })
      .then((res) => !cancelled && setAuditCount(res?.count ?? null))
      .catch(() => {});
    return () => { cancelled = true; };
  }, [isGuideDemo, access, isShared, vmid]);

  return (
    <div className={styles.page}>
      <PageHeader
        title={<>
          {t("ResourceDetailPage.title")} <span className={styles.vmidText}>#{isGuideDemo ? "DEMO" : vmid}</span>
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
            badge: tabDef.key === "auditLogs" ? auditCount ?? undefined : undefined,
            buttonProps: { "data-guide-tab": `resource-${tabDef.key}` },
          }))}
          value={activeTab}
          onChange={setTab}
          ariaLabel={t("ResourceDetailPage.tabsAriaLabel")}
        />
        <div className={styles.tabsToolbar} ref={setTabToolbar} />
      </div>

      <div className={styles.content} data-guide={`resource-detail-${activeTab}`}>
        {isGuideDemo ? <ResourceDetailGuideDemo tab={activeTab} toolbar={tabToolbar} onShowOverview={showOverview} /> : (
          <>
            {activeTab === "overview"       && <OverviewTab vmid={vmid} access={access} />}
            {activeTab === "monitoring"     && <MonitoringTab vmid={vmid} toolbar={tabToolbar} />}
            {activeTab === "specifications" && <SpecificationsTab vmid={vmid} />}
            {activeTab === "snapshots"      && (
              <SnapshotsTab vmid={vmid} toolbar={tabToolbar} onOperationFailed={recheckSnapshotCapability} />
            )}
            {activeTab === "backups"        && (
              <BackupsTab
                vmid={vmid}
                toolbar={tabToolbar}
                capability={backupInfo}
                onOperationFailed={recheckSnapshotCapability}
              />
            )}
            {activeTab === "auditLogs"      && <AuditLogsTab vmid={vmid} />}
            {activeTab === "advanced"       && (
              <AdvancedSettingsTab
                vmid={vmid}
                backTo={backTo}
                onShowOverview={showOverview}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
