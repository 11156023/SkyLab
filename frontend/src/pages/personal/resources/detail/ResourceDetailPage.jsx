import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import styles from "./ResourceDetailPage.module.scss";
import MIcon from "../../../../components/MIcon";
import {
  useBackupCapabilityQuery,
  useResourceDetailCacheUpdater,
  useResourceQuery,
  useSnapshotCapabilityQuery,
} from "../../../../queries/resourceDetail";
import { useResourceAuditLogsQuery } from "../../../../queries/auditLogs";
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

const DEMO_ACCESS = Object.freeze({ access_role: "owner", can_manage: true, owner_email: null });
/* 查不到身分（含網路錯誤）時照擁有者顯示分頁；各分頁的端點自己會擋權限 */
const FALLBACK_ACCESS = DEMO_ACCESS;
/* 操作紀錄分頁的筆數 badge：count 是後端獨立的總數查詢，limit 1 只為省流量 */
const AUDIT_COUNT_PARAMS = Object.freeze({ skip: 0, limit: 1 });

function toAccess(r) {
  return {
    access_role: r?.access_role ?? "owner",
    can_manage: r?.can_manage !== false,
    owner_email: r?.owner_email ?? null,
    machine_kind: r?.machine_kind ?? "personal",
    class_relation: r?.class_relation ?? null,
    owner_name: r?.owner_name ?? null,
    teaching_class_name: r?.teaching_class_name ?? null,
  };
}

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
  const detailCache = useResourceDetailCacheUpdater();

  /* 身分（擁有者／被分享／班級…）決定要顯示哪些分頁。快取以 vmid 為 key：同一頁直接換到另一台
     （例如 AI 助手導過來）時是另一份快取，不會拿上一台的權限決定這台的分頁。
     這裡只要身分，不輪詢；總覽分頁會用同一份快取每 30 秒刷新。
     失敗過的判斷看 errorUpdateCount：沒有資料時重抓會把 isError 暫時清掉，
     不能因為重抓中就把已經決定的分頁收起來又放回去 */
  const detailQuery = useResourceQuery(vmid, { enabled: !isGuideDemo, refetchInterval: false });
  let access = null; // { access_role, can_manage, owner_email, … }
  if (isGuideDemo) access = DEMO_ACCESS;
  else if (detailQuery.data !== undefined) access = toAccess(detailQuery.data);
  else if (detailQuery.errorUpdateCount > 0) access = FALLBACK_ACCESS;

  /* 快照可用性：後端即時問 PVE 這台機器「當下」能不能做快照（磁碟所在 storage 不支援時為否）。
     查不到（含被分享者無權限、重查失敗）一律當不可用：寧可少一個分頁，也不露出按了必失敗的功能 */
  const snapshotCapabilityQuery = useSnapshotCapabilityQuery(vmid, { enabled: !isGuideDemo });
  let snapshotCapable = null; // null＝還沒查到
  if (snapshotCapabilityQuery.isError) snapshotCapable = false;
  else if (snapshotCapabilityQuery.data !== undefined) snapshotCapable = snapshotCapabilityQuery.data?.available === true;
  else if (snapshotCapabilityQuery.errorUpdateCount > 0) snapshotCapable = false;
  /* 操作失敗後重查：快照與備份的可用性一起重抓 */
  const recheckSnapshotCapability = () => detailCache.invalidateCapabilities(vmid);
  /* 只有確定可用才顯示快照分頁；還沒查到的期間先不顯示 */
  const snapshotAvailable = isGuideDemo || snapshotCapable === true;

  /* 備份：快照「確定不能用」的機器才查。所屬叢集有設定備份 storage 時，以備份分頁
     取代快照分頁當還原點；重查快照可用性時也跟著重查 */
  const snapshotUnavailable = !isGuideDemo && snapshotCapable === false;
  const backupCapabilityQuery = useBackupCapabilityQuery(vmid, { enabled: snapshotUnavailable });
  const backupInfo = snapshotUnavailable && !backupCapabilityQuery.isError
    ? backupCapabilityQuery.data ?? null
    : null;
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

  /* 操作紀錄分頁的筆數 badge。被分享的使用者看不到這個分頁，等身分確認後才抓；
     快取以 vmid 為 key，換機器時不會掛著上一台的數字，這台查詢失敗就不顯示 */
  const auditCountQuery = useResourceAuditLogsQuery(vmid, AUDIT_COUNT_PARAMS, {
    enabled: !isGuideDemo && access !== null && !isShared,
    refetchInterval: false,
  });
  const auditCount = auditCountQuery.data?.count ?? null;

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

      {/* key 綁 vmid：換到另一台時各分頁整個重掛，上一台還在路上的回應（多數載入沒有取消機制）
          寫回的是已卸載的元件，不會蓋掉這台的資料；錯誤、表單等狀態也不會沿用到這台 */}
      <div key={vmid} className={styles.content} data-guide={`resource-detail-${activeTab}`}>
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
