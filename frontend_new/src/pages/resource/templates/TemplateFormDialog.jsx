import { useEffect, useState } from "react";
import styles from "./TemplatesPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useAuth } from "../../../contexts/AuthContext";
import { GroupsService } from "../../../services/groups";
import { ResourcesService } from "../../../services/resources";
import { TemplatesService } from "../../../services/templates";
import { useToast } from "../../../hooks/useToast";

/**
 * 建立（從 VM 轉換）或編輯範本的 dialog。
 * template 有值 = 編輯模式。
 */
export default function TemplateFormDialog({ template, onClose, onSaved }) {
  const toast = useToast();
  const { user } = useAuth();
  const isEdit = Boolean(template);
  const isAdmin = user?.role === "admin" || user?.is_superuser === true;

  const [sourceVmid, setSourceVmid] = useState("");
  const [name, setName] = useState(template?.name ?? "");
  const [description, setDescription] = useState(template?.description ?? "");
  const [visibility, setVisibility] = useState(template?.visibility ?? "groups");
  const [groupIds, setGroupIds] = useState(template?.group_ids ?? []);
  const [defaultCores, setDefaultCores] = useState(
    template?.default_cores ? String(template.default_cores) : "",
  );
  const [defaultMemory, setDefaultMemory] = useState(
    template?.default_memory ? String(template.default_memory) : "",
  );
  const [defaultDisk, setDefaultDisk] = useState(
    template?.default_disk ? String(template.default_disk) : "",
  );
  const [resources, setResources] = useState([]);
  const [groups, setGroups] = useState([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!isEdit) {
      (isAdmin ? ResourcesService.listAll() : ResourcesService.list())
        .then((res) => !cancelled && setResources(res ?? []))
        .catch(() => {});
    }
    GroupsService.list()
      .then((res) => !cancelled && setGroups(res?.data ?? []))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [isEdit, isAdmin]);

  const toggleGroup = (groupId) => {
    setGroupIds((prev) =>
      prev.includes(groupId) ? prev.filter((id) => id !== groupId) : [...prev, groupId],
    );
  };

  const handleSubmit = async () => {
    if (!isEdit && !sourceVmid) {
      toast.error("請選擇要轉換的來源 VM");
      return;
    }
    if (!name.trim()) {
      toast.error("請輸入範本名稱");
      return;
    }
    if (visibility === "groups" && groupIds.length === 0) {
      toast.error("群組可見模式需要至少選擇一個群組");
      return;
    }

    const numOrNull = (v) => (String(v).trim() ? Number(v) : null);
    const common = {
      name: name.trim(),
      description: description.trim() || null,
      visibility,
      group_ids: visibility === "groups" ? groupIds : [],
      default_cores: numOrNull(defaultCores),
      default_memory: numOrNull(defaultMemory),
      default_disk: numOrNull(defaultDisk),
    };

    setBusy(true);
    try {
      if (isEdit) {
        await TemplatesService.update(template.id, common);
        toast.success("範本已更新");
      } else {
        await TemplatesService.create({ ...common, source_vmid: Number(sourceVmid) });
        toast.success("已開始轉換範本，來源 VM 會先關機再轉為唯讀範本");
      }
      onSaved();
      onClose();
    } catch (e) {
      toast.error(e?.message ?? (isEdit ? "更新範本失敗" : "建立範本失敗"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={`${styles.modal} ${styles.modalWide}`} onClick={(e) => e.stopPropagation()}>
        <span className={styles.modalTitle}>
          <MIcon name="library_books" size={20} />
          {isEdit ? "編輯範本" : "把 VM 轉為範本"}
        </span>
        <p className={styles.modalDesc}>
          {isEdit
            ? "更新範本的名稱、說明、可見範圍與克隆預設規格。"
            : "選擇一台已裝好環境的母機。轉換會先關機，完成後原 VM 變成唯讀範本，無法再直接開機。"}
        </p>

        {!isEdit && (
          <div className={styles.field}>
            <label htmlFor="tpl-source">來源母機</label>
            <select
              id="tpl-source"
              value={sourceVmid}
              onChange={(e) => setSourceVmid(e.target.value)}
            >
              <option value="">選擇要轉換的 VM/LXC…</option>
              {resources
                .filter((r) => r.vmid != null && r.vmid > 0 && !r.is_placeholder)
                .map((r) => (
                  <option key={r.vmid} value={String(r.vmid)}>
                    {r.name}（VMID {r.vmid} · {r.type}）
                  </option>
                ))}
            </select>
            {resources.length === 0 && (
              <span className={styles.fieldWarn}>找不到可用的 VM，請先建立並設定好一台母機。</span>
            )}
          </div>
        )}

        <div className={styles.field}>
          <label htmlFor="tpl-name">範本名稱</label>
          <input
            id="tpl-name"
            type="text"
            maxLength={255}
            placeholder="例如 Ubuntu 22.04 + Docker 實驗環境"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        <div className={styles.field}>
          <label htmlFor="tpl-desc">說明（選填）</label>
          <textarea
            id="tpl-desc"
            rows={3}
            maxLength={1000}
            placeholder="描述這個範本裝了什麼、適合哪些課程使用"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <div className={styles.field}>
          <label className={styles.checkLine}>
            <input
              type="checkbox"
              checked={visibility === "global"}
              onChange={(e) => setVisibility(e.target.checked ? "global" : "groups")}
            />
            全域可見（所有使用者都能克隆）
          </label>
          {visibility === "groups" && (
            <div className={styles.groupBox}>
              <p className={styles.fieldHint}>勾選可以看到這個範本的群組：</p>
              {groups.length === 0 ? (
                <span className={styles.fieldWarn}>你目前沒有任何群組，請先到群組頁建立。</span>
              ) : (
                <div className={styles.groupGrid}>
                  {groups.map((group) => (
                    <label key={group.id} className={styles.checkLine}>
                      <input
                        type="checkbox"
                        checked={groupIds.includes(group.id)}
                        onChange={() => toggleGroup(group.id)}
                      />
                      <span className={styles.checkLabel}>{group.name}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className={styles.tripleGrid}>
          <div className={styles.field}>
            <label htmlFor="tpl-cores">預設 CPU 核數</label>
            <input
              id="tpl-cores"
              type="number"
              min={1}
              max={64}
              placeholder="沿用範本"
              value={defaultCores}
              onChange={(e) => setDefaultCores(e.target.value)}
            />
          </div>
          <div className={styles.field}>
            <label htmlFor="tpl-memory">預設記憶體 (MB)</label>
            <input
              id="tpl-memory"
              type="number"
              min={128}
              placeholder="沿用範本"
              value={defaultMemory}
              onChange={(e) => setDefaultMemory(e.target.value)}
            />
          </div>
          <div className={styles.field}>
            <label htmlFor="tpl-disk">預設磁碟 (GB)</label>
            <input
              id="tpl-disk"
              type="number"
              min={1}
              placeholder="沿用範本"
              value={defaultDisk}
              onChange={(e) => setDefaultDisk(e.target.value)}
            />
          </div>
        </div>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={busy}
            onClick={handleSubmit}
          >
            {busy ? "處理中…" : isEdit ? "儲存變更" : "開始轉換"}
          </button>
        </div>
      </div>
    </div>
  );
}
