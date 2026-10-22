import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { createPortal } from "react-dom";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import useAnchoredMenu from "../../../hooks/useAnchoredMenu";
import styles from "./TemplatesPage.module.scss";
import MIcon from "../../../components/MIcon";
import DataTable, { DataTableCells } from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import Modal from "../../../components/Modal/Modal";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { TemplatesService } from "../../../services/templates";
import {
  useTemplateAttachmentsQuery,
  useTemplateCacheUpdater,
  useTemplatesQuery,
} from "../../../queries/templates";
import { downloadBlob } from "../../../services/api";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { useLoadError, useLoadFailureMessage } from "../../../hooks/useQueryLoadState";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { TemplateStatusBadge } from "./TemplateBadges";
import TemplateCloneDialog from "./TemplateCloneDialog";
import TemplateFormDialog from "./TemplateFormDialog";
import { formatBytes } from "./templateFormat";
import LoadingState from "../../../components/LoadingState/LoadingState";
import PageHeader from "../../../components/PageHeader/PageHeader";

function visibilityLabel(template, t) {
  return template.visibility === "global"
    ? t("TemplatesPage.visibilityGlobal")
    : t("TemplatesPage.visibilityPrivate");
}

const NO_ATTACHMENTS = Object.freeze([]);
const NO_TEMPLATES = Object.freeze([]);

