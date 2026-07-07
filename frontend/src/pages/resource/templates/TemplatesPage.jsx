import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./TemplatesPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useAuth } from "../../../contexts/AuthContext";
import { TemplatesService } from "../../../services/templates";
import { useToast } from "../../../hooks/useToast";
import { TemplateStatusBadge } from "./TemplateBadges";
import TemplateCloneDialog from "./TemplateCloneDialog";
import TemplateFormDialog from "./TemplateFormDialog";
import TemplateTasksCard from "./TemplateTasksCard";

function visibilityLabel(template) {
  return template.visibility === "global"
    ? "全域"
    : `${template.group_ids?.length ?? 0} 個群組`;
}

/** 單列的「⋯」操作選單 */
function RowMenu({ template, cycleBusy, onClone, onEdit, onCycle, onDelete, onClose, anchorRef }) {
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      if (!ref.current?.contains(e.target) && !anchorRef?.current?.contains(e.target)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose, anchorRef]);

  return (
    <div ref={ref} className={styles.rowMenu}>
      <button
        type="button"
        className={styles.rowMenuItem}
        disabled={template.status !== "ready"}
        onClick={() => { onClose(); onClone(template); }}
      >
        <MIcon name="content_copy" size={15} />
        克隆開通
      </button>
      <button
        type="button"
        className={styles.rowMenuItem}
        onClick={() => { onClose(); onEdit(template); }}
      >
        <MIcon name="edit" size={15} />
        編輯 / 可見範圍
      </button>
      <div className={styles.rowMenuDivider} />
      {template.status === "ready" && (
        <button
          type="button"
          className={styles.rowMenuItem}
          disabled={cycleBusy}
          onClick={() => { onClose(); onCycle(template.id, "start"); }}
        >
          <MIcon name="sync" size={15} />
          開始更新循環
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
            完成更新（轉為新版）
          </button>
          <button
            type="button"
            className={styles.rowMenuItem}
            disabled={cycleBusy}
            onClick={() => { onClose(); onCycle(template.id, "cancel"); }}
          >
            取消更新循環
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
        刪除範本
      </button>
    </div>
  );
}

function ManagementRow({ template, cycleBusy, onClone, onEdit, onCycle, onDelete }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuBtnRef = useRef(null);

  return (
    <tr className={styles.tr}>
      <td className={styles.td}>
        <div className={styles.nameCell}>
          <span className={styles.namePrimary}>{template.name}</span>
          {template.pve_exists === false && (
            <span className={styles.pveMissing} title="PVE 端找不到這個範本，可能已被手動刪除">
              <MIcon name="warning" size={13} />
              PVE 不存在
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
      <td className={`${styles.td} ${styles.mutedCell}`}>{visibilityLabel(template)}</td>
      <td className={`${styles.td} ${styles.mutedCell}`}>v{template.version}</td>
      <td className={`${styles.td} ${styles.tdMenu}`}>
        <div className={styles.menuWrap}>
          {menuOpen && (
            <RowMenu
              template={template}
              cycleBusy={cycleBusy}
              onClone={onClone}
              onEdit={onEdit}
              onCycle={onCycle}
              onDelete={onDelete}
              onClose={() => setMenuOpen(false)}
              anchorRef={menuBtnRef}
            />
          )}
          <button
            ref={menuBtnRef}
            type="button"
            className={styles.menuBtn}
            onClick={() => setMenuOpen((v) => !v)}
            title="更多操作"
          >
            <MIcon name="more_horiz" size={18} />
          </button>
        </div>
      </td>
    </tr>
  );
}

function StudentCatalog({ templates, onClone }) {
  if (templates.length === 0) {
    return (
      <div className={styles.card}>
        <p className={styles.stateText}>目前沒有可用的範本。老師發布範本後，就會出現在這裡。</p>
      </div>
    );
  }

  return (
    <div className={styles.catalogGrid}>
      {templates.map((template) => (
        <div key={template.id} className={styles.catalogCard}>
          <div className={styles.catalogHead}>
            <MIcon name="library_books" size={18} />
            <span className={styles.catalogName}>{template.name}</span>
          </div>
          {template.description && (
            <p className={styles.catalogDesc}>{template.description}</p>
          )}
          <div className={styles.catalogChips}>
            <span className={styles.typeChip}>{template.resource_type}</span>
            {template.default_cores && (
              <span className={styles.typeChip}>{template.default_cores} 核</span>
            )}
            {template.default_memory && (
              <span className={styles.typeChip}>
                {Math.round(template.default_memory / 1024)} GB RAM
              </span>
            )}
            {template.default_disk && (
              <span className={styles.typeChip}>{template.default_disk} GB 磁碟</span>
            )}
            <span className={styles.typeChip}>v{template.version}</span>
          </div>
          <button
            type="button"
            className={`${styles.btnPrimary} ${styles.catalogBtn}`}
            onClick={() => onClone(template)}
          >
            <MIcon name="content_copy" size={14} />
            一鍵克隆
          </button>
        </div>
      ))}
    </div>
  );
}

export default function TemplatesPage() {
  const toast = useToast();
  const { user } = useAuth();
  const canManage =
    user?.role === "admin" || user?.role === "teacher" || user?.is_superuser === true;

  const [templates, setTemplates] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState(null);
  const [cloneTarget, setCloneTarget] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [cycleBusy, setCycleBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [tasksKey, setTasksKey] = useState(0);
  const timerRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const res = await TemplatesService.list();
      setTemplates(res?.data ?? []);
      return res?.data ?? [];
    } catch (e) {
      toast.error(e?.message ?? "載入範本失敗");
      setTemplates((prev) => prev ?? []);
      return [];
    }
  }, [toast]);

  /** 有 creating/updating 中的範本時 4 秒輪詢，否則 30 秒 */
  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      const list = await load();
      if (cancelled) return;
      const active = list.some((t) => t.status === "creating" || t.status === "updating");
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
    setTasksKey((k) => k + 1);
    setRefreshing(false);
  };

  const handleCycle = async (templateId, action) => {
    setCycleBusy(true);
    try {
      if (action === "start") await TemplatesService.startUpdateCycle(templateId);
      else if (action === "finish") await TemplatesService.finishUpdateCycle(templateId);
      else await TemplatesService.cancelUpdateCycle(templateId);
      toast.success(
        action === "start"
          ? "已開始更新循環：系統正在複製一台暫存母機，完成後會出現在你的資源列表，修改完再回到此頁按「完成更新」"
          : action === "finish"
            ? "正在把暫存母機轉為新版範本"
            : "已取消更新循環，暫存母機將被回收",
      );
      await load();
      setTasksKey((k) => k + 1);
    } catch (e) {
      toast.error(e?.message ?? "操作失敗");
    } finally {
      setCycleBusy(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await TemplatesService.remove(deleteTarget.id);
      toast.success("刪除任務已送出");
      setDeleteTarget(null);
      await load();
      setTasksKey((k) => k + 1);
    } catch (e) {
      toast.error(e?.message ?? "刪除範本失敗");
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  const list = templates ?? [];
  const readyTemplates = list.filter((t) => t.status === "ready");

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>模板管理</h1>
          <p className={styles.pageSubtitle}>
            {canManage
              ? "把設定好的母機轉為範本，學生即可一鍵克隆出自己的環境"
              : "從老師提供的範本一鍵克隆出自己的環境，開好即用"}
          </p>
        </div>
        <div className={styles.pageActions}>
          <button
            type="button"
            className={styles.btnSecondary}
            onClick={refresh}
            disabled={refreshing}
          >
            <MIcon name="sync" size={16} />
            {refreshing ? "載入中…" : "重新整理"}
          </button>
          {canManage && (
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={() => setCreateOpen(true)}
            >
              <MIcon name="add" size={16} />
              從 VM 建立範本
            </button>
          )}
        </div>
      </div>

      {templates === null ? (
        <div className={styles.card}>
          <p className={styles.stateText}>載入範本中…</p>
        </div>
      ) : canManage ? (
        list.length === 0 ? (
          <div className={styles.card}>
            <p className={styles.stateText}>
              還沒有任何範本。先準備好一台母機（裝好系統與課程環境），再點右上角「從 VM 建立範本」。
            </p>
          </div>
        ) : (
          <div className={styles.card}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.th}>名稱</th>
                  <th className={styles.th}>VMID</th>
                  <th className={styles.th}>類型</th>
                  <th className={styles.th}>狀態</th>
                  <th className={styles.th}>可見範圍</th>
                  <th className={styles.th}>版本</th>
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
                    onCycle={handleCycle}
                    onDelete={setDeleteTarget}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : (
        <StudentCatalog templates={readyTemplates} onClone={setCloneTarget} />
      )}

      <TemplateTasksCard key={tasksKey} />

      {createOpen && (
        <TemplateFormDialog
          onClose={() => setCreateOpen(false)}
          onSaved={() => {
            load();
            setTasksKey((k) => k + 1);
          }}
        />
      )}
      {editTarget && (
        <TemplateFormDialog
          template={editTarget}
          onClose={() => setEditTarget(null)}
          onSaved={() => load()}
        />
      )}
      {cloneTarget && (
        <TemplateCloneDialog
          template={cloneTarget}
          canBatch={canManage}
          onClose={() => setCloneTarget(null)}
          onCloned={() => setTasksKey((k) => k + 1)}
        />
      )}

      {deleteTarget && (
        <div className={styles.modalOverlay} onClick={() => setDeleteTarget(null)}>
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <span className={styles.modalTitle}>刪除範本「{deleteTarget.name}」？</span>
            <p className={styles.modalDesc}>
              PVE 端的範本磁碟會一併刪除，動作無法復原。如果還有從此範本克隆出的機器（linked
              clone），系統會拒絕刪除。
            </p>
            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={() => setDeleteTarget(null)}
              >
                取消
              </button>
              <button
                type="button"
                className={styles.btnDanger}
                disabled={deleting}
                onClick={handleDelete}
              >
                {deleting ? "刪除中…" : "確認刪除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
