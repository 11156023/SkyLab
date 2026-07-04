import { useCallback, useEffect, useState } from "react";
import styles from "./QuotasPage.module.scss";
import MIcon from "../../../components/MIcon";
import { QuotasService } from "../../../services/quotas";
import { GroupsService } from "../../../services/groups";
import { useToast } from "../../../hooks/useToast";

const EMPTY_FORM = {
  scope: "group", // "group" | "user"
  target: "",
  max_cpu_cores: 8,
  max_memory_mb: 16384,
  max_disk_gb: 100,
  max_instances: 5,
};

const NUMBER_FIELDS = [
  { key: "max_cpu_cores", label: "CPU cores" },
  { key: "max_memory_mb", label: "記憶體 (MB)" },
  { key: "max_disk_gb", label: "磁碟 (GB)" },
  { key: "max_instances", label: "實例數" },
];

function CreateQuotaDialog({ groups, onClose, onCreated }) {
  const toast = useToast();
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  const handleCreate = async () => {
    setSaving(true);
    try {
      await QuotasService.create({
        scope: form.scope,
        group_id: form.scope === "group" ? form.target : null,
        user_id: form.scope === "user" ? form.target : null,
        max_cpu_cores: form.max_cpu_cores,
        max_memory_mb: form.max_memory_mb,
        max_disk_gb: form.max_disk_gb,
        max_instances: form.max_instances,
      });
      toast.success("配額已建立");
      onCreated();
    } catch (e) {
      toast.error(e?.message ?? "建立失敗");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <span className={styles.modalTitle}>
          <MIcon name="data_usage" size={18} />
          新增配額
        </span>

        <div className={styles.field}>
          <label htmlFor="quota-scope">範圍</label>
          <select
            id="quota-scope"
            value={form.scope}
            onChange={(e) => setForm({ ...form, scope: e.target.value, target: "" })}
          >
            <option value="group">群組預設</option>
            <option value="user">個人覆寫</option>
          </select>
        </div>

        {form.scope === "group" ? (
          <div className={styles.field}>
            <label htmlFor="quota-group">群組</label>
            <select
              id="quota-group"
              value={form.target}
              onChange={(e) => setField("target", e.target.value)}
            >
              <option value="">選擇群組</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <div className={styles.field}>
            <label htmlFor="quota-user">使用者 ID</label>
            <input
              id="quota-user"
              value={form.target}
              placeholder="使用者 UUID"
              onChange={(e) => setField("target", e.target.value)}
            />
          </div>
        )}

        <div className={styles.formGrid}>
          {NUMBER_FIELDS.map(({ key, label }) => (
            <div key={key} className={styles.field}>
              <label htmlFor={`quota-${key}`}>{label}</label>
              <input
                id={`quota-${key}`}
                type="number"
                min={1}
                value={form[key]}
                onChange={(e) => setField(key, Number(e.target.value))}
              />
            </div>
          ))}
        </div>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnGhost} onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={!form.target || saving}
            onClick={handleCreate}
          >
            {saving ? "建立中…" : "建立"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function QuotasPage() {
  const toast = useToast();
  const [quotas, setQuotas] = useState(null);
  const [groups, setGroups] = useState([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState(null);

  const load = useCallback(async () => {
    try {
      setQuotas(await QuotasService.list());
    } catch (e) {
      toast.error(e?.message ?? "載入配額失敗");
      setQuotas((prev) => prev ?? []);
    }
  }, [toast]);

  useEffect(() => {
    load();
    GroupsService.list()
      .then((res) => setGroups(res?.data ?? []))
      .catch(() => setGroups([]));
  }, [load]);

  const handleDelete = async (quota) => {
    const target = quota.group_name ?? quota.user_email ?? quota.id;
    if (!window.confirm(`確定要刪除「${target}」的配額？刪除後將套用內建預設。`)) return;
    setDeleting(quota.id);
    try {
      await QuotasService.remove(quota.id);
      toast.success("已刪除");
      load();
    } catch (e) {
      toast.error(e?.message ?? "刪除失敗");
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>配額管理</h1>
          <p className={styles.pageSubtitle}>群組預設與個人覆寫的資源上限</p>
        </div>
        <button type="button" className={styles.btnPrimary} onClick={() => setDialogOpen(true)}>
          <MIcon name="add" size={16} />
          新增配額
        </button>
      </div>

      <div className={styles.card}>
        {quotas === null ? (
          <p className={styles.stateText}>載入中…</p>
        ) : quotas.length === 0 ? (
          <div className={styles.empty}>
            <MIcon name="data_usage" size={32} />
            <p>尚未設定任何配額（未設定者套用內建預設：8 cores / 16 GB / 100 GB / 5 台）</p>
          </div>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>範圍</th>
                <th>對象</th>
                <th>CPU</th>
                <th>記憶體 (MB)</th>
                <th>磁碟 (GB)</th>
                <th>台數</th>
                <th className={styles.thRight}>操作</th>
              </tr>
            </thead>
            <tbody>
              {quotas.map((q) => (
                <tr key={q.id}>
                  <td>
                    <span
                      className={`${styles.badge} ${q.scope === "group" ? styles.badge_group : styles.badge_user}`}
                    >
                      {q.scope === "group" ? "群組" : "個人覆寫"}
                    </span>
                  </td>
                  <td>{q.group_name ?? q.user_email ?? "—"}</td>
                  <td>{q.max_cpu_cores}</td>
                  <td>{q.max_memory_mb}</td>
                  <td>{q.max_disk_gb}</td>
                  <td>{q.max_instances}</td>
                  <td className={styles.tdRight}>
                    <button
                      type="button"
                      className={styles.btnDanger}
                      disabled={deleting === q.id}
                      onClick={() => handleDelete(q)}
                      title="刪除配額"
                    >
                      <MIcon name="delete" size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {dialogOpen && (
        <CreateQuotaDialog
          groups={groups}
          onClose={() => setDialogOpen(false)}
          onCreated={() => {
            setDialogOpen(false);
            load();
          }}
        />
      )}
    </div>
  );
}
