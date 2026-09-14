import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./ResourceDetailPage.module.scss";
import MIcon from "../../../../components/MIcon";
import LoadingState from "../../../../components/LoadingState/LoadingState";
import EmptyState from "../../../../components/EmptyState/EmptyState";
import { ResourcesService } from "../../../../services/resources";
import { useToast } from "../../../../hooks/useToast";
import useDialogPresence from "../../../../hooks/useDialogPresence";
import { focusInvalidField } from "../../../../utils/focusField";
import { useConfirm } from "../../../../components/ConfirmDialog/ConfirmProvider";
import { formatDateTime } from "../../../../utils/formatDate";

const INIT_SNAPSHOT_NAME = "skylab-init";

export default function SnapshotsTab({ vmid }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const confirm = useConfirm();
  const [snapshots, setSnapshots] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [snapname, setSnapname] = useState("");
  const [nameInvalid, setNameInvalid] = useState(false);
  const snapnameRef = useRef(null);
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const createDialog    = useDialogPresence(createOpen);

  const load = useCallback(async () => {
    try {
      setSnapshots(await ResourcesService.listSnapshots(vmid));
    } catch (e) {
      toast.error(e?.message ?? t("SnapshotsTab.loadFailed"));
      setSnapshots((prev) => prev ?? []);
    }
  }, [vmid, toast, t]);

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
      toast.error(e?.message ?? t("SnapshotsTab.operationFailed"));
    } finally {
      setBusy(false);
    }
  };

  async function handleReset() {
    const ok = await confirm({
      title: t("SnapshotsTab.resetConfirmTitle"),
      message: t("SnapshotsTab.resetConfirmDesc"),
      confirmText: t("SnapshotsTab.reset"),
      danger: true,
    });
    if (ok) run(() => ResourcesService.resetToInit(vmid), t("SnapshotsTab.resetTaskQueued"));
  }

  async function handleRollback(name) {
    const ok = await confirm({
      title: t("SnapshotsTab.rollbackConfirmTitle", { name }),
      message: t("SnapshotsTab.rollbackConfirmDesc"),
      confirmText: t("SnapshotsTab.restore"),
      danger: true,
    });
    if (ok) run(() => ResourcesService.rollbackSnapshot(vmid, name), t("SnapshotsTab.rollbackStarted"));
  }

  async function handleDeleteSnap(name) {
    const ok = await confirm({
      title: t("SnapshotsTab.deleteConfirmTitle", { name }),
      message: t("SnapshotsTab.deleteConfirmDesc"),
      confirmText: t("SnapshotsTab.delete"),
      danger: true,
    });
    if (ok) run(() => ResourcesService.deleteSnapshot(vmid, name), t("SnapshotsTab.snapshotDeleted"));
  }

  const handleCreate = () => {
    if (!snapname.trim()) {
      setNameInvalid(true);
      focusInvalidField(snapnameRef.current);
      return;
    }
    run(
      () =>
        ResourcesService.createSnapshot(vmid, {
          snapname: snapname.trim(),
          description: description || undefined,
          vmstate: false,
        }),
      t("SnapshotsTab.snapshotCreating"),
      () => {
        setCreateOpen(false);
        setSnapname("");
        setNameInvalid(false);
        setDescription("");
      },
    );
  };

  if (snapshots === null) return <LoadingState />;

  return (
    <div className={styles.tabStack}>
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>{t("SnapshotsTab.title")}</h2>
            <p className={styles.cardDesc}>{t("SnapshotsTab.desc")}</p>
          </div>
          <div className={styles.headerActions}>
            <button
              type="button"
              className={styles.btnSecondary}
              disabled={!hasInitSnapshot || busy}
              title={hasInitSnapshot ? undefined : t("SnapshotsTab.noInitSnapshotHint")}
              onClick={handleReset}
            >
              <MIcon name="restart_alt" size={14} />
              {t("SnapshotsTab.oneClickReset")}
            </button>
            {!hasInitSnapshot && (
              <button
                type="button"
                className={styles.btnSecondary}
                disabled={busy}
                onClick={() =>
                  run(() => ResourcesService.createInitSnapshot(vmid), t("SnapshotsTab.initSnapshotCreated"))
                }
              >
                {t("SnapshotsTab.createInitSnapshot")}
              </button>
            )}
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={() => { setNameInvalid(false); setCreateOpen(true); }}
            >
              <MIcon name="add" size={14} />
              {t("SnapshotsTab.createSnapshot")}
            </button>
          </div>
        </div>

        {snapshots.length === 0 ? (
          <EmptyState icon="photo_camera" title={t("SnapshotsTab.emptyTitle")} />
        ) : (
          <div className={styles.tableScroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>{t("SnapshotsTab.colName")}</th>
                <th className={styles.th}>{t("SnapshotsTab.colDesc")}</th>
                <th className={styles.th}>{t("SnapshotsTab.colCreatedAt")}</th>
                <th className={`${styles.th} ${styles.thRight}`}>{t("SnapshotsTab.colActions")}</th>
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
                          {t("SnapshotsTab.protected")}
                        </span>
                      )}
                    </span>
                  </td>
                  <td className={`${styles.td} ${styles.mutedCell}`}>
                    {snap.description || "—"}
                  </td>
                  <td className={`${styles.td} ${styles.mutedCell}`}>
                    {snap.snaptime ? formatDateTime(snap.snaptime * 1000) : "—"}
                  </td>
                  <td className={`${styles.td} ${styles.tdRight}`}>
                    <button
                      type="button"
                      className={styles.btnSecondary}
                      disabled={busy}
                      onClick={() => handleRollback(snap.name)}
                    >
                      <MIcon name="history" size={14} />
                      {t("SnapshotsTab.restore")}
                    </button>
                    {snap.name !== INIT_SNAPSHOT_NAME && (
                      <button
                        type="button"
                        className={styles.btnDangerOutline}
                        disabled={busy}
                        onClick={() => handleDeleteSnap(snap.name)}
                      >
                        <MIcon name="delete_outline" size={14} />
                        {t("SnapshotsTab.delete")}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {createDialog.open && (
        <div
          className={`${styles.modalOverlay} ${createDialog.closing ? styles.modalOverlayOut : ""}`}
          onClick={() => setCreateOpen(false)}
        >
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <span className={styles.modalTitle}>{t("SnapshotsTab.createSnapshotTitle")}</span>
            <p className={styles.modalDesc}>{t("SnapshotsTab.createSnapshotDesc")}</p>
            <div className={`${styles.field} ${nameInvalid ? styles.fieldInvalid : ""}`}>
              <label htmlFor="snap-name">{t("SnapshotsTab.nameLabel")}</label>
              <input
                id="snap-name"
                ref={snapnameRef}
                type="text"
                placeholder="snap-2026-07-04"
                aria-invalid={nameInvalid}
                value={snapname}
                onChange={(e) => { setSnapname(e.target.value); setNameInvalid(false); }}
              />
            </div>
            <div className={styles.field}>
              <label htmlFor="snap-desc">{t("SnapshotsTab.descLabel")}</label>
              <textarea
                id="snap-desc"
                rows={3}
                placeholder={t("SnapshotsTab.descPlaceholder")}
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
                {t("SnapshotsTab.cancel")}
              </button>
              <button
                type="button"
                className={styles.btnPrimary}
                disabled={busy}
                onClick={handleCreate}
              >
                {busy ? t("SnapshotsTab.creating") : t("SnapshotsTab.create")}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
