import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./GpuMgmtPage.module.scss";
import MIcon from "../../../components/MIcon";
import { GpuService } from "../../../services/gpu";
import { useToast } from "../../../hooks/useToast";

const COLUMNS = ["Mapping", "描述", "節點 / PCI", "可用 / 總數", "使用中 VM", "狀態", "動作"];

/* 將 backend GPUMappingDetail 攤平為前端列 */
function flattenMappings(mappings) {
  return mappings.map((m) => {
    const mapEntries = (m.maps ?? []).map((entry, index) => ({
      key: `${entry.node ?? "node"}-${entry.path ?? "path"}-${entry.id ?? index}`,
      node: entry.node ?? "—",
      pci: entry.path ?? "—",
    }));
    const nodes = [
      ...new Set(mapEntries.map((entry) => entry.node).filter((node) => node !== "—")),
    ];
    const pciPaths = mapEntries.map((entry) => entry.pci).filter((path) => path !== "—");
    return {
      id: m.id,
      mapping: m.id,
      description: m.description,
      node: nodes.join(", ") || "—",
      pci: pciPaths.join(", ") || "—",
      mapEntries,
      device_count: m.device_count,
      used_count: m.used_count,
      available_count: m.available_count,
      total_vram_mb: m.total_vram_mb,
      used_vram_mb: m.used_vram_mb,
      is_sriov: m.is_sriov,
      vms: (m.used_by ?? []).map((u) => ({
        vmid: u.vmid,
        name: u.vm_name,
        status: u.status,
      })),
    };
  });
}

function EmptyState() {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="memory" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>尚未偵測到 GPU</h2>
      <p className={styles.emptyDesc}>
        在 Proxmox 節點上完成 PCI Passthrough 設定後，GPU 將會出現在這裡
      </p>
    </div>
  );
}

function StatusBadge({ used, total }) {
  if (total === 0) {
    return <span className={`${styles.badge} ${styles.badge_unknown}`}>未知</span>;
  }
  if (used === 0) {
    return <span className={`${styles.badge} ${styles.badge_available}`}>可用</span>;
  }
  if (used >= total) {
    return <span className={`${styles.badge} ${styles.badge_full}`}>已滿載</span>;
  }
  return (
    <span className={`${styles.badge} ${styles.badge_inuse}`}>
      {used}/{total} 使用中
    </span>
  );
}

function VmChips({ vms }) {
  if (!vms || vms.length === 0) {
    return <span className={styles.muted}>—</span>;
  }
  return (
    <div className={styles.vmChips}>
      {vms.map((vm) => (
        <span key={vm.vmid} className={styles.vmChip} title={`VMID ${vm.vmid}`}>
          <MIcon name="computer" size={14} />
          <span>{vm.name || `VM ${vm.vmid}`}</span>
          <span
            className={`${styles.dot} ${vm.status === "running" ? styles.dotRunning : styles.dotStopped}`}
          />
        </span>
      ))}
    </div>
  );
}

export default function GpuMgmtPage() {
  const toast = useToast();
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await GpuService.listMappings();
      setRows(flattenMappings(res?.data ?? []));
    } catch (e) {
      toast.error(e?.message ?? "載入 GPU mappings 失敗");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const handleDelete = async (id) => {
    if (!window.confirm(`確定要刪除 mapping "${id}"?`)) return;
    try {
      await GpuService.deleteMapping(id);
      toast.success("已刪除");
      load();
    } catch (e) {
      toast.error(e?.message ?? "刪除失敗");
    }
  };

  const stats = useMemo(() => {
    const total = rows.reduce((s, n) => s + (n.device_count ?? 0), 0);
    const used = rows.reduce((s, n) => s + (n.used_count ?? 0), 0);
    const avail = Math.max(0, total - used);
    return { total, used, avail };
  }, [rows]);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (n) =>
        (n.mapping ?? "").toLowerCase().includes(q) ||
        (n.description ?? "").toLowerCase().includes(q) ||
        (n.node ?? "").toLowerCase().includes(q) ||
        (n.pci ?? "").toLowerCase().includes(q) ||
        n.mapEntries.some(
          (entry) =>
            entry.node.toLowerCase().includes(q) ||
            entry.pci.toLowerCase().includes(q),
        ),
    );
  }, [rows, filter]);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>GPU 管理</h1>
          <p className={styles.pageSubtitle}>查看叢集中所有 PCI Passthrough GPU 的指派狀態</p>
        </div>
        <div className={styles.pageActions}>
          <button type="button" className={styles.btnSecondary} onClick={load} disabled={loading}>
            <MIcon name="sync" size={16} />
            {loading ? "載入中…" : "重新整理"}
          </button>
        </div>
      </div>

      <div className={styles.statRow}>
        <div className={styles.statCard}>
          <div className={styles.statIcon}>
            <MIcon name="developer_board" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>GPU 總數</span>
            <span className={styles.statValue}>{stats.total}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconOk}`}>
            <MIcon name="check_circle" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>可用</span>
            <span className={styles.statValue}>{stats.avail}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconBusy}`}>
            <MIcon name="monitor_heart" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>使用中</span>
            <span className={styles.statValue}>{stats.used}</span>
          </div>
        </div>
      </div>

      <div className={styles.toolbar}>
        <div className={styles.search}>
          <MIcon name="search" size={16} />
          <input
            type="text"
            className={styles.searchInput}
            placeholder="搜尋節點、型號或 PCI 位址"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
      </div>

      <div className={styles.content}>
        {visible.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {COLUMNS.map((col) => (
                    <th key={col} className={styles.th}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((n) => (
                  <tr key={n.id} className={styles.tr}>
                    <td className={styles.td}>
                      <div className={styles.nameCell}>
                        <div className={styles.nameIcon}>
                          <MIcon name="memory" size={18} />
                        </div>
                        <div>
                          <div className={styles.namePrimary}>{n.mapping}</div>
                          <div className={styles.nameSub}>
                            {n.is_sriov ? "SR-IOV" : "Passthrough"}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className={styles.td}>{n.description || "—"}</td>
                    <td className={styles.td}>
                      {n.mapEntries.length === 0 ? (
                        <span className={styles.muted}>—</span>
                      ) : (
                        <div className={styles.mapList}>
                          {n.mapEntries.map((entry) => (
                            <div key={entry.key} className={styles.mapEntry}>
                              <span className={styles.mapNode}>{entry.node}</span>
                              <code className={styles.code}>{entry.pci}</code>
                            </div>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className={styles.td}>
                      {n.available_count} / {n.device_count}
                    </td>
                    <td className={styles.td}>
                      <VmChips vms={n.vms} />
                    </td>
                    <td className={styles.td}>
                      <StatusBadge used={n.used_count} total={n.device_count} />
                    </td>
                    <td className={styles.td}>
                      <div className={styles.actions}>
                        <button
                          type="button"
                          className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                          title="移除映射"
                          onClick={() => handleDelete(n.id)}
                        >
                          <MIcon name="delete" size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
