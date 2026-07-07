import { useState } from "react";
import styles from "./TemplatesPage.module.scss";
import MIcon from "../../../components/MIcon";
import { TemplatesService } from "../../../services/templates";
import { useToast } from "../../../hooks/useToast";

/** 從範本克隆開通（teacher/admin 可批量，student 固定單台） */
export default function TemplateCloneDialog({ template, canBatch, onClose, onCloned }) {
  const toast = useToast();
  const [hostname, setHostname] = useState("");
  const [count, setCount] = useState("1");
  const [cores, setCores] = useState(
    template?.default_cores ? String(template.default_cores) : "",
  );
  const [memory, setMemory] = useState(
    template?.default_memory ? String(template.default_memory) : "",
  );
  const [disk, setDisk] = useState(
    template?.default_disk ? String(template.default_disk) : "",
  );
  const [start, setStart] = useState(true);
  const [busy, setBusy] = useState(false);

  const handleSubmit = async () => {
    const numOrNull = (v) => (String(v).trim() ? Number(v) : null);
    setBusy(true);
    try {
      const res = await TemplatesService.clone(template.id, {
        hostname: hostname.trim() || null,
        count: canBatch ? Math.max(1, Number(count) || 1) : 1,
        cores: numOrNull(cores),
        memory: numOrNull(memory),
        disk: numOrNull(disk),
        start,
      });
      toast.success(
        (res?.tasks?.length ?? 0) > 1
          ? `已送出 ${res.tasks.length} 台克隆任務，可在下方任務清單追蹤進度`
          : "克隆任務已送出，完成後會出現在你的資源列表",
      );
      onCloned();
      onClose();
    } catch (e) {
      toast.error(e?.message ?? "克隆失敗");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <span className={styles.modalTitle}>
          <MIcon name="content_copy" size={20} />
          克隆「{template.name}」
        </span>
        <p className={styles.modalDesc}>
          系統會以 linked clone 快速複製（必要時自動改用完整複製），並自動配置 IP
          與防火牆。完成後可在資源頁操作。
        </p>

        <div className={styles.cloneGrid}>
          <div className={styles.field}>
            <label htmlFor="clone-hostname">主機名稱（選填）</label>
            <input
              id="clone-hostname"
              type="text"
              maxLength={63}
              placeholder="預設使用範本名稱"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
            />
          </div>
          {canBatch && (
            <div className={styles.field}>
              <label htmlFor="clone-count">數量</label>
              <input
                id="clone-count"
                type="number"
                min={1}
                max={50}
                value={count}
                onChange={(e) => setCount(e.target.value)}
              />
            </div>
          )}
        </div>

        <div className={styles.tripleGrid}>
          <div className={styles.field}>
            <label htmlFor="clone-cores">CPU 核數</label>
            <input
              id="clone-cores"
              type="number"
              min={1}
              max={64}
              placeholder="沿用範本"
              value={cores}
              onChange={(e) => setCores(e.target.value)}
            />
          </div>
          <div className={styles.field}>
            <label htmlFor="clone-memory">記憶體 (MB)</label>
            <input
              id="clone-memory"
              type="number"
              min={128}
              placeholder="沿用範本"
              value={memory}
              onChange={(e) => setMemory(e.target.value)}
            />
          </div>
          <div className={styles.field}>
            <label htmlFor="clone-disk">磁碟 (GB)</label>
            <input
              id="clone-disk"
              type="number"
              min={1}
              placeholder="沿用範本"
              value={disk}
              onChange={(e) => setDisk(e.target.value)}
              disabled={template.resource_type === "lxc"}
            />
          </div>
        </div>

        <label className={styles.checkLine}>
          <input
            type="checkbox"
            checked={start}
            onChange={(e) => setStart(e.target.checked)}
          />
          克隆完成後自動開機
        </label>

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
            <MIcon name="content_copy" size={14} />
            {busy ? "送出中…" : "開始克隆"}
          </button>
        </div>
      </div>
    </div>
  );
}
