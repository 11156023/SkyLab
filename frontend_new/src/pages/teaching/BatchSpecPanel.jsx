import { useEffect, useState } from "react";
import styles from "./TeachingPage.module.scss";
import { ResourcesService } from "../../services/resources";
import { TeachingService } from "../../services/teaching";
import { useToast } from "../../hooks/useToast";
import { VmSelectTable, useGroupVms } from "./ConfigPushPanel";

const STATUS_LABEL = {
  pending: "等待中",
  running: "調整中",
  ok: "已生效",
  needs_restart: "需重啟生效",
  quota_exceeded: "超出配額",
  error: "失敗",
};

function statusClass(status) {
  if (status === "ok") return "cell_ok";
  if (status === "needs_restart") return "cell_info";
  if (status === "quota_exceeded" || status === "error") return "cell_err";
  return "";
}

export default function BatchSpecPanel({ groupId }) {
  const toast = useToast();
  const vms = useGroupVms(groupId);
  const [selected, setSelected] = useState(new Set());
  const [cores, setCores] = useState("");
  const [memoryMb, setMemoryMb] = useState("");
  const [taskId, setTaskId] = useState(null);
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [rebooting, setRebooting] = useState(null);

  /* 任務進行中每 2 秒輪詢狀態 */
  useEffect(() => {
    if (!taskId) return undefined;
    let cancelled = false;
    let timer = null;

    const poll = async () => {
      try {
        const res = await TeachingService.getBatchSpecStatus(taskId);
        if (cancelled) return;
        setStatus(res);
        const active = res.items.some(
          (i) => i.status === "pending" || i.status === "running",
        );
        if (active) timer = setTimeout(poll, 2_000);
      } catch {
        if (!cancelled) timer = setTimeout(poll, 2_000);
      }
    };

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [taskId]);

  const toggle = (vmid) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(vmid)) next.delete(vmid);
      else next.add(vmid);
      return next;
    });
  };

  const hasChange = cores !== "" || memoryMb !== "";

  const handleSubmit = async () => {
    setBusy(true);
    try {
      const res = await TeachingService.startBatchSpec({
        vmids: [...selected],
        group_id: groupId,
        cores: cores ? Number(cores) : null,
        memory_mb: memoryMb ? Number(memoryMb) : null,
      });
      toast.success("批次調整任務已開始");
      setStatus(null);
      setTaskId(res.task_id);
    } catch (e) {
      toast.error(e?.message ?? "批次調整啟動失敗");
    } finally {
      setBusy(false);
    }
  };

  const handleReboot = async (vmid) => {
    setRebooting(vmid);
    try {
      await ResourcesService.reboot(vmid);
      toast.success(`VM ${vmid} 重啟指令已送出`);
    } catch (e) {
      toast.error(e?.message ?? "重啟失敗");
    } finally {
      setRebooting(null);
    }
  };

  return (
    <div className={styles.panelStack}>
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <h2 className={styles.cardTitle}>批次調整規格</h2>
        </div>
        <div className={styles.cardBody}>
          <div className={styles.formGrid}>
            <label className={styles.field}>
              <span>CPU 核心數（留空不變）</span>
              <input
                type="number"
                min={1}
                value={cores}
                onChange={(e) => setCores(e.target.value)}
                placeholder="4"
              />
            </label>
            <label className={styles.field}>
              <span>記憶體 MB（留空不變）</span>
              <input
                type="number"
                min={256}
                value={memoryMb}
                onChange={(e) => setMemoryMb(e.target.value)}
                placeholder="4096"
              />
            </label>
          </div>

          <VmSelectTable vms={vms} selected={selected} onToggle={toggle} />

          <button
            type="button"
            className={styles.btnPrimary}
            disabled={selected.size === 0 || !hasChange || busy}
            onClick={handleSubmit}
          >
            {busy ? "送出中…" : `調整 ${selected.size} 台 VM`}
          </button>
        </div>
      </div>

      {status && (
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <h2 className={styles.cardTitle}>調整結果</h2>
          </div>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>VMID</th>
                <th className={styles.th}>結果</th>
                <th className={styles.th}>原因</th>
                <th className={`${styles.th} ${styles.thRight}`}>操作</th>
              </tr>
            </thead>
            <tbody>
              {status.items.map((item) => (
                <tr key={item.vmid} className={styles.tr}>
                  <td className={`${styles.td} ${styles.monoCell}`}>{item.vmid}</td>
                  <td className={`${styles.td} ${styles[statusClass(item.status)] ?? ""}`}>
                    {STATUS_LABEL[item.status] ?? item.status}
                  </td>
                  <td className={`${styles.td} ${styles.mutedCell}`}>{item.error ?? "—"}</td>
                  <td className={`${styles.td} ${styles.tdRight}`}>
                    {item.status === "needs_restart" && (
                      <button
                        type="button"
                        className={styles.btnSecondary}
                        disabled={rebooting === item.vmid}
                        onClick={() => handleReboot(item.vmid)}
                      >
                        {rebooting === item.vmid ? "重啟中…" : "重啟"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