/** 使用手冊（附件）瀏覽與下載 */
function ManualDialog({ template, closing = false, onClose }) {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const [downloadingId, setDownloadingId] = useState(null);

  /* 附件清單走 TanStack Query（與編輯對話框共用同一份快取）；載不到時提示並當成沒有附件，
     已有資料時背景重抓失敗沿用原清單 */
  const attachmentsQuery = useTemplateAttachmentsQuery(template.id);
  const attachmentsData = attachmentsQuery.data;
  /* 首載失敗後切回視窗的重試期間仍算失敗（不閃回載入中）；依訊息字串觸發，同一個錯誤只提示一次 */
  const attachmentsLoadError = useLoadError(attachmentsQuery).error;
  const attachmentsError = attachmentsData === undefined ? attachmentsLoadError : null;
  const attachments = attachmentsData ?? (attachmentsError ? NO_ATTACHMENTS : null);
  const attachmentsErrorMessage = attachmentsError ? attachmentsError.message ?? t("Error.generic", { ns: "common" }) : null;
  useEffect(() => {
    if (attachmentsErrorMessage) toast.error(attachmentsErrorMessage);
  }, [attachmentsErrorMessage, toast]);

  const handleDownload = async (attachment) => {
    setDownloadingId(attachment.id);
    try {
      const blob = await TemplatesService.downloadAttachment(template.id, attachment.id);
      downloadBlob(blob, attachment.filename);
    } catch (e) {
      toast.error(e?.message ?? t("Error.generic", { ns: "common" }));
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <Modal
      closing={closing}
      onClose={onClose}
      size="md"
      icon={<MIcon name="description" size={20} />}
      title={t("TemplatesPage.manualTitle", { name: template.name })}
      actions={
        <button type="button" className={styles.btnSecondary} onClick={onClose}>
          {t("TemplatesPage.close")}
        </button>
      }
    >
      {attachments === null ? (
        <LoadingState text={t("TemplatesPage.loadingAttachments")} />
      ) : attachments.length === 0 ? (
        <p className={styles.stateText}>{t("TemplatesPage.noAttachments")}</p>
      ) : (
        <div className={styles.attachList}>
          {attachments.map((a) => (
            <div key={a.id} className={styles.attachItem}>
              <MIcon name="description" size={15} />
              <span className={styles.attachName}>{a.filename}</span>
              <span className={styles.attachSize}>{formatBytes(a.size_bytes)}</span>
              <button
                type="button"
                className={styles.attachBtn}
                disabled={downloadingId === a.id}
                onClick={() => handleDownload(a)}
              >
                <MIcon name="download" size={15} />
                {downloadingId === a.id ? t("TemplatesPage.downloading") : t("TemplatesPage.download")}
              </button>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

/** 單列的「⋯」操作選單。
   portal 到 body 並用 fixed 定位：選單原本絕對定位在 .tableScroll 裡，
   overflow-x: auto 會連 y 軸一起變裁切上下文，選單被切掉還撑出捲軸（同 PowerMenu 的做法） */
const ROW_MENU_WIDTH = 200;

function RowMenu({ template, cycleBusy, onClone, onEdit, onManual, onRetry, onCycle, onDelete, onClose, anchorRef, closing = false }) {
  const { t } = useTranslation("resource");
  const { ref, pos } = useAnchoredMenu({ anchorRef, onClose, width: ROW_MENU_WIDTH });

  return createPortal(
    <div
      ref={ref}
      className={`${styles.rowMenu} ${closing ? styles.rowMenuOut : ""}`}
      style={pos ? { top: pos.top, left: pos.left } : { top: 0, left: 0, visibility: "hidden" }}
    >
      <button
        type="button"
        className={styles.rowMenuItem}
        disabled={template.status !== "ready"}
        onClick={() => { onClose(); onClone(template); }}
      >
        <MIcon name="content_copy" size={15} />
        {t("TemplatesPage.menuClone")}
      </button>
      <button
        type="button"
        className={styles.rowMenuItem}
        onClick={() => { onClose(); onEdit(template); }}
      >
        <MIcon name="edit" size={15} />
        {t("TemplatesPage.menuEdit")}
      </button>
      {template.attachment_count > 0 && (
        <button
          type="button"
          className={styles.rowMenuItem}
          onClick={() => { onClose(); onManual(template); }}
        >
          <MIcon name="description" size={15} />
          {t("TemplatesPage.menuManual", { count: template.attachment_count })}
        </button>
      )}
      <div className={styles.rowMenuDivider} />
      {template.status === "failed" && (
        <button
          type="button"
          className={styles.rowMenuItem}
          disabled={cycleBusy}
          onClick={() => { onClose(); onRetry(template.id); }}
        >
          <MIcon name="restart_alt" size={15} />
          {t("TemplatesPage.menuRetry")}
        </button>
      )}
      {template.status === "ready" && (
        <button
          type="button"
          className={styles.rowMenuItem}
          disabled={cycleBusy}
          onClick={() => { onClose(); onCycle(template.id, "start"); }}
        >
          <MIcon name="sync" size={15} />
          {t("TemplatesPage.menuStartCycle")}
        </button>
      )}
      {template.status === "updating" && (
        <>
          <button
            type="button"
            className={styles.rowMenuItem}
            disabled={cycleBusy}
            onClick={() => { onClose(); onCycle(template.id, "finish"); }}
          >
            <MIcon name="sync" size={15} />
            {t("TemplatesPage.menuFinishCycle")}
          </button>
          <button
            type="button"
            className={styles.rowMenuItem}
            disabled={cycleBusy}
            onClick={() => { onClose(); onCycle(template.id, "cancel"); }}
          >
            {t("TemplatesPage.menuCancelCycle")}
          </button>
        </>
      )}
      <div className={styles.rowMenuDivider} />
      <button
        type="button"
        className={`${styles.rowMenuItem} ${styles.rowMenuItemDanger}`}
        onClick={() => { onClose(); onDelete(template); }}
      >
        <MIcon name="delete_outline" size={15} />
        {t("TemplatesPage.menuDelete")}
      </button>
    </div>,
    document.body,
  );
}

/* ── 表格欄位（TanStack Table）──
   v9 的 header／cell 會被當元件掛載，欄位定義放模組層級才不會每次 render 整格重新掛載。
   「⋯」選單的開關狀態在 ManagementRow，經 rowState 交給動作欄 */
const columnHelper = createColumnHelper();

function useColumns() {
  const { t } = useTranslation("resource");
  return [
    t("TemplatesPage.columnName"),
    t("TemplatesPage.columnVmid"),
    t("TemplatesPage.columnType"),
    t("TemplatesPage.columnStatus"),
    t("TemplatesPage.columnVisibility"),
    t("TemplatesPage.columnVersion"),
    t("TemplatesPage.columnActions"),
  ];
}

/* 欄名來自 useColumns() */
function ColumnTitle({ index }) {
  return useColumns()[index];
}

function TemplateNameCell({ template }) {
  const { t } = useTranslation("resource");
  return (
    <>
      <div className={styles.nameCell}>
        <span className={styles.namePrimary}>{template.name}</span>
        {template.pve_exists === false && template.status === "ready" && (
          <span className={styles.pveMissing} title={t("TemplatesPage.pveMissingTitle")}>
            <MIcon name="warning" size={13} />
            {t("TemplatesPage.pveMissingLabel")}
          </span>
        )}
        {template.password_settable === false && (
          <span
            className={`${styles.badge} ${styles.badge_muted}`}
            title={t("TemplatesPage.passwordUnsettableTitle")}
          >
            <MIcon name="lock" size={13} />
            {t("TemplatesPage.passwordUnsettableLabel")}
          </span>
        )}
      </div>
      {template.description && (
        <p className={styles.nameDesc}>{template.description}</p>
      )}
      {template.error_message && (
        <p className={styles.nameError}>{template.error_message}</p>
      )}
    </>
  );
}

function VisibilityCell({ template }) {
  const { t } = useTranslation("resource");
  return visibilityLabel(template, t);
}

function VersionCell({ version }) {
  const { t } = useTranslation("resource");
  return t("TemplatesPage.versionLabel", { version });
}

function TemplateMenuCell({
  template,
  cycleBusy,
  menuOpen,
  menuClosing,
  menuBtnRef,
  onToggleMenu,
  onCloseMenu,
  onClone,
  onEdit,
  onManual,
  onRetry,
  onCycle,
  onDelete,
}) {
  const { t } = useTranslation("resource");
  return (
    <div className={styles.menuWrap}>
      {menuOpen && (
        <RowMenu
          template={template}
          cycleBusy={cycleBusy}
          onClone={onClone}
          onEdit={onEdit}
          onManual={onManual}
          onRetry={onRetry}
          onCycle={onCycle}
          onDelete={onDelete}
          onClose={onCloseMenu}
          anchorRef={menuBtnRef}
          closing={menuClosing}
        />
      )}
      <button
        ref={menuBtnRef}
        type="button"
        className={styles.menuBtn}
        data-guide="templates-menu"
        onClick={onToggleMenu}
        title={t("TemplatesPage.moreActionsTitle")}
        aria-label={t("TemplatesPage.moreActionsTitle")}
      >
        <MIcon name="more_vert" size={18} />
      </button>
    </div>
  );
}

const templateColumns = columnHelper.columns([
  columnHelper.accessor("name", {
    header: () => <ColumnTitle index={0} />,
    cell: ({ row }) => <TemplateNameCell template={row.original} />,
    meta: { thClassName: styles.th, tdClassName: styles.td, tdProps: { "data-guide": "templates-name" } },
  }),
  columnHelper.accessor("pve_vmid", {
    header: () => <ColumnTitle index={1} />,
    cell: ({ row }) => row.original.pve_vmid,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.monoCell}` },
  }),
  columnHelper.accessor("resource_type", {
    header: () => <ColumnTitle index={2} />,
    cell: ({ row }) => <span className={styles.typeChip}>{row.original.resource_type}</span>,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("status", {
    header: () => <ColumnTitle index={3} />,
    cell: ({ row }) => <TemplateStatusBadge status={row.original.status} />,
    meta: { thClassName: styles.th, tdClassName: styles.td, tdProps: { "data-guide": "templates-status" } },
  }),
  columnHelper.accessor("visibility", {
    header: () => <ColumnTitle index={4} />,
    cell: ({ row }) => <VisibilityCell template={row.original} />,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.mutedCell}` },
  }),
  columnHelper.accessor("version", {
    header: () => <ColumnTitle index={5} />,
    cell: ({ row }) => <VersionCell version={row.original.version} />,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.mutedCell}` },
  }),
  columnHelper.display({
    id: "actions",
    header: () => <ColumnTitle index={6} />,
    cell: ({ row, rowState }) => <TemplateMenuCell template={row.original} {...rowState} />,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.tdMenu}` },
  }),
]);

/* row 是 TanStack 的列，template 是它的原始資料（一併傳進來，編譯後的元件才會跟著資料重繪） */
function ManagementRow({ row, template, cycleBusy, onClone, onEdit, onManual, onRetry, onCycle, onDelete }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menu = useDialogPresence(menuOpen, 130);
  const menuBtnRef = useRef(null);

  return (
    <tr className={styles.tr}>
      <DataTableCells
        row={row}
        rowState={{
          cycleBusy,
          menuOpen: menu.open,
          menuClosing: menu.closing,
          menuBtnRef,
          onToggleMenu: () => setMenuOpen((v) => !v),
          onCloseMenu: () => setMenuOpen(false),
          onClone,
          onEdit,
          onManual,
          onRetry,
          onCycle,
          onDelete,
        }}
      />
    </tr>
  );
}

export default function TemplatesPage() {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const confirm = useConfirm();
  /* 範本清單走 TanStack Query：有建立中／更新中的範本時 4 秒輪詢，否則 30 秒；分頁隱藏時暫停。
     載入失敗提示一次（同一串失敗不重複提示，成功過再失敗才再提示），已有資料時沿用原清單；首載失敗當成沒有範本 */
  const templatesQuery = useTemplatesQuery();
  const templateCache = useTemplateCacheUpdater();
  const templatesData = templatesQuery.data;

  /* 「首載失敗」看 useLoadError：沒資料時 v5 每次重試都把 isError 清掉，直接看它會在重試期間閃回整頁載入中 */
  const templatesLoadError = useLoadError(templatesQuery).error;
  const templates = templatesData ?? (templatesLoadError ? NO_TEMPLATES : null);
  const templatesFailedMessage = useLoadFailureMessage(templatesQuery, t("Error.generic", { ns: "common" }));
  useEffect(() => {
    if (templatesFailedMessage) toast.error(templatesFailedMessage);
  }, [templatesFailedMessage, toast]);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState(null);
  const [cloneTarget, setCloneTarget] = useState(null);
  const [manualTarget, setManualTarget] = useState(null);
  const createDialog  = useDialogPresence(createOpen);
  const editDialog    = useDialogPresence(editTarget);
  const manualDialog  = useDialogPresence(manualTarget);
  const cloneDialog   = useDialogPresence(cloneTarget);
  const [cycleBusy, setCycleBusy] = useState(false);

  /** 操作後重抓清單（畫面沿用現有資料不閃）；呼叫端等它完成才解除忙碌狀態 */
  const reload = () => templateCache.invalidate();

  const handleCycle = async (templateId, action) => {
    if (action === "finish") {
      const ok = await confirm({
        title: t("TemplatesPage.finishCycleConfirmTitle"),
        message: t("TemplatesPage.finishCycleConfirmMessage"),
        confirmText: t("TemplatesPage.shutdownAndConvert"),
        danger: true,
      });
      if (!ok) return;
    }
    setCycleBusy(true);
    try {
      if (action === "start") await TemplatesService.startUpdateCycle(templateId);
      else if (action === "finish") await TemplatesService.finishUpdateCycle(templateId);
      else await TemplatesService.cancelUpdateCycle(templateId);
      toast.success(
        action === "start"
          ? t("TemplatesPage.cycleStartedToast")
          : action === "finish"
            ? t("TemplatesPage.cycleFinishedToast")
            : t("TemplatesPage.cycleCancelledToast"),
      );
      await reload();
    } catch (e) {
      toast.error(e?.message ?? t("TemplatesPage.cycleActionFailed"));
    } finally {
      setCycleBusy(false);
    }
  };

  const handleRetry = async (templateId) => {
    const ok = await confirm({
      title: t("TemplatesPage.retryConfirmTitle"),
      message: t("TemplatesPage.retryConfirmMessage"),
      confirmText: t("TemplatesPage.shutdownAndConvert"),
      danger: true,
    });
    if (!ok) return;
    setCycleBusy(true);
    try {
      await TemplatesService.retry(templateId);
      toast.success(t("TemplatesPage.retryToast"));
      await reload();
    } catch (e) {
      toast.error(e?.message ?? t("TemplatesPage.retryFailed"));
    } finally {
      setCycleBusy(false);
    }
  };

  /* 刪除確認走共用 useConfirm（樣式規範：勿自建本地 ConfirmModal），
     按下確認即關閉彈窗，進度以 toast 呈現 */
  const handleDelete = async (template) => {
    const ok = await confirm({
      title: t("TemplatesPage.deleteConfirmTitle", { name: template.name }),
      message: t("TemplatesPage.deleteConfirmDesc"),
      confirmText: t("TemplatesPage.confirmDelete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await TemplatesService.remove(template.id);
      toast.success(t("TemplatesPage.deleteQueuedToast"));
      await reload();
    } catch (e) {
      toast.error(e?.message ?? t("TemplatesPage.deleteFailed"));
    }
  };

  const list = templates ?? NO_TEMPLATES;
  const table = useTable({
    features: baseFeatures,
    columns: templateColumns,
    data: list,
    getRowId: (template) => String(template.id),
  });

  return (
    <div className={styles.page}>
      <PageHeader title={t("TemplatesPage.pageTitle")}>
        {/* 有範本才在頁首放「從 VM 建立」；沒有時空狀態那顆就是唯一入口，載入中也先不放 */}
        {list.length > 0 && (
          <div className={styles.pageActions}>
            <button
              type="button"
              className={styles.btnPrimary}
              data-guide="templates-create"
              onClick={() => setCreateOpen(true)}
            >
              <MIcon name="add" size={16} />
              {t("TemplatesPage.createFromVm")}
            </button>
          </div>
        )}
      </PageHeader>

      {templates === null ? (
        <LoadingState fullPage text={t("TemplatesPage.loadingTemplates")} />
      ) : list.length === 0 ? (
        /* 同資源管理、GPU 管理：頁面層級空狀態不包卡片，直接在剩餘高度垂直置中 */
        <EmptyState
          icon="widgets"
          title={t("TemplatesPage.emptyTitle")}
          action={<button type="button" className={styles.btnPrimary} data-guide="templates-create" onClick={() => setCreateOpen(true)}><MIcon name="add" size={16} />{t("TemplatesPage.createFromVm")}</button>}
        />
      ) : (
        <div className={styles.card} data-guide="templates-table">
          <div className={styles.tableScroll}>
          <DataTable
            table={table}
            className={styles.table}
            renderRow={(row) => (
              <ManagementRow
                key={row.id}
                row={row}
                template={row.original}
                cycleBusy={cycleBusy}
                onClone={setCloneTarget}
                onEdit={setEditTarget}
                onManual={setManualTarget}
                onRetry={handleRetry}
                onCycle={handleCycle}
                onDelete={handleDelete}
              />
            )}
          />
          </div>
        </div>
      )}

      {createDialog.open && (
        <TemplateFormDialog
          closing={createDialog.closing}
          onClose={() => setCreateOpen(false)}
          onSaved={() => reload()}
        />
      )}
      {editDialog.open && (
        <TemplateFormDialog
          template={editDialog.item}
          closing={editDialog.closing}
          onClose={() => setEditTarget(null)}
          onSaved={() => reload()}
        />
      )}
      {manualDialog.open && (
        <ManualDialog
          template={manualDialog.item}
          closing={manualDialog.closing}
          onClose={() => setManualTarget(null)}
        />
      )}
      {cloneDialog.open && (
        <TemplateCloneDialog
          template={cloneDialog.item}
          closing={cloneDialog.closing}
          onClose={() => setCloneTarget(null)}
        />
      )}
    </div>
  );
}
