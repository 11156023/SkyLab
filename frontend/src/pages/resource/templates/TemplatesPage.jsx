import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./TemplatesPage.module.scss";
import MIcon from "../../../components/MIcon";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { TemplatesService } from "../../../services/templates";
import { downloadBlob } from "../../../services/api";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { TemplateStatusBadge } from "./TemplateBadges";
import TemplateCloneDialog from "./TemplateCloneDialog";
import TemplateFormDialog from "./TemplateFormDialog";
import LoadingState from "../../../components/LoadingState/LoadingState";
import PageHeader from "../../../components/PageHeader/PageHeader";

function visibilityLabel(template, t) {
  return template.visibility === "global"
    ? t("TemplatesPage.visibilityGlobal")
    : t("TemplatesPage.visibilityPrivate");
}

const formatBytes = (bytes) => {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
};

/** 使用手冊（附件）瀏覽與下載 */
function ManualDialog({ template, closing = false, onClose }) {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const [attachments, setAttachments] = useState(null);
  const [downloadingId, setDownloadingId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    TemplatesService.listAttachments(template.id)
      .then((res) => !cancelled && setAttachments(res?.data ?? []))
      .catch((e) => {
        if (!cancelled) {
          toast.error(e?.message ?? t("TemplatesPage.attachmentLoadFailed"));
          setAttachments([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [template.id, toast, t]);

  const handleDownload = async (attachment) => {
    setDownloadingId(attachment.id);
    try {
      const blob = await TemplatesService.downloadAttachment(template.id, attachment.id);
      downloadBlob(blob, attachment.filename);
    } catch (e) {
      toast.error(e?.message ?? t("TemplatesPage.downloadFailed"));
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onClick={onClose}
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <span className={styles.modalTitle}>
          <MIcon name="description" size={20} />
          {t("TemplatesPage.manualTitle", { name: template.name })}
        </span>
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
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            {t("TemplatesPage.close")}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 單列的「⋯」操作選單 */
function RowMenu({ template, cycleBusy, onClone, onEdit, onManual, onRetry, onCycle, onDelete, onClose, anchorRef, closing = false }) {
  const { t } = useTranslation("resource");
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      if (!ref.current?.contains(e.target) && !anchorRef?.current?.contains(e.target)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose, anchorRef]);

  return (
    <div ref={ref} className={`${styles.rowMenu} ${closing ? styles.rowMenuOut : ""}`}>
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
    </div>
  );
}

function ManagementRow({ template, cycleBusy, onClone, onEdit, onManual, onRetry, onCycle, onDelete }) {
  const { t } = useTranslation("resource");
  const [menuOpen, setMenuOpen] = useState(false);
  const menu = useDialogPresence(menuOpen, 130);
  const menuBtnRef = useRef(null);

  return (
    <tr className={styles.tr}>
      <td className={styles.td}>
        <div className={styles.nameCell}>
          <span className={styles.namePrimary}>{template.name}</span>
          {template.pve_exists === false && template.status === "ready" && (
            <span className={styles.pveMissing} title={t("TemplatesPage.pveMissingTitle")}>
              <MIcon name="warning" size={13} />
              {t("TemplatesPage.pveMissingLabel")}
            </span>
          )}
        </div>
        {template.description && (
          <p className={styles.nameDesc}>{template.description}</p>
        )}
        {template.error_message && (
          <p className={styles.nameError}>{template.error_message}</p>
        )}
      </td>
      <td className={`${styles.td} ${styles.monoCell}`}>{template.pve_vmid}</td>
      <td className={styles.td}>
        <span className={styles.typeChip}>{template.resource_type}</span>
      </td>
      <td className={styles.td}>
        <TemplateStatusBadge status={template.status} />
      </td>
      <td className={`${styles.td} ${styles.mutedCell}`}>
        {visibilityLabel(template, t)}
      </td>
      <td className={`${styles.td} ${styles.mutedCell}`}>{t("TemplatesPage.versionLabel", { version: template.version })}</td>
      <td className={`${styles.td} ${styles.tdMenu}`}>
        <div className={styles.menuWrap}>
          {menu.open && (
            <RowMenu
              template={template}
              cycleBusy={cycleBusy}
              onClone={onClone}
              onEdit={onEdit}
              onManual={onManual}
              onRetry={onRetry}
              onCycle={onCycle}
              onDelete={onDelete}
              onClose={() => setMenuOpen(false)}
              anchorRef={menuBtnRef}
              closing={menu.closing}
            />
          )}
          <button
            ref={menuBtnRef}
            type="button"
            className={styles.menuBtn}
            onClick={() => setMenuOpen((v) => !v)}
            title={t("TemplatesPage.moreActionsTitle")}
          >
            <MIcon name="more_horiz" size={18} />
          </button>
        </div>
      </td>
    </tr>
  );
}

export default function TemplatesPage() {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const confirm = useConfirm();
  const [templates, setTemplates] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState(null);
  const [cloneTarget, setCloneTarget] = useState(null);
  const [manualTarget, setManualTarget] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const createDialog  = useDialogPresence(createOpen);
  const editDialog    = useDialogPresence(editTarget);
  const manualDialog  = useDialogPresence(manualTarget);
  const cloneDialog   = useDialogPresence(cloneTarget);
  const deleteDialog  = useDialogPresence(deleteTarget);
  const [cycleBusy, setCycleBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const timerRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const res = await TemplatesService.list();
      setTemplates(res?.data ?? []);
      return res?.data ?? [];
    } catch (e) {
      toast.error(e?.message ?? t("TemplatesPage.loadFailed"));
      setTemplates((prev) => prev ?? []);
      return [];
    }
  }, [toast, t]);

  /** 有 creating/updating 中的範本時 4 秒輪詢，否則 30 秒 */
  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      const list = await load();
      if (cancelled) return;
      const active = list.some((tpl) => tpl.status === "creating" || tpl.status === "updating");
      timerRef.current = setTimeout(tick, active ? 4_000 : 30_000);
    };

    tick();
    return () => {
      cancelled = true;
      clearTimeout(timerRef.current);
    };
  }, [load]);

  const refresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

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
      await load();
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
      await load();
    } catch (e) {
      toast.error(e?.message ?? t("TemplatesPage.retryFailed"));
    } finally {
      setCycleBusy(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await TemplatesService.remove(deleteTarget.id);
      toast.success(t("TemplatesPage.deleteQueuedToast"));
      setDeleteTarget(null);
      await load();
    } catch (e) {
      toast.error(e?.message ?? t("TemplatesPage.deleteFailed"));
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  const list = templates ?? [];

  return (
    <div className={styles.page}>
      <PageHeader title={t("TemplatesPage.pageTitle")} subtitle={t("TemplatesPage.pageSubtitle")}>
        <div className={styles.pageActions}>
          <button
            type="button"
            className={styles.btnSecondary}
            onClick={refresh}
            disabled={refreshing}
          >
            <MIcon name="sync" size={16} />
            {refreshing ? t("TemplatesPage.refreshing") : t("TemplatesPage.refresh")}
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            onClick={() => setCreateOpen(true)}
          >
            <MIcon name="add" size={16} />
            {t("TemplatesPage.createFromVm")}
          </button>
        </div>
      </PageHeader>

      {templates === null ? (
        <LoadingState fullPage text={t("TemplatesPage.loadingTemplates")} />
      ) : list.length === 0 ? (
        <div className={styles.card}>
          <EmptyState
            icon="widgets"
            title={t("TemplatesPage.emptyTitle")}
          />
        </div>
      ) : (
        <div className={styles.card}>
          <div className={styles.tableScroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>{t("TemplatesPage.columnName")}</th>
                <th className={styles.th}>{t("TemplatesPage.columnVmid")}</th>
                <th className={styles.th}>{t("TemplatesPage.columnType")}</th>
                <th className={styles.th}>{t("TemplatesPage.columnStatus")}</th>
                <th className={styles.th}>{t("TemplatesPage.columnVisibility")}</th>
                <th className={styles.th}>{t("TemplatesPage.columnVersion")}</th>
                <th className={styles.th} />
              </tr>
            </thead>
            <tbody>
              {list.map((template) => (
                <ManagementRow
                  key={template.id}
                  template={template}
                  cycleBusy={cycleBusy}
                  onClone={setCloneTarget}
                  onEdit={setEditTarget}
                  onManual={setManualTarget}
                  onRetry={handleRetry}
                  onCycle={handleCycle}
                  onDelete={setDeleteTarget}
                />
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}

      {createDialog.open && (
        <TemplateFormDialog
          closing={createDialog.closing}
          onClose={() => setCreateOpen(false)}
          onSaved={() => load()}
        />
      )}
      {editDialog.open && (
        <TemplateFormDialog
          template={editDialog.item}
          closing={editDialog.closing}
          onClose={() => setEditTarget(null)}
          onSaved={() => load()}
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
          canBatch
          closing={cloneDialog.closing}
          onClose={() => setCloneTarget(null)}
        />
      )}

      {deleteDialog.open && (
        <div
          className={`${styles.modalOverlay} ${deleteDialog.closing ? styles.modalOverlayOut : ""}`}
          onClick={() => setDeleteTarget(null)}
        >
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <span className={styles.modalTitle}>{t("TemplatesPage.deleteConfirmTitle", { name: deleteDialog.item.name })}</span>
            <p className={styles.modalDesc}>
              {t("TemplatesPage.deleteConfirmDesc")}
            </p>
            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={() => setDeleteTarget(null)}
              >
                {t("TemplatesPage.cancel")}
              </button>
              <button
                type="button"
                className={styles.btnDanger}
                disabled={deleting}
                onClick={handleDelete}
              >
                {deleting ? t("TemplatesPage.deleting") : t("TemplatesPage.confirmDelete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
