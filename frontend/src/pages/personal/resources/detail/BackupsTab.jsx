import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import styles from "./ResourceDetailPage.module.scss";
import MIcon from "../../../../components/MIcon";
import Modal from "../../../../components/Modal/Modal";
import LoadingState from "../../../../components/LoadingState/LoadingState";
import EmptyState from "../../../../components/EmptyState/EmptyState";
import { ResourcesService } from "../../../../services/resources";
import { useToast } from "../../../../hooks/useToast";
import useDialogPresence from "../../../../hooks/useDialogPresence";
import { useConfirm } from "../../../../components/ConfirmDialog/ConfirmProvider";
import { useJobs } from "../../../../components/Jobs/JobsProvider";
import { formatDateTime } from "../../../../utils/formatDate";
import { findActiveBackupJob, formatBackupSize } from "./backupFormat";

/* 入列後到任務出現在「執行中任務」之間的空窗：超過這段時間還沒看到任務，就當它已經跑完 */
const QUEUED_FALLBACK_MS = 20000;
const DESCRIPTION_MAX_LENGTH = 120;

/**
 * 備份分頁：快照不能用的機器以備份當還原點（由 ResourceDetailPage 依後端的
 * snapshot-capability／backup-capability 決定要不要掛載）。
 *
 * 備份與還原都是幾分鐘起跳的背景任務：送出後只會拿到任務 id，進度在「背景任務」看；
 * 這裡靠全站的執行中任務清單（useJobs）知道這台機器的任務何時結束，再重抓備份清單。
 * capability＝後端回的 { requires_shutdown, max_count }。
 */
