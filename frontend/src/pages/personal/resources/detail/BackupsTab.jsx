import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./ResourceDetailPage.module.scss";
import MIcon from "../../../../components/MIcon";
import DataTable, { DataTableCells } from "../../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../../components/DataTable/features";
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
import { useBackupsQuery, useQueryErrorEffect } from "../../../../queries/resourceDetail";

/* 入列後到任務出現在「執行中任務」之間的空窗：超過這段時間還沒看到任務，就當它已經跑完 */
const QUEUED_FALLBACK_MS = 20000;
const DESCRIPTION_MAX_LENGTH = 120;
const EMPTY = Object.freeze([]);

/* ── 備份清單的欄位（模組層級：cell／header 會被當成元件掛載，render 裡新建會整格重掛） ── */
function BackupHeader({ column }) {
  const { t } = useTranslation("personal");
  return t(column.columnDef.meta.labelKey);
}

/* 操作鈕的鎖定狀態與處理函式是分頁層級的，經 BackupRow 的 rowState 傳進來 */
function BackupActionsCell({ rowState }) {
  const { t } = useTranslation("personal");
  const { locked, onRestore, onDelete } = rowState;
  return (
    <>
      <button type="button" className={styles.btnSecondary} disabled={locked} onClick={onRestore}>
        <MIcon name="history" size={14} />
        {t("BackupsTab.restore")}
      </button>
      <button type="button" className={styles.btnDangerOutline} disabled={locked} onClick={onDelete}>
        <MIcon name="delete_outline" size={14} />
        {t("BackupsTab.delete")}
      </button>
    </>
  );
}

const columnHelper = createColumnHelper();
const BACKUP_COLUMNS = columnHelper.columns([
  columnHelper.accessor("created_at", {
    header: BackupHeader,
    cell: (info) => (info.getValue() ? formatDateTime(info.getValue() * 1000) : "—"),
    meta: { labelKey: "BackupsTab.colCreatedAt", thClassName: styles.th, tdClassName: `${styles.td} ${styles.nowrapCell}` },
  }),
  columnHelper.accessor("description", {
    header: BackupHeader,
    cell: (info) => info.getValue() || "—",
    meta: { labelKey: "BackupsTab.colDesc", thClassName: styles.th, tdClassName: `${styles.td} ${styles.mutedCell}` },
  }),
  columnHelper.accessor("size", {
    header: BackupHeader,
    cell: (info) => formatBackupSize(info.getValue()),
    meta: { labelKey: "BackupsTab.colSize", thClassName: styles.th, tdClassName: `${styles.td} ${styles.mutedCell}` },
  }),
  columnHelper.display({
    id: "actions",
    header: BackupHeader,
    cell: BackupActionsCell,
    meta: { labelKey: "BackupsTab.colActions", thClassName: styles.th, tdClassName: `${styles.td} ${styles.tdActions}` },
  }),
]);

const getBackupId = (backup) => backup.volid;

/* 鎖定狀態是分頁層級的、清單資料沒變也會變：經列元件的 props 進 rowState，cell 才會跟著重繪 */
function BackupRow({ row, backup, locked, onRestore, onDelete }) {
  return (
    <tr className={styles.tr}>
      <DataTableCells
        row={row}
        rowState={{ locked, onRestore: () => onRestore(backup), onDelete: () => onDelete(backup) }}
      />
    </tr>
  );
}

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
  const [createOpen, setCreateOpen] = useState(false);
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState(false);
  const createDialog = useDialogPresence(createOpen);
  /* 用 ref 持有 callback：不讓它的 identity 進 effect 的依賴 */
  const onOperationFailedRef = useRef(onOperationFailed);
  useEffect(() => {
    onOperationFailedRef.current = onOperationFailed;
  }, [onOperationFailed]);

  /* 備份清單：每次載入失敗都提示並請上層重查可用性；已有清單時沿用，從沒載到就當成空的 */
  const backupsQuery = useBackupsQuery(vmid);
  useQueryErrorEffect(backupsQuery, (e) => {
    toast.error(e?.message ?? t("Error.generic", { ns: "common" }));
    onOperationFailedRef.current?.();
  });
  const backups = backupsQuery.data ?? (backupsQuery.isError ? [] : null);
  const { refetch } = backupsQuery;
  const backupTable = useTable({
    features: baseFeatures,
    columns: BACKUP_COLUMNS,
    data: backups ?? EMPTY,
    getRowId: getBackupId,
  });

  /* 這台機器的備份／還原任務跑完（從執行中清單消失）就重抓清單 */
  const taskActive = Boolean(findActiveBackupJob(jobs, vmid));
  const wasActiveRef = useRef(false);
  useEffect(() => {
    if (taskActive) setQueued(false);
    if (wasActiveRef.current && !taskActive) refetch();
    wasActiveRef.current = taskActive;
  }, [taskActive, refetch]);

  useEffect(() => {
    if (!queued) return undefined;
    const timer = window.setTimeout(() => {
      setQueued(false);
      refetch();
    }, QUEUED_FALLBACK_MS);
    return () => window.clearTimeout(timer);
  }, [queued, refetch]);

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
      else await refetch();
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
            <DataTable
              table={backupTable}
              className={styles.table}
              renderRow={(row) => (
                <BackupRow
                  key={row.id}
                  row={row}
                  backup={row.original}
                  locked={locked}
                  onRestore={handleRestore}
                  onDelete={handleDelete}
                />
              )}
            />
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
