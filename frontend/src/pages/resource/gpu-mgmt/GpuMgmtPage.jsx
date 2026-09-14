import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./GpuMgmtPage.module.scss";
import MIcon from "../../../components/MIcon";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { GpuService } from "../../../services/gpu";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import LoadingState from "../../../components/LoadingState/LoadingState";
import PageHeader from "../../../components/PageHeader/PageHeader";

function useColumns() {
  const { t } = useTranslation("resource");
  return [
    t("GpuMgmtPage.columnMapping"),
    t("GpuMgmtPage.columnDescription"),
    t("GpuMgmtPage.columnNodePci"),
    t("GpuMgmtPage.columnAvailableTotal"),
    t("GpuMgmtPage.columnVmInUse"),
    t("GpuMgmtPage.columnStatus"),
    t("GpuMgmtPage.columnActions"),
  ];
}

/* 超過此數量的 PCI 位址改以「範圍摘要 + 展開列」顯示，避免 SR-IOV 撐爆列高 */
const PCI_COLLAPSE_THRESHOLD = 3;

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
      capacity_count: m.capacity_count || m.device_count,
      used_count: m.used_count,
      available_count: m.available_count,
      total_vram_mb: m.total_vram_mb,
      used_vram_mb: m.used_vram_mb,
      used_vram_known: m.used_vram_known ?? true,
      per_instance_vram_mb: m.per_instance_vram_mb,
      mdev_profile: m.mdev_profile,
      is_sriov: m.is_sriov,
      vms: (m.used_by ?? []).map((u) => ({
        vmid: u.vmid,
        name: u.vm_name,
        status: u.status,
      })),
    };
  });
}

/* 解析 PCI 位址 domain:bus:device.function；function 佔 3 bits，
   以 device*8+function 當序數判斷 VF 是否連續（15:00.7 的下一個是 15:01.0） */
function parsePci(path) {
  const m = /^([0-9a-fA-F]{4}:[0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-9a-fA-F])$/.exec(path ?? "");
  if (!m) return null;
  return { prefix: m[1], ord: parseInt(m[2], 16) * 8 + parseInt(m[3], 16) };
}

/* 將同節點、同 domain:bus 的連續位址壓成區段；無法解析或不連續就各自成段 */
function compressEntries(mapEntries) {
  const segments = [];
  for (const entry of mapEntries) {
    const parsed = parsePci(entry.pci);
    const last = segments[segments.length - 1];
    if (
      parsed &&
      last?.endOrd != null &&
      last.node === entry.node &&
      last.prefix === parsed.prefix &&
      parsed.ord === last.endOrd + 1
    ) {
      last.end = entry.pci;
      last.endOrd = parsed.ord;
      last.count += 1;
    } else {
      segments.push({
        node: entry.node,
        start: entry.pci,
        end: entry.pci,
        prefix: parsed?.prefix,
        endOrd: parsed?.ord ?? null,
        count: 1,
      });
    }
  }
  return segments;
}

/* 區段顯示文字，迄端省略 domain：0000:15:00.2 – 15:03.7 */
function formatSegment(seg) {
  if (seg.count === 1) return seg.start;
  return `${seg.start} – ${seg.end.replace(/^[0-9a-fA-F]{4}:/, "")}`;
}

/* 依節點分組（保留原始順序），供展開列分節顯示 */
function groupByNode(mapEntries) {
  const groups = [];
  for (const entry of mapEntries) {
    const last = groups[groups.length - 1];
    if (last && last.node === entry.node) last.items.push(entry);
    else groups.push({ node: entry.node, items: [entry] });
  }
  return groups;
}

/* MB → 人類可讀（512 MB / 4 GB / 1.5 GB） */
function formatVram(mb) {
  if (!mb || mb <= 0) return "";
  if (mb < 1024) return `${mb} MB`;
  const gb = mb / 1024;
  return `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB`;
}

