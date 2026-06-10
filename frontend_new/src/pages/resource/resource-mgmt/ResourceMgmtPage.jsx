import { useState } from "react";
import styles from "./ResourceMgmtPage.module.scss";
import MIcon from "../../../components/MIcon";

const MOCK_RESOURCES = [];

const COLUMNS = ["名稱", "虛擬機類型", "狀態", "流量", "網段位置", "備注", "動作"];

function EmptyState() {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="dns" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>尚無虛擬機或容器</h2>
      <p className={styles.emptyDesc}>
        點擊右上角按鈕建立第一台虛擬機或容器
      </p>
    </div>
  );
}

export default function ResourceMgmtPage({ onNavigate }) {
  const [resources] = useState(MOCK_RESOURCES);

  return (
    <div className={styles.page}>
      {/* ── 頁首 ── */}
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>虛擬機與容器</h1>
          <p className={styles.pageSubtitle}>查看所有已在線虛擬機與 LXC 容器</p>
        </div>
        <div className={styles.pageActions}>
          <button type="button" className={styles.btnSecondary}>
            <MIcon name="sync" size={16} />
            重新整理
          </button>
          <button type="button" className={styles.btnPrimary} onClick={() => onNavigate?.("my-requests", { view: "create" })}>
            <MIcon name="add" size={16} />
            建立資源
          </button>
        </div>
      </div>

      {/* ── 內容 ── */}
      <div className={styles.content}>
        {resources.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {COLUMNS.map((col) => (
                    <th key={col} className={styles.th}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {resources.map((r) => (
                  <tr key={r.id} className={styles.tr}>
                    <td className={styles.td}>
                      <div className={styles.nameCell}>
                        <div className={styles.nameIcon}>
                          <MIcon name={r.icon ?? "computer"} size={18} />
                        </div>
                        <div>
                          <div className={styles.namePrimary}>{r.name}</div>
                          <div className={styles.nameSub}>{r.desc}</div>
                        </div>
                      </div>
                    </td>
                    <td className={styles.td}>{r.type}</td>
                    <td className={styles.td}>
                      <span className={`${styles.badge} ${styles[`badge_${r.status}`]}`}>
                        {r.status === "running" ? "運行中" : r.status === "stopped" ? "已停止" : r.status}
                      </span>
                    </td>
                    <td className={styles.td}>{r.traffic ?? "—"}</td>
                    <td className={styles.td}>{r.network ?? "—"}</td>
                    <td className={styles.td}>{r.note ?? "—"}</td>
                    <td className={styles.td}>
                      <div className={styles.actions}>
                        <button type="button" className={styles.actionBtn} title="設定">
                          <MIcon name="settings" size={16} />
                        </button>
                        <button type="button" className={`${styles.actionBtn} ${styles.actionBtnDanger}`} title="刪除">
                          <MIcon name="delete" size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