export default function BackupsTab({ vmid, toolbar, capability, onOperationFailed }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const confirm = useConfirm();
  const jobs = useJobs()?.items;
  const [backups, setBackups] = useState(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState(false);
  const createDialog = useDialogPresence(createOpen);
  /* 用 ref 持有 callback：不讓它的 identity 進 load 的依賴而觸發重抓 */
  const onOperationFailedRef = useRef(onOperationFailed);
  useEffect(() => {
    onOperationFailedRef.current = onOperationFailed;
  }, [onOperationFailed]);

  const load = useCallback(async () => {
    try {
      setBackups(await ResourcesService.listBackups(vmid));
    } catch (e) {
      toast.error(e?.message ?? t("Error.generic", { ns: "common" }));
      setBackups((prev) => prev ?? []);
      onOperationFailedRef.current?.();
    }
  }, [vmid, toast, t]);

  useEffect(() => {
    load();
  }, [load]);

  /* 這台機器的備份／還原任務跑完（從執行中清單消失）就重抓清單 */
  const taskActive = Boolean(findActiveBackupJob(jobs, vmid));
  const wasActiveRef = useRef(false);
  useEffect(() => {
    if (taskActive) setQueued(false);
    if (wasActiveRef.current && !taskActive) load();
    wasActiveRef.current = taskActive;
  }, [taskActive, load]);

  useEffect(() => {
    if (!queued) return undefined;
    const timer = window.setTimeout(() => {
      setQueued(false);
      load();
    }, QUEUED_FALLBACK_MS);
    return () => window.clearTimeout(timer);
  }, [queued, load]);

  const taskRunning = taskActive || queued;
  const locked = busy || taskRunning;
  const limit = capability?.max_count ?? null;
  const atLimit = limit != null && (backups?.length ?? 0) >= limit;

  const run = async (fn, successMsg, { queuesTask = false, after } = {}) => {
    setBusy(true);
    try {
      await fn();
      toast.success(successMsg);
      after?.();
      if (queuesTask) setQueued(true);
      else await load();
    } catch (e) {
      toast.error(e?.message ?? t("BackupsTab.operationFailed"));
      onOperationFailedRef.current?.();
    } finally {
      setBusy(false);
    }
  };

  async function handleRestore(backup) {
    const ok = await confirm({
      title: t("BackupsTab.restoreConfirmTitle"),
      message: t("BackupsTab.restoreConfirmDesc", {
        time: backup.created_at ? formatDateTime(backup.created_at * 1000) : "—",
      }),
      confirmText: t("BackupsTab.restore"),
      danger: true,
    });
    if (ok) {
      run(() => ResourcesService.restoreBackup(vmid, backup.volid), t("BackupsTab.restoreQueued"), {
        queuesTask: true,
      });
    }
  }

  async function handleDelete(backup) {
    const ok = await confirm({
      title: t("BackupsTab.deleteConfirmTitle"),
      message: t("BackupsTab.deleteConfirmDesc"),
      confirmText: t("BackupsTab.delete"),
      danger: true,
    });
    if (ok) run(() => ResourcesService.deleteBackup(vmid, backup.volid), t("BackupsTab.backupDeleted"));
  }

  /* 取消或點背景關閉時一併清空表單，下次開啟不會殘留上一輪填到一半的內容 */
  const closeCreate = () => {
    setCreateOpen(false);
    setDescription("");
  };

  const handleCreate = () => {
    run(
      () => ResourcesService.createBackup(vmid, { description: description.trim() || undefined }),
      t("BackupsTab.backupQueued"),
      { queuesTask: true, after: closeCreate },
    );
  };

  if (backups === null) return <LoadingState />;

  const createButton = (
    <button
      type="button"
      className={styles.btnPrimary}
      disabled={locked || atLimit}
      title={atLimit ? t("BackupsTab.limitReachedHint", { limit }) : undefined}
      onClick={() => setCreateOpen(true)}
    >
      <MIcon name="add" size={14} />
      {t("BackupsTab.createBackup")}
    </button>
  );

  return (
    <div className={styles.tabStack}>
      {/* 操作按鈕 portal 到分頁列右側的工具槽，與分頁切換器同列 */}
      {toolbar && createPortal(createButton, toolbar)}

      <p className={styles.rpHint}>
        <MIcon name="info" size={14} />
        {t("BackupsTab.notice")}
      </p>
      {capability?.requires_shutdown && (
        <p className={styles.rpHint}>
          <MIcon name="power_settings_new" size={14} />
          {t("BackupsTab.shutdownNotice")}
        </p>
      )}
      {taskRunning && (
        <p className={styles.rpHint} role="status">
          <MIcon name="sync" size={14} />
          {t("BackupsTab.inProgress")}
        </p>
      )}

      <div className={styles.card}>
        {backups.length === 0 ? (
          <EmptyState
            icon="backup"
            title={t("BackupsTab.emptyTitle")}
            action={
              <button
                type="button"
                className={styles.btnPrimary}
                disabled={locked}
                onClick={() => setCreateOpen(true)}
              >
                <MIcon name="add" size={14} />
                {t("BackupsTab.createBackup")}
              </button>
            }
          />
        ) : (
          <div className={styles.tableScroll}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.th}>{t("BackupsTab.colCreatedAt")}</th>
                  <th className={styles.th}>{t("BackupsTab.colDesc")}</th>
                  <th className={styles.th}>{t("BackupsTab.colSize")}</th>
                  <th className={styles.th}>{t("BackupsTab.colActions")}</th>
                </tr>
              </thead>
              <tbody>
                {backups.map((backup) => (
                  <tr key={backup.volid} className={styles.tr}>
                    <td className={styles.td}>
                      {backup.created_at ? formatDateTime(backup.created_at * 1000) : "—"}
                    </td>
                    <td className={`${styles.td} ${styles.mutedCell}`}>{backup.description || "—"}</td>
                    <td className={`${styles.td} ${styles.mutedCell}`}>{formatBackupSize(backup.size)}</td>
                    <td className={`${styles.td} ${styles.tdActions}`}>
                      <button
                        type="button"
                        className={styles.btnSecondary}
                        disabled={locked}
                        onClick={() => handleRestore(backup)}
                      >
                        <MIcon name="history" size={14} />
                        {t("BackupsTab.restore")}
                      </button>
                      <button
                        type="button"
                        className={styles.btnDangerOutline}
                        disabled={locked}
                        onClick={() => handleDelete(backup)}
                      >
                        <MIcon name="delete_outline" size={14} />
                        {t("BackupsTab.delete")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {createDialog.open && (
        <Modal
          as="form"
          onSubmit={(e) => { e.preventDefault(); handleCreate(); }}
          closing={createDialog.closing}
          onClose={closeCreate}
          title={t("BackupsTab.createTitle")}
          description={t("BackupsTab.createDesc")}
          actions={
            <>
              <button type="button" className={styles.btnSecondary} onClick={closeCreate}>
                {t("BackupsTab.cancel")}
              </button>
              <button type="submit" className={styles.btnPrimary} disabled={locked}>
                {busy ? t("BackupsTab.submitting") : t("BackupsTab.create")}
              </button>
            </>
          }
        >
          <div className={styles.field}>
            <label htmlFor="backup-desc">{t("BackupsTab.descLabel")}</label>
            <textarea
              id="backup-desc"
              rows={3}
              maxLength={DESCRIPTION_MAX_LENGTH}
              placeholder={t("BackupsTab.descPlaceholder")}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </Modal>
      )}
    </div>
  );
}