function EmptyState() {
  const { t } = useTranslation("resource");
  return <SharedEmptyState icon="memory" title={t("GpuMgmtPage.emptyTitle")} />;
}

function StatusBadge({ used, total }) {
  const { t } = useTranslation("resource");
  if (total === 0) {
    return <span className={`${styles.badge} ${styles.badge_unknown}`}>{t("GpuMgmtPage.statusUnknown")}</span>;
  }
  if (used === 0) {
    return <span className={`${styles.badge} ${styles.badge_available}`}>{t("GpuMgmtPage.statusAvailable")}</span>;
  }
  if (used >= total) {
    return <span className={`${styles.badge} ${styles.badge_full}`}>{t("GpuMgmtPage.statusFull")}</span>;
  }
  return (
    <span className={`${styles.badge} ${styles.badge_inuse}`}>
      {t("GpuMgmtPage.statusInUse", { used, total })}
    </span>
  );
}

function VmChips({ vms }) {
  const { t } = useTranslation("resource");
  if (!vms || vms.length === 0) {
    return <span className={styles.muted}>—</span>;
  }
  return (
    <div className={styles.vmChips}>
      {vms.map((vm) => (
        <span key={vm.vmid} className={styles.vmChip} title={t("GpuMgmtPage.vmidTitle", { vmid: vm.vmid })}>
          <MIcon name="computer" size={14} />
          <span>{vm.name || t("GpuMgmtPage.vmFallbackName", { vmid: vm.vmid })}</span>
          <span
            className={`${styles.dot} ${vm.status === "running" ? styles.dotRunning : styles.dotStopped}`}
          />
        </span>
      ))}
    </div>
  );
}

