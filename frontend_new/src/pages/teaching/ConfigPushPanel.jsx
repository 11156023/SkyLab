import { useEffect, useRef, useState } from "react";
import styles from "./TeachingPage.module.scss";
import { TeachingService } from "../../services/teaching";
import { useToast } from "../../hooks/useToast";

const STATUS_LABEL = {
  pending: "等待中",
  running: "分發中",
  ok: "成功",
  error: "失敗",
};

function statusClass(status) {
  if (status === "ok") return "cell_ok";
  if (status === "error") return "cell_err";
  return "";
}

/** 群組 VM 勾選表格（配置分發與批次規格共用） */
export function VmSelectTable({ vms, selected, onToggle }) {
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th className={`${styles.th} ${styles.thCheck}`} />
          <th className={styles.th}>VMID</th>
          <th className={styles.th}>名稱</th>
          <th className={styles.th}>擁有者</th>
          <th className={styles.th}>狀態</th>
        </tr>
      </thead>
      <tbody>
        {vms.map((vm) => (
          <tr key={vm.vmid} className={styles.tr}>
            <td className={styles.td}>
              <input
                type="checkbox"
                className={styles.check}
                checked={selected.has(vm.vmid)}
                onChange={() => onToggle(vm.vmid)}
              />
            </td>
            <td className={`${styles.td} ${styles.monoCell}`}>{vm.vmid}</td>
            <td className={styles.td}>{vm.name ?? "—"}</td>
            <td className={`${styles.td} ${styles.mutedCell}`}>{vm.owner_name ?? "—"}</td>
            <td className={`${styles.td} ${styles.mutedCell}`}>{vm.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** 以熱圖端點取得群組 VM 清單（與舊版一致） */
export function useGroupVms(groupId) {
  const [vms, setVms] = useState([]);

  useEffect(() => {
    let cancelled = false;
    TeachingService.getHeatmap(groupId)
      .then((data) => !cancelled && setVms(data ?? []))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [groupId]);

  return vms;
}

export default function ConfigPushPanel({ groupId }) {
  const toast = useToast();
  const vms = useGroupVms(groupId);
  const [selected, setSelected] = useState(new Set());
  const [targetPath, setTargetPath] = useState("");
  const [taskId, setTaskId] = useState(null);
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);

  /* 任務進行中每 2 秒輪詢狀態 */
  useEffect(() => {
    if (!taskId) return undefined;
    let cancelled = false;
    let timer = null;

    const poll = async () => {
      try {
        const res = await TeachingService.getConfigPushStatus(taskId);
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

  const handlePush = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      toast.error("請先選擇檔案");
      return;
    }
    setBusy(true);
    try {
      const res = await TeachingService.startConfigPush({
        file,
        targetPath,
        vmids: [...selected],
      });
      toast.success("分發任務已開始");
      setStatus(null);
      setTaskId(res.task_id);
    } catch (e) {
      toast.error(e?.message ?? "分發啟動失敗（檔案上限 1 MB，路徑必須為絕對路徑）");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.panelStack}>
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <h2 className={styles.cardTitle}>配置文件分發</h2>
        </div>
        <div className={styles.cardBody}>
          <div className={styles.formGrid}>
            <label className={styles.field}>
              <span>配置檔案（上限 1 MB）</span>
              <input type="file" ref={fileRef} />
            </label>
            <label className={styles.field}>
              <span>目標絕對路徑</span>
              <input
                type="text"
                value={targetPath}
                onChange={(e) => setTargetPath(e.target.value)}
                placeholder="/etc/nginx/nginx.conf"
              />
            </label>
          </div>

          <VmSelectTable vms={vms} selected={selected} onToggle={toggle} />

          <button
            type="button"
            className={styles.btnPrimary}
            disabled={selected.size === 0 || !targetPath.startsWith("/") || busy}
            onClick={handlePush}
          >
            {busy ? "送出中…" : `分發到 ${selected.size} 台 VM`}
          </button>
        </div>
      </div>

      {status && (
        <div className={styles.card}>
          <div className={styles.cardHeader}>
            <h2 className={styles.cardTitle}>分發結果</h2>
          </div>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>VMID</th>
                <th className={styles.th}>結果</th>
                <th className={styles.th}>原因</th>
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
