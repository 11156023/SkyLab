import { useCallback, useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import styles from "./settings.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { ProxmoxConfigService } from "../../../services/proxmoxConfig";
import PageHeader from "../../../components/PageHeader/PageHeader";

/**
 * 節點管理（系統管理 → 節點管理）：各 PVE 節點的啟用狀態、連線位址與放置優先度。
 * 節點清單由「PVE 連線」的同步動作更新。2026-09 從「系統設定」的分頁拆成獨立頁面。
 */

/* ── 編輯 Modal：改用彈窗，列本身不再變形（#22） ── */
function NodeEditDialog({ node, saving, closing = false, onClose, onSave }) {
  const { t } = useTranslation("system");
  const titleId = useId();
  const [form, setForm] = useState({ host: node.host, port: node.port, priority: node.priority });

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === "Escape" && !saving) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [saving, onClose]);

  function submit(e) {
    e.preventDefault();
    onSave(node, form);
  }

  /* 掛到 document.body：外層若加了 backdrop-filter／transform，
     position: fixed 的遮罩會被困在那一層、蓋不滿整個畫面（同 PVE 連線對話框） */
  return createPortal(
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onMouseDown={onClose}
    >
      <form
        className={`${styles.modal} ${styles.modalNarrow}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onSubmit={submit}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h2 id={titleId} className={styles.modalTitle}>
            {t("SettingsPage.editNodeTitle", { name: node.name })}
          </h2>
          <button type="button" className={styles.dialogClose} onClick={onClose} disabled={saving} aria-label={t("SettingsPage.close")}>
            <MIcon name="close" size={18} />
          </button>
        </div>

        <label className={styles.field}>
          <span>Host</span>
          <input
            value={form.host}
            onChange={(e) => setForm((p) => ({ ...p, host: e.target.value }))}
            required
          />
        </label>
        <label className={styles.field}>
          <span>Port</span>
          <input
            type="number"
            min={1}
            max={65535}
            value={form.port}
            onChange={(e) => setForm((p) => ({ ...p, port: e.target.value }))}
          />
          <em className={styles.fieldHint}>{t("SettingsPage.nodeHostHint")}</em>
        </label>
        <label className={styles.field}>
          <span>{t("SettingsPage.nodePriorityLabel")}</span>
          <input
            type="number"
            value={form.priority}
            onChange={(e) => setForm((p) => ({ ...p, priority: e.target.value }))}
          />
          <em className={styles.fieldHint}>{t("SettingsPage.nodePriorityHint")}</em>
        </label>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={saving}>
            {t("SettingsPage.cancel")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={saving}>
            {saving ? t("SettingsPage.saving") : t("SettingsPage.save")}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

/* ── 節點管理 ──────────────────────────────────────── */
function NodeList() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const [nodes, setNodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editTarget, setEditTarget] = useState(null); // node 物件
  const editPresence = useDialogPresence(editTarget);
  const [saving, setSaving] = useState(false);

  const fetchNodes = useCallback(() => {
    setLoading(true);
    ProxmoxConfigService.getNodes()
      .then(setNodes)
      .catch((err) => toast.error(err?.message ?? t("SettingsPage.toastLoadNodesFailed")))
      .finally(() => setLoading(false));
  }, [toast, t]);

  useEffect(() => {
    fetchNodes();
  }, [fetchNodes]);

  async function saveEdit(node, form) {
    setSaving(true);
    try {
      const updated = await ProxmoxConfigService.updateNode(node.id, {
        host: form.host.trim(),
        port: Number(form.port) || 8006,
        priority: Number(form.priority) || 0,
        enabled: node.enabled ?? true,
      });
      setNodes((prev) => prev.map((n) => (n.id === node.id ? updated : n)));
      toast.success(t("SettingsPage.toastNodeUpdated"));
      setEditTarget(null);
    } catch (err) {
      toast.error(err?.message ?? t("SettingsPage.toastUpdateNodeFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function toggleEnabled(node, enabled) {
    setSaving(true);
    try {
      const updated = await ProxmoxConfigService.updateNode(node.id, {
        host: node.host,
        port: node.port,
        priority: node.priority,
        enabled,
      });
      setNodes((prev) => prev.map((n) => (n.id === node.id ? updated : n)));
      toast.success(
        enabled ? t("SettingsPage.toastNodeEnabled", { name: node.name }) : t("SettingsPage.toastNodeDisabled", { name: node.name })
      );
    } catch (err) {
      toast.error(err?.message ?? t("SettingsPage.toastUpdateNodeFailed"));
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <LoadingState text={t("SettingsPage.loadingNodes")} />;
  if (nodes.length === 0) {
    return (
      <EmptyState icon="lock" title={t("SettingsPage.emptyNoNodeData")} />
    );
  }

  return (
    <div className={styles.list}>
      {nodes.map((node) => (
        /* 清單列同 PVE 連線：頭像｜名稱｜在線狀態｜啟用｜編輯 */
        <div key={node.id ?? node.name} className={`${styles.listRow} ${styles.listRowCompact}`}>
          <span className={styles.listAvatar} aria-hidden="true">
            <MIcon name="dns" size={20} />
          </span>
          <div className={styles.rowMain}>
            <span className={styles.rowName}>
              {node.name}
              {node.is_primary && <span className={`${styles.badge} ${styles.badge_info}`}>{t("SettingsPage.primaryNode")}</span>}
              {node.enabled === false && (
                <span className={`${styles.badge} ${styles.badge_danger}`}>{t("SettingsPage.disabled")}</span>
              )}
            </span>
            <span className={styles.rowMeta}>
              {node.host}:{node.port} · Priority {node.priority}
              {node.enabled === false && ` · ${t("SettingsPage.notAcceptingNewVms")}`}
            </span>
          </div>
          <div className={styles.listTags}>
            <span className={`${styles.badge} ${styles.listStatus} ${node.is_online ? styles.badge_success : styles.badge_danger}`}>
              {node.is_online ? t("SettingsPage.online") : t("SettingsPage.offline")}
            </span>
            <label className={`${styles.checkRow} ${styles.listExtra}`} title={t("SettingsPage.disableNodeHint")}>
              <input
                type="checkbox"
                checked={node.enabled !== false}
                disabled={saving || node.id == null}
                onChange={(e) => toggleEnabled(node, e.target.checked)}
              />
              <span>{t("SettingsPage.enable")}</span>
            </label>
          </div>
          <div className={styles.rowActions}>
            <button
              type="button"
              className={styles.iconBtn}
              onClick={() => setEditTarget(node)}
              disabled={node.id == null}
              aria-label={`${t("SettingsPage.edit")} ${node.name}`}
              title={t("SettingsPage.edit")}
            >
              <MIcon name="edit" size={16} />
            </button>
          </div>
        </div>
      ))}

      {editPresence.open && (
        <NodeEditDialog
          key={editPresence.item.id}
          node={editPresence.item}
          saving={saving}
          closing={editPresence.closing}
          onClose={() => setEditTarget(null)}
          onSave={saveEdit}
        />
      )}
    </div>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function NodesPage() {
  const { t } = useTranslation("system");
  return (
    <div className={styles.page}>
      <PageHeader title={t("SettingsPage.nodesTitle")} />
      <div className={styles.content}>
        <NodeList />
      </div>
    </div>
  );
}
