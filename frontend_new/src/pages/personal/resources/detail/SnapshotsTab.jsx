import { useCallback, useEffect, useState } from "react";
import styles from "./ResourceDetailPage.module.scss";
import MIcon from "../../../../components/MIcon";
import { ResourcesService } from "../../../../services/resources";
import { useToast } from "../../../../hooks/useToast";

const INIT_SNAPSHOT_NAME = "skylab-init";

/** 輕量確認 dialog（比照 ResourcesPage 的 ConfirmModal 行為） */
function ConfirmModal({ title, desc, confirmLabel = "確定", danger = false, loading = false, onConfirm, onClose }) {
  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <span className={styles.modalTitle}>{title}</span>
        {desc && <p className={styles.modalDesc}>{desc}</p>}
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className={danger ? styles.btnDanger : styles.btnPrimary}
            disabled={loading}
            onClick={onConfirm}
          >
            {loading ? "處理中…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function SnapshotsTab({ vmid }) {
  const toast = useToast();
  const [snapshots, setSnapshots] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [resetConfirm, setResetConfirm] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [rollbackTarget, setRollbackTarget] = useState(null);
  const [snapname, setSnapname] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setSnapshots(await ResourcesService.listSnapshots(vmid));
    } catch (e) {
      toast.error(e?.message ?? "無法載入快照列表");
      setSnapshots((prev) => prev ?? []);
    }
  }, [vmid, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const hasInitSnapshot = (snapshots ?? []).some((s) => s.name === INIT_SNAPSHOT_NAME);

  const run = async (fn, successMsg, after) => {
    setBusy(true);
    try {
      await fn();
      toast.success(successMsg);
      after?.();
      await load();
    } catch (e) {
      toast.error(e?.message ?? "操作失敗");
    } finally {
      setBusy(false);
    }
  };

  const handleCreate = () => {
    if (!snapname.trim()) {
      toast.error("請輸入快照名稱");
      return;
    }
    run(
      () =>
        ResourcesService.createSnapshot(vmid, {
          snapname: snapname.trim(),
          description: description || undefined,
          vmstate: false,
        }),
      "快照建立中",
      () => {
        setCreateOpen(false);
        setSnapname("");
        setDescription("");
      },
    );
  };

  if (snapshots === null) return <p className={styles.stateText}>載入中…</p>;

  return (
    <div className={styles.tabStack}>
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>快照管理</h2>
            <p className={styles.cardDesc}>建立、還原與刪除此資源的快照</p>
          </div>
          <div className={styles.headerActions}>
            <button
              type="button"
              className={styles.btnSecondary}
              disabled={!hasInitSnapshot || busy}
              title={hasInitSnapshot ? undefined : "尚未建立初始快照，無法一鍵重置"}
              onClick={() => setResetConfirm(true)}
            >
              <MIcon name="restart_alt" size={14} />
              一鍵重置
            </button>
            {!hasInitSnapshot && (
              <button
                type="button"
                className={styles.btnSecondary}
                disabled={busy}
                onClick={() =>
                  run(() => ResourcesService.createInitSnapshot(vmid), "初始快照已建立")
                }
              >
                建立初始快照
              </button>
            )}
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={() => setCreateOpen(true)}
            >
              <MIcon name="add" size={14} />
              建立快照
            </button>
          </div>
        </div>

        {snapshots.length === 0 ? (
          <p className={styles.stateText}>尚無快照</p>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>名稱</th>
                <th className={styles.th}>描述</th>
                <th className={styles.th}>建立時間</th>
                <th className={`${styles.th} ${styles.thRight}`}>操作</th>
              </tr>
            </thead>
            <tbody>
              {snapshots.map((snap) => (
                <tr key={snap.name} className={styles.tr}>
                  <td className={styles.td}>
                    <span className={styles.snapName}>
                      {snap.name}
                      {snap.name === INIT_SNAPSHOT_NAME && (
                        <span className={`${styles.badge} ${styles.badge_info}`}>
                          <MIcon name="verified_user" size={12} />
                          受保護
                        </span>
                      )}
                    </span>
                  </td>
                  <td className={`${styles.td} ${styles.mutedCell}`}>
                    {snap.description || "—"}
                  </td>
                  <td className={`${styles.td} ${styles.mutedCell}`}>
                    {snap.snaptime
                      ? new Date(snap.snaptime * 1000).toLocaleString("zh-TW")
                      : "—"}
                  </td>
                  <td className={`${styles.td} ${styles.tdRight}`}>
                    <button
                      type="button"
                      className={styles.btnSecondary}
                      disabled={busy}
                      onClick={() => setRollbackTarget(snap.name)}
                    >
                      <MIcon name="history" size={14} />
                      還原
                    </button>
                    {snap.name !== INIT_SNAPSHOT_NAME && (
                      <button
                        type="button"
                        className={styles.btnDangerOutline}
                        disabled={busy}
                        onClick={() => setDeleteTarget(snap.name)}
                      >
                        <MIcon name="delete_outline" size={14} />
                        刪除
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {createOpen && (
        <div className={styles.modalOverlay} onClick={() => setCreateOpen(false)}>
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <span className={styles.modalTitle}>建立快照</span>
            <p className={styles.modalDesc}>快照會保留目前磁碟狀態，可隨時還原</p>
            <div className={styles.field}>
              <label htmlFor="snap-name">名稱 *</label>
              <input
                id="snap-name"
                type="text"
                placeholder="snap-2026-07-04"
                value={snapname}
                onChange={(e) => setSnapname(e.target.value)}
              />
            </div>
            <div className={styles.field}>
              <label htmlFor="snap-desc">描述</label>
              <textarea
                id="snap-desc"
                rows={3}
                placeholder="例如：升級套件前的備份"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={() => setCreateOpen(false)}
              >
                取消
              </button>
              <button
                type="button"
                className={styles.btnPrimary}
                disabled={busy}
                onClick={handleCreate}
              >
                {busy ? "建立中…" : "建立"}
              </button>
            </div>
          </div>
        </div>
      )}

      {resetConfirm && (
        <ConfirmModal
          title="重置到初始狀態？"
          desc="VM 會還原到初始快照並重新開機，之後的所有變更將會消失。"
          confirmLabel="重置"
          danger
          loading={busy}
          onConfirm={() =>
            run(() => ResourcesService.resetToInit(vmid), "重置任務已排入背景執行", () =>
              setResetConfirm(false),
            )
          }
          onClose={() => setResetConfirm(false)}
        />
      )}

      {rollbackTarget && (
        <ConfirmModal
          title={`還原到快照「${rollbackTarget}」？`}
          desc="還原後，快照之後的變更將會消失。"
          confirmLabel="還原"
          danger
          loading={busy}
          onConfirm={() =>
            run(
              () => ResourcesService.rollbackSnapshot(vmid, rollbackTarget),
              "還原已開始",
              () => setRollbackTarget(null),
            )
          }
          onClose={() => setRollbackTarget(null)}
        />
      )}

      {deleteTarget && (
        <ConfirmModal
          title={`刪除快照「${deleteTarget}」？`}
          desc="刪除後無法復原。"
          confirmLabel="刪除"
          danger
          loading={busy}
          onConfirm={() =>
            run(
              () => ResourcesService.deleteSnapshot(vmid, deleteTarget),
              "快照已刪除",
              () => setDeleteTarget(null),
            )
          }
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
