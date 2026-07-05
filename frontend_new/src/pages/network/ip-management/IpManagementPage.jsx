import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./IpManagementPage.module.scss";
import MIcon from "../../../components/MIcon";
import { IpManagementService } from "../../../services/ipManagement";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";

const COLUMNS = ["IP 位址", "用途", "VMID", "備註", "分配時間"];

const PURPOSE_LABELS = {
  vm: "VM",
  lxc: "LXC",
  gateway_vm: "Gateway VM",
  subnet_gateway: "閘道",
  reserved: "保留",
};

function EmptyState() {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="lan" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>尚未設定子網</h2>
      <p className={styles.emptyDesc}>
        建立子網設定後,系統將自動為虛擬機與容器分配 IP 位址
      </p>
    </div>
  );
}

function PurposeBadge({ purpose }) {
  const label = PURPOSE_LABELS[purpose] ?? purpose ?? "—";
  return (
    <span className={`${styles.badge} ${styles[`badge_${purpose ?? "unknown"}`]}`}>
      {label}
    </span>
  );
}

export default function IpManagementPage() {
  const toast = useToast();
  const [allocations, setAllocations] = useState([]);
  const [subnet, setSubnet] = useState(null);
  const [status, setStatus] = useState(null);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);

  /** silent = true 時不觸發 loading 與錯誤提示，供背景自動刷新使用 */
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [allocRes, subnetRes, statusRes] = await Promise.all([
        IpManagementService.listAllocations({ limit: 500 }),
        IpManagementService.getSubnet().catch(() => null),
        IpManagementService.getStatus().catch(() => null),
      ]);
      setAllocations(allocRes?.allocations ?? []);
      setSubnet(subnetRes ?? null);
      setStatus(statusRes ?? null);
    } catch (e) {
      if (!silent) toast.error(e?.message ?? "載入 IP 分配失敗");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(() => load(true));

  const stats = useMemo(() => {
    const total = subnet?.total_ips ?? status?.total_ips ?? 0;
    const used  = subnet?.used_ips  ?? status?.used_ips  ?? allocations.length;
    const free  = subnet?.available_ips ?? status?.available_ips ?? Math.max(0, total - used);
    return { total, used, free };
  }, [allocations, subnet, status]);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return allocations;
    return allocations.filter(
      (a) =>
        (a.ip_address ?? "").toLowerCase().includes(q) ||
        (a.description ?? "").toLowerCase().includes(q) ||
        String(a.vmid ?? "").includes(q),
    );
  }, [allocations, filter]);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>IP 管理</h1>
          <p className={styles.pageSubtitle}>管理子網設定與所有 IP 位址分配</p>
        </div>
      </div>

      <div className={styles.statRow}>
        <div className={styles.statCard}>
          <div className={styles.statIcon}>
            <MIcon name="public" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>子網總 IP</span>
            <span className={styles.statValue}>{stats.total}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconOk}`}>
            <MIcon name="check_circle" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>可用</span>
            <span className={styles.statValue}>{stats.free}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconBusy}`}>
            <MIcon name="lan" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>已分配</span>
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
            placeholder="搜尋 IP、VMID 或備註"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        {subnet && (
          <span className={styles.muted}>
            子網: <code className={styles.code}>{subnet.cidr}</code> · Bridge: <code className={styles.code}>{subnet.bridge_name}</code>
          </span>
        )}
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
                {visible.map((a) => (
                  <tr key={a.ip_address} className={styles.tr}>
                    <td className={styles.td}>
                      <div className={styles.nameCell}>
                        <div className={styles.nameIcon}>
                          <MIcon name="device_hub" size={18} />
                        </div>
                        <div>
                          <div className={styles.namePrimary}>{a.ip_address}</div>
                        </div>
                      </div>
                    </td>
                    <td className={styles.td}>
                      <PurposeBadge purpose={a.purpose} />
                    </td>
                    <td className={styles.td}>{a.vmid ?? "—"}</td>
                    <td className={styles.td}>{a.description ?? "—"}</td>
                    <td className={styles.td}>
                      {a.allocated_at
                        ? new Date(a.allocated_at).toLocaleString("zh-TW")
                        : "—"}
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