export default function GpuMgmtPage() {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const confirm = useConfirm();
  const columns = useColumns();
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [expandedIds, setExpandedIds] = useState(() => new Set());

  const toggleExpanded = (id) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  /** silent = true 時不觸發 loading 與錯誤提示，供背景自動刷新使用 */
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await GpuService.listMappings();
      setRows(flattenMappings(res?.data ?? []));
    } catch (e) {
      if (!silent) toast.error(e?.message ?? t("GpuMgmtPage.loadFailed"));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [toast, t]);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(() => load(true));

  const handleDelete = async (id) => {
    const ok = await confirm({
      title: t("GpuMgmtPage.deleteMappingTitle"),
      message: t("GpuMgmtPage.deleteMappingMessage", { id }),
      confirmText: t("GpuMgmtPage.deleteConfirm"),
      danger: true,
    });
    if (!ok) return;
    try {
      await GpuService.deleteMapping(id);
      toast.success(t("GpuMgmtPage.deletedToast"));
      load();
    } catch (e) {
      toast.error(e?.message ?? t("GpuMgmtPage.deleteFailed"));
    }
  };

  const stats = useMemo(() => {
    const total = rows.reduce((s, n) => s + (n.capacity_count ?? n.device_count ?? 0), 0);
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
      <PageHeader title={t("GpuMgmtPage.pageTitle")} subtitle={t("GpuMgmtPage.pageSubtitle")} />

      <div className={styles.statRow}>
        <div className={styles.statCard}>
          <div className={styles.statIcon}>
            <MIcon name="developer_board" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("GpuMgmtPage.statTotal")}</span>
            <span className={styles.statValue}>{stats.total}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconOk}`}>
            <MIcon name="check_circle" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("GpuMgmtPage.statAvailable")}</span>
            <span className={styles.statValue}>{stats.avail}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconBusy}`}>
            <MIcon name="monitor_heart" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("GpuMgmtPage.statInUse")}</span>
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
            placeholder={t("GpuMgmtPage.searchPlaceholder")}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
      </div>

      <div className={styles.content}>
        {loading ? (
          <LoadingState fullPage />
        ) : visible.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {columns.map((col) => (
                    <th key={col} className={styles.th}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((n) => {
                  const collapsible = n.mapEntries.length > PCI_COLLAPSE_THRESHOLD;
                  const expanded = collapsible && expandedIds.has(n.id);
                  const unitLabel = n.is_sriov ? t("GpuMgmtPage.unitVf") : t("GpuMgmtPage.unitDevice");
                  return (
                    <Fragment key={n.id}>
                      <tr className={`${styles.tr} ${expanded ? styles.trExpanded : ""}`}>
                        <td className={styles.td}>
                          <div className={styles.nameCell}>
                            <div className={styles.nameIcon}>
                              <MIcon name="memory" size={18} />
                            </div>
                            <div>
                              <div className={styles.namePrimary}>{n.mapping}</div>
                              <div className={styles.nameSub}>
                                {n.is_sriov ? t("GpuMgmtPage.typeSriov") : t("GpuMgmtPage.typePassthrough")}
                                {n.mdev_profile ? ` · ${n.mdev_profile}` : ""}
                                {n.per_instance_vram_mb > 0
                                  ? t("GpuMgmtPage.perInstanceVram", { vram: formatVram(n.per_instance_vram_mb) })
                                  : ""}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className={styles.td}>{n.description || "—"}</td>
                        <td className={styles.td}>
                          {n.mapEntries.length === 0 ? (
                            <span className={styles.muted}>—</span>
                          ) : collapsible ? (
                            <div className={styles.mapSummary}>
                              {compressEntries(n.mapEntries).map((seg) => (
                                <span key={`${seg.node}-${seg.start}`} className={styles.mapEntry}>
                                  <span className={styles.mapNode}>{seg.node}</span>
                                  <code className={styles.code}>{formatSegment(seg)}</code>
                                </span>
                              ))}
                              <button
                                type="button"
                                className={`${styles.vfChip} ${expanded ? styles.vfChipOpen : ""}`}
                                aria-expanded={expanded}
                                onClick={() => toggleExpanded(n.id)}
                              >
                                {n.mapEntries.length} {unitLabel}
                                <MIcon name="expand_more" size={14} />
                              </button>
                            </div>
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
                          <div>{n.available_count} / {n.capacity_count}</div>
                          {n.is_sriov && n.capacity_count !== n.device_count && (
                            <div className={styles.cellSub}>{n.device_count} VF</div>
                          )}
                          {n.total_vram_mb > 0 && (
                            <div className={styles.cellSub}>
                              {n.used_vram_known
                                ? t("GpuMgmtPage.vramInUse", { used: formatVram(n.used_vram_mb) || "0", total: formatVram(n.total_vram_mb) })
                                : t("GpuMgmtPage.vramUnknown", { total: formatVram(n.total_vram_mb) })}
                            </div>
                          )}
                        </td>
                        <td className={styles.td}>
                          <VmChips vms={n.vms} />
                        </td>
                        <td className={styles.td}>
                          <StatusBadge used={n.used_count} total={n.capacity_count} />
                        </td>
                        <td className={styles.td}>
                          <div className={styles.actions}>
                            <button
                              type="button"
                              className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                              title={t("GpuMgmtPage.removeMappingTitle")}
                              onClick={() => handleDelete(n.id)}
                            >
                              <MIcon name="delete" size={16} />
                            </button>
                          </div>
                        </td>
                      </tr>

                      {expanded && (
                        <tr className={styles.detailTr}>
                          <td className={styles.detailTd} colSpan={columns.length}>
                            <div className={styles.detailBody}>
                              {groupByNode(n.mapEntries).map((group) => (
                                <div key={group.node}>
                                  <div className={styles.detailNode}>
                                    {group.node} · {group.items.length} {unitLabel}
                                  </div>
                                  <div className={styles.pciGrid}>
                                    {group.items.map((entry) => (
                                      <code key={entry.key} className={styles.code}>{entry.pci}</code>
                                    ))}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
