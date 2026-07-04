import { useCallback, useEffect, useState } from "react";
import styles from "./MonitoringPage.module.scss";
import MIcon from "../../../components/MIcon";
import { MiningIncidentsService } from "../../../services/miningIncidents";
import { useToast } from "../../../hooks/useToast";

const STATUS_LABELS = {
  detected: "已偵測",
  suspended: "已暫停",
  banned: "已停權",
  dismissed: "已解除",
};

/** detected/suspended 視為待處理（紅），其餘中性 */
function statusBadgeClass(status) {
  return status === "detected" || status === "suspended" ? "badge_err" : "badge_muted";
}

export default function MiningIncidentsPanel() {
  const toast = useToast();
  const [incidents, setIncidents] = useState(null);
  const [banTarget, setBanTarget] = useState(null);
  const [dismissTarget, setDismissTarget] = useState(null);
  const [dismissExempt, setDismissExempt] = useState(false);
  const [dismissNote, setDismissNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setIncidents(await MiningIncidentsService.list());
    } catch {
      setIncidents((prev) => prev ?? []);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, [load]);

  const open = (incidents ?? []).filter(
    (i) => i.status === "detected" || i.status === "suspended",
  );
  const closed = (incidents ?? []).filter(
    (i) => i.status === "banned" || i.status === "dismissed",
  );

  const handleBan = async () => {
    setBusy(true);
    try {
      await MiningIncidentsService.ban(banTarget.id);
      toast.success("帳號已停權，VM 維持暫停狀態");
      setBanTarget(null);
      await load();
    } catch (e) {
      toast.error(`停權失敗：${e?.message ?? "未知錯誤"}`);
    } finally {
      setBusy(false);
    }
  };

  const closeDismiss = () => {
    setDismissTarget(null);
    setDismissExempt(false);
    setDismissNote("");
  };

  const handleDismiss = async () => {
    setBusy(true);
    try {
      const result = await MiningIncidentsService.dismiss(dismissTarget.id, {
        exempt: dismissExempt,
        note: dismissNote || null,
      });
      toast.success(result.status === "dismissed" ? "已解除事件並嘗試恢復 VM" : "已解除事件");
      closeDismiss();
      await load();
    } catch (e) {
      toast.error(`解除失敗：${e?.message ?? "未知錯誤"}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <div>
          <h2 className={styles.cardTitle}>
            <MIcon name="gavel" size={18} />
            挖礦事件
          </h2>
          <p className={styles.cardDesc}>
            CPU 長期滿載的疑似挖礦資源（已自動存證與暫停，待管理員審核）
          </p>
        </div>
        {open.length > 0 && <span className={styles.alertCount}>{open.length} 待處理</span>}
      </div>

      {incidents === null ? (
        <p className={styles.cardEmpty}>載入中…</p>
      ) : incidents.length === 0 ? (
        <div className={styles.cardEmpty}>
          <MIcon name="verified_user" size={24} />
          <p>目前沒有挖礦事件</p>
        </div>
      ) : (
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.th}>VMID</th>
              <th className={styles.th}>平均 CPU</th>
              <th className={styles.th}>觀察視窗</th>
              <th className={styles.th}>存證快照</th>
              <th className={styles.th}>狀態</th>
              <th className={styles.th}>偵測時間</th>
              <th className={`${styles.th} ${styles.thRight}`}>操作</th>
            </tr>
          </thead>
          <tbody>
            {[...open, ...closed].map((incident) => (
              <tr key={incident.id} className={styles.tr}>
                <td className={`${styles.td} ${styles.monoCell}`}>{incident.vmid}</td>
                <td className={`${styles.td} ${styles.monoCell}`}>
                  {incident.avg_cpu.toFixed(1)}%
                </td>
                <td className={`${styles.td} ${styles.mutedCell}`}>
                  {incident.window_hours} 小時
                </td>
                <td className={styles.td}>
                  {incident.snapshot_name ? (
                    <span className={`${styles.monoCell} ${styles.snapCell}`}>
                      <MIcon name="photo_camera" size={12} />
                      {incident.snapshot_name}
                    </span>
                  ) : (
                    <span className={styles.mutedCell}>存證失敗</span>
                  )}
                </td>
                <td className={styles.td}>
                  <span className={`${styles.badge} ${styles[statusBadgeClass(incident.status)]}`}>
                    {STATUS_LABELS[incident.status] ?? incident.status}
                  </span>
                </td>
                <td className={`${styles.td} ${styles.mutedCell}`}>
                  {new Date(incident.detected_at).toLocaleString("zh-TW")}
                </td>
                <td className={`${styles.td} ${styles.tdRight}`}>
                  {(incident.status === "detected" || incident.status === "suspended") && (
                    <>
                      <button
                        type="button"
                        className={styles.btnDangerOutline}
                        onClick={() => setBanTarget(incident)}
                      >
                        <MIcon name="block" size={14} />
                        停權
                      </button>
                      <button
                        type="button"
                        className={styles.btnSecondary}
                        onClick={() => setDismissTarget(incident)}
                      >
                        <MIcon name="undo" size={14} />
                        誤判解除
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* 停權確認 */}
      {banTarget && (
        <div className={styles.modalOverlay} onClick={() => setBanTarget(null)}>
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <span className={styles.modalTitle}>確認停權帳號？</span>
            <p className={styles.modalDesc}>
              VMID {banTarget.vmid} 的擁有者帳號將被停用（無法登入），VM
              維持暫停狀態以保留證據。此操作可由管理員在使用者管理中還原。
            </p>
            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={() => setBanTarget(null)}
              >
                取消
              </button>
              <button
                type="button"
                className={styles.btnDanger}
                disabled={busy}
                onClick={handleBan}
              >
                {busy ? "處理中…" : "確認停權"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 誤判解除 */}
      {dismissTarget && (
        <div className={styles.modalOverlay} onClick={closeDismiss}>
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <span className={styles.modalTitle}>解除挖礦事件</span>
            <p className={styles.modalDesc}>
              VMID {dismissTarget.vmid} 將標記為誤判並嘗試恢復運行。
            </p>
            <label className={styles.checkLine}>
              <input
                type="checkbox"
                checked={dismissExempt}
                onChange={(e) => setDismissExempt(e.target.checked)}
              />
              同時將此資源加入豁免（之後不再偵測）
            </label>
            <div className={styles.field}>
              <label htmlFor="mining-note">備註（選填）</label>
              <textarea
                id="mining-note"
                rows={3}
                placeholder="例如：教授的模型訓練工作負載"
                value={dismissNote}
                onChange={(e) => setDismissNote(e.target.value)}
              />
            </div>
            <div className={styles.modalActions}>
              <button type="button" className={styles.btnSecondary} onClick={closeDismiss}>
                取消
              </button>
              <button
                type="button"
                className={styles.btnPrimary}
                disabled={busy}
                onClick={handleDismiss}
              >
                {busy ? "處理中…" : "確認解除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
