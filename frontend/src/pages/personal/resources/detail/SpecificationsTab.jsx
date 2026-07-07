import { useEffect, useState } from "react";
import styles from "./ResourceDetailPage.module.scss";
import { useAuth } from "../../../../contexts/AuthContext";
import { ResourcesService } from "../../../../services/resources";
import { SpecChangeRequestsService } from "../../../../services/specChangeRequests";
import { useToast } from "../../../../hooks/useToast";

export default function SpecificationsTab({ vmid }) {
  const toast = useToast();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin" || user?.is_superuser || false;

  const [config, setConfig] = useState(null);
  const [cores, setCores] = useState(1);
  const [memory, setMemory] = useState(512);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  const loadConfig = async () => {
    try {
      const c = await ResourcesService.getConfig(vmid);
      setConfig(c);
      setCores(c.cpu_cores || 1);
      setMemory(c.memory_mb || 512);
    } catch {
      setError(true);
    }
  };

  useEffect(() => {
    loadConfig();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vmid]);

  const handleSubmit = async () => {
    const hasChanges = cores !== config.cpu_cores || memory !== config.memory_mb;

    if (isAdmin) {
      setBusy(true);
      try {
        await ResourcesService.updateSpecDirect(vmid, {
          cores: cores !== config.cpu_cores ? cores : undefined,
          memory: memory !== config.memory_mb ? memory : undefined,
        });
        toast.success("規格已更新");
        await loadConfig();
      } catch (e) {
        toast.error(e?.message ?? "規格更新失敗");
      } finally {
        setBusy(false);
      }
      return;
    }

    if (reason.trim().length < 10) {
      toast.error("請填寫至少 10 個字的申請原因");
      return;
    }
    if (!hasChanges) {
      toast.error("規格沒有變更");
      return;
    }

    setBusy(true);
    try {
      await SpecChangeRequestsService.create({
        vmid,
        change_type: "combined",
        reason,
        requested_cpu: cores !== config.cpu_cores ? cores : undefined,
        requested_memory: memory !== config.memory_mb ? memory : undefined,
      });
      toast.success("規格變更申請已送出");
      setReason("");
    } catch (e) {
      toast.error(e?.message ?? "申請送出失敗");
    } finally {
      setBusy(false);
    }
  };

  if (error) return <p className={styles.stateText}>無法載入資源配置</p>;
  if (!config) return <p className={styles.stateText}>載入中…</p>;

  return (
    <div className={styles.tabStack}>
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>規格調整</h2>
            <p className={styles.cardDesc}>
              {isAdmin
                ? "管理員可直接套用新規格（立即生效）"
                : "送出申請後由管理員審核，通過後才會套用"}
            </p>
          </div>
        </div>
        <div className={styles.cardBody}>
          <div className={styles.formGrid}>
            <div className={styles.field}>
              <label htmlFor="spec-cores">CPU 核心</label>
              <input
                id="spec-cores"
                type="number"
                min={1}
                max={32}
                value={cores}
                onChange={(e) => setCores(Number.parseInt(e.target.value, 10) || 1)}
              />
              <span className={styles.fieldHint}>目前：{config.cpu_cores}</span>
            </div>
            <div className={styles.field}>
              <label htmlFor="spec-memory">記憶體 (MB)</label>
              <input
                id="spec-memory"
                type="number"
                min={512}
                max={65536}
                step={512}
                value={memory}
                onChange={(e) => setMemory(Number.parseInt(e.target.value, 10) || 512)}
              />
              <span className={styles.fieldHint}>目前：{config.memory_mb} MB</span>
            </div>
          </div>

          {!isAdmin && (
            <div className={styles.field}>
              <label htmlFor="spec-reason">申請原因 *</label>
              <textarea
                id="spec-reason"
                rows={4}
                placeholder="請說明為什麼需要調整規格（課程需求、負載狀況等）"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <span className={styles.fieldHint}>至少 10 個字</span>
            </div>
          )}

          <button
            type="button"
            className={styles.btnPrimary}
            disabled={busy}
            onClick={handleSubmit}
          >
            {busy ? "處理中…" : isAdmin ? "套用變更" : "送出申請"}
          </button>
        </div>
      </div>

      {!isAdmin && (
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <h2 className={styles.cardTitle}>審核流程</h2>
          </div>
          <div className={styles.cardBody}>
            <ol className={styles.stepList}>
              <li>送出規格變更申請，附上原因說明</li>
              <li>管理員在「申請審核」頁面審核</li>
              <li>審核通過後系統自動套用新規格</li>
            </ol>
          </div>
        </div>
      )}
    </div>
  );
}
