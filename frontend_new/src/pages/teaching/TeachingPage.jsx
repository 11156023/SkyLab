import { useEffect, useState } from "react";
import styles from "./TeachingPage.module.scss";
import MIcon from "../../components/MIcon";
import { GroupsService } from "../../services/groups";
import { useToast } from "../../hooks/useToast";
import HeatmapPanel from "./HeatmapPanel";
import ConfigPushPanel from "./ConfigPushPanel";
import BatchSpecPanel from "./BatchSpecPanel";

const TABS = [
  { key: "heatmap", label: "學生進度熱圖", icon: "grid_view" },
  { key: "push",    label: "配置文件分發", icon: "upload_file" },
  { key: "spec",    label: "批次調整規格", icon: "tune" },
];

export default function TeachingPage() {
  const toast = useToast();
  const [groups, setGroups] = useState([]);
  const [groupId, setGroupId] = useState("");
  const [tab, setTab] = useState("heatmap");

  useEffect(() => {
    let cancelled = false;
    GroupsService.list()
      .then((res) => !cancelled && setGroups(res?.data ?? []))
      .catch((err) => toast.error(err?.message ?? "載入群組失敗"));
    return () => {
      cancelled = true;
    };
  }, [toast]);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>教學面板</h1>
          <p className={styles.pageSubtitle}>群組學生 VM 的進度熱圖、配置分發與批次規格調整</p>
        </div>
        <select
          className={styles.groupSelect}
          value={groupId}
          onChange={(e) => setGroupId(e.target.value)}
        >
          <option value="">選擇群組</option>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
      </div>

      {groupId ? (
        <>
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
            {tab === "heatmap" && <HeatmapPanel groupId={groupId} />}
            {tab === "push"    && <ConfigPushPanel groupId={groupId} />}
            {tab === "spec"    && <BatchSpecPanel groupId={groupId} />}
          </div>
        </>
      ) : (
        <div className={styles.emptyHint}>
          <MIcon name="groups" size={32} />
          <p>請先選擇一個群組</p>
        </div>
      )}
    </div>
  );
}
