import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import styles from "./ResourceDetailPage.module.scss";
import MIcon from "../../../../components/MIcon";
import PairInviteDialog from "../../../../components/Teaching/PairInviteDialog";
import ClassroomWatchDialog from "../../../../components/Classroom/ClassroomWatchDialog";
import OverviewTab from "./OverviewTab";
import MonitoringTab from "./MonitoringTab";
import SpecificationsTab from "./SpecificationsTab";
import SnapshotsTab from "./SnapshotsTab";
import AuditLogsTab from "./AuditLogsTab";
import AdvancedSettingsTab from "./AdvancedSettingsTab";

const TABS = [
  { key: "overview",       label: "總覽",     icon: "info" },
  { key: "monitoring",     label: "監控",     icon: "monitoring" },
  { key: "specifications", label: "規格",     icon: "tune" },
  { key: "snapshots",      label: "快照",     icon: "photo_camera" },
  { key: "auditLogs",      label: "操作紀錄", icon: "receipt_long" },
  { key: "advanced",       label: "進階設定", icon: "settings" },
];

/**
 * 資源詳情頁。backTo 由路由決定（/my-resources 或 /resource-mgmt）。
 */
export default function ResourceDetailPage({ backTo = "/my-resources" }) {
  const navigate = useNavigate();
  const params = useParams();
  const vmid = Number.parseInt(params.vmid, 10);
  const [tab, setTab] = useState("overview");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [pairSessionId, setPairSessionId] = useState(null);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <button
            type="button"
            className={styles.backBtn}
            onClick={() => navigate(backTo)}
            title="返回列表"
          >
            <MIcon name="arrow_back" size={20} />
          </button>
          <h1 className={styles.pageTitle}>
            資源詳情 <span className={styles.vmidText}>#{vmid}</span>
          </h1>
        </div>
        <button
          type="button"
          className={styles.inviteBtn}
          onClick={() => setInviteOpen(true)}
        >
          <MIcon name="group_add" size={16} />
          邀請協作
        </button>
      </div>

      {inviteOpen && (
        <PairInviteDialog
          vmid={vmid}
          onClose={() => setInviteOpen(false)}
          onCreated={(sessionId) => setPairSessionId(sessionId)}
        />
      )}
      {pairSessionId && (
        <ClassroomWatchDialog
          sessionId={pairSessionId}
          title={`協作 VM ${vmid}`}
          pair
          onClose={() => setPairSessionId(null)}
        />
      )}

      <div className={styles.tabs}>
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={`${styles.tab} ${tab === t.key ? styles.tabActive : ""}`}
            onClick={() => setTab(t.key)}
          >
            <MIcon name={t.icon} size={16} />
            {t.label}
          </button>
        ))}
      </div>

      <div className={styles.content}>
        {tab === "overview"       && <OverviewTab vmid={vmid} />}
        {tab === "monitoring"     && <MonitoringTab vmid={vmid} />}
        {tab === "specifications" && <SpecificationsTab vmid={vmid} />}
        {tab === "snapshots"      && <SnapshotsTab vmid={vmid} />}
        {tab === "auditLogs"      && <AuditLogsTab vmid={vmid} />}
        {tab === "advanced"       && <AdvancedSettingsTab vmid={vmid} />}
      </div>
    </div>
  );
}
