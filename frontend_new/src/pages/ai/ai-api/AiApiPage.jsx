import { useState } from "react";
import styles from "./AiApiPage.module.scss";
import MIcon from "../../../components/MIcon";

const TABS = [
  { key: "apply",   label: "申請"    },
  { key: "keys",    label: "API Keys" },
  { key: "records", label: "申請紀錄" },
];

function EmptyState({ tab }) {
  const text = {
    apply:   "目前沒有進行中的申請",
    keys:    "目前沒有使用中的 API 金鑰",
    records: "目前沒有申請紀錄",
  };
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="key" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>尚無資料</h2>
      <p className={styles.emptyDesc}>{text[tab]}</p>
    </div>
  );
}

export default function AiApiPage() {
  const [activeTab, setActiveTab] = useState("records");

  return (
    <div className={styles.page}>
      {/* ── 頁首：麵包屑 + 標題 / Tabs ── */}
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <div className={styles.titleRow}>
            <h1 className={styles.pageTitle}>AI API 金鑰申請與管理</h1>
            <span className={styles.breadcrumb}>SkyLab AI API</span>
          </div>
          <p className={styles.pageSubtitle}>申請、管理與查詢 AI API 金鑰。</p>
        </div>

        <div className={styles.tabs}>
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`${styles.tab} ${activeTab === tab.key ? styles.tabActive : ""}`}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── 內容 ── */}
      <div className={styles.content}>
        <EmptyState tab={activeTab} />
      </div>
    </div>
  );
}
