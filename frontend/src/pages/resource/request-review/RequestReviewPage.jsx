import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./RequestReviewPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import { AiApiService } from "../../../services/aiApi";
import { DeletionRequestsService } from "../../../services/deletionRequests";
import { SpecChangeRequestsService } from "../../../services/specChangeRequests";
import { VmRequestsService } from "../../../services/vmRequests";

const TABS = [
  { key: "pending", label: "待審核", icon: "pending_actions" },
  { key: "approved", label: "已通過", icon: "task_alt" },
  { key: "rejected", label: "已拒絕", icon: "block" },
  { key: "all", label: "全部", icon: "view_list" },
];

const REVIEW_COLUMNS = ["申請類型", "申請內容", "申請人", "時間", "規格 / 摘要", "狀態"];

const STATUS_META = {
  pending: { label: "待審核", tone: "info" },
  approved: { label: "已通過", tone: "success" },
  rejected: { label: "已拒絕", tone: "danger" },
  cancelled: { label: "已取消", tone: "muted" },
  running: { label: "處理中", tone: "info" },
  completed: { label: "已完成", tone: "muted" },
  failed: { label: "失敗", tone: "danger" },
  deleted_approved: { label: "已通過 / 資源已刪除", tone: "success" },
};

const EMPTY_TEXT = {
  pending: "目前沒有待審核的申請",
  approved: "目前沒有已通過的申請",
  rejected: "目前沒有已拒絕的申請",
  all: "目前沒有申請紀錄",
};

function formatDateTime(value) {
  if (!value) return "未設定";
  return new Date(value).toLocaleString("zh-TW", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatRange(startAt, endAt) {
  if (!startAt && !endAt) return "未設定";
  if (!endAt) return `${formatDateTime(startAt)} 起`;
  return `${formatDateTime(startAt)} - ${formatDateTime(endAt)}`;
}

function isDeletedApprovedVm(request) {
  return (
    request?.review_comment === "Resource deleted by user" ||
    request?.review_comment === "Resource deleted (orphan DB cleanup)" ||
    request?.resource_warning === "Resource deleted by user" ||
    request?.resource_warning === "Resource deleted (orphan DB cleanup)"
  );
}

function vmSpecLabel(request) {
  if (!request) return "-";
  const disk =
    request.resource_type === "vm"
      ? `${request.disk_size ?? 0} GB Disk`
      : `${request.rootfs_size ?? 0} GB Rootfs`;
  return `${request.cores} CPU / ${(request.memory / 1024).toFixed(1)} GB RAM / ${disk}`;
}

function specChangeLabel(request) {
  const parts = [
    request.requested_cpu
      ? `CPU ${request.current_cpu ?? "-"} -> ${request.requested_cpu}`
      : "",
    request.requested_memory
      ? `RAM ${request.current_memory ?? "-"} -> ${request.requested_memory} MB`
      : "",
    request.requested_disk
      ? `Disk ${request.current_disk ?? "-"} -> ${request.requested_disk} GB`
      : "",
  ].filter(Boolean);
  return parts.join(" / ") || request.change_type || "-";
}

function sourceLabel(source) {
  if (source === "vm") return "建立申請";
  if (source === "spec") return "規格調整";
  if (source === "ai") return "AI API 金鑰";
  return "刪除請求";
}

function sourceIcon(item) {
  if (item.source === "spec") return "tune";
  if (item.source === "deletion") return "delete_outline";
  if (item.source === "ai") return "vpn_key";
  return item.raw?.resource_type === "vm" ? "computer" : "terminal";
}

const AI_DURATION_LABELS = {
  "1h": "1 小時",
  "1d": "1 天",
  "7d": "1 週",
  "30d": "1 個月",
  never: "永不過期",
};

function normalizeVmRequest(request) {
  const deletedApproved = isDeletedApprovedVm(request);
  const reviewStatus = deletedApproved
    ? "approved"
    : ["pending", "approved", "rejected"].includes(request.status)
      ? request.status
      : "other";

  return {
    id: `vm:${request.id}`,
    rawId: request.id,
    source: "vm",
    raw: request,
    reviewStatus,
    status: deletedApproved ? "deleted_approved" : request.status,
    title: request.hostname || request.name || "未命名申請",
    user: request.user_full_name || request.user_email || "未知使用者",
    userSubtext: request.user_email || request.user_id || "-",
    timeText: formatRange(request.start_at, request.end_at),
    specText: vmSpecLabel(request),
    reason: request.reason,
    paramLabel: "作業系統",
    paramText:
      request.os_info ||
      request.ostemplate ||
      request.service_template_slug ||
      (request.template_id ? `Template #${request.template_id}` : "未設定"),
    gpuText: request.gpu_mapping_id || "未申請",
    nodeText: request.assigned_node || request.desired_node || "尚未評估",
    createdAt: request.created_at,
    reviewedAt: request.reviewed_at,
  };
}

function normalizeSpecRequest(request) {
  return {
    id: `spec:${request.id}`,
    rawId: request.id,
    source: "spec",
    raw: request,
    reviewStatus: request.status,
    status: request.status,
    title: `VMID ${request.vmid} 規格調整`,
    user: request.user_full_name || request.user_email || "未知使用者",
    userSubtext: request.user_email || request.user_id || "-",
    timeText: formatDateTime(request.created_at),
    specText: specChangeLabel(request),
    reason: request.reason,
    paramLabel: "變更類型",
    paramText: request.change_type || "-",
    gpuText: "-",
    nodeText: `VMID ${request.vmid}`,
    createdAt: request.created_at,
    reviewedAt: request.reviewed_at,
  };
}

function normalizeAiRequest(request) {
  const durationText = AI_DURATION_LABELS[request.duration] ?? request.duration ?? "-";
  return {
    id: `ai:${request.id}`,
    rawId: request.id,
    source: "ai",
    raw: request,
    reviewStatus: ["pending", "approved", "rejected"].includes(request.status)
      ? request.status
      : "other",
    status: request.status,
    title: `金鑰「${request.api_key_name}」`,
    user: request.user_full_name || request.user_email || "未知使用者",
    userSubtext: request.user_email || request.user_id || "-",
    timeText: formatDateTime(request.created_at),
    specText: `期限 ${durationText}`,
    reason: request.purpose,
    paramLabel: "金鑰期限",
    paramText: durationText,
    gpuText: "-",
    nodeText: "-",
    createdAt: request.created_at,
    reviewedAt: request.reviewed_at,
  };
}

function normalizeDeletionRequest(request) {
  return {
    id: `deletion:${request.id}`,
    rawId: request.id,
    source: "deletion",
    raw: request,
    reviewStatus: "other",
    status: request.status,
    title: `${request.name || "Resource"} / VMID ${request.vmid}`,
    user: request.user_full_name || request.user_email || "未知使用者",
    userSubtext: request.user_email || request.user_id || "-",
    timeText: formatDateTime(request.created_at),
    specText: `${request.resource_type || "resource"} / ${request.node || "unknown node"}`,
    reason: request.error_message || "使用者送出刪除請求",
    paramLabel: "刪除參數",
    paramText: `purge=${request.purge ? "yes" : "no"} / force=${request.force ? "yes" : "no"}`,
    gpuText: "-",
    nodeText: request.node || "unknown node",
    createdAt: request.created_at,
    reviewedAt: request.completed_at,
  };
}

function StatusBadge({ status }) {
  const meta = STATUS_META[status] ?? { label: status, tone: "muted" };
  return (
    <span className={`${styles.badge} ${styles[`badge_${meta.tone}`]}`}>
      {meta.label}
    </span>
  );
}

function EmptyState({ tab }) {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="assignment_turned_in" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>沒有申請</h2>
      <p className={styles.emptyDesc}>{EMPTY_TEXT[tab]}</p>
    </div>
  );
}

function InfoRow({ label, value }) {
  return (
    <div className={styles.infoRow}>
      <span>{label}</span>
      <strong>{value || "-"}</strong>
    </div>
  );
}

function filterByTab(items, tab) {
  if (tab === "all") return items;
  return items.filter((item) => item.reviewStatus === tab);
}

export default function RequestReviewPage() {
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("pending");
  const [requests, setRequests] = useState([]);
  const [allRequests, setAllRequests] = useState([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [context, setContext] = useState(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState("");
  const [comment, setComment] = useState("");
  const [reviewing, setReviewing] = useState(false);

  const selected = useMemo(
    () => requests.find((request) => request.id === selectedId) ?? requests[0] ?? null,
    [requests, selectedId],
  );

  /** silent = true 時不觸發 loading 與錯誤提示，供背景自動刷新使用 */
  const fetchRequests = useCallback(async (tab = activeTab, silent = false) => {
    if (!silent) {
      setLoading(true);
      setError("");
    }
    try {
      const [vmRes, specRes, deletionRes, aiRes] = await Promise.all([
        VmRequestsService.listAll(undefined),
        SpecChangeRequestsService.listAll(),
        DeletionRequestsService.listAll(),
        // AI API 服務未啟用時仍要能審核其他類型的申請
        AiApiService.listAllRequests().catch(() => ({ data: [] })),
      ]);
      const items = [
        ...(vmRes.data ?? []).map(normalizeVmRequest),
        ...(specRes.data ?? []).map(normalizeSpecRequest),
        ...(deletionRes.data ?? []).map(normalizeDeletionRequest),
        ...(aiRes.data ?? []).map(normalizeAiRequest),
      ].sort(
        (a, b) =>
          new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime(),
      );
      const filtered = filterByTab(items, tab);
      setAllRequests(items);
      setRequests(filtered);
      setSelectedId((current) =>
        current && filtered.some((item) => item.id === current)
          ? current
          : filtered[0]?.id ?? null,
      );
    } catch (err) {
      if (!silent) {
        setRequests([]);
        setAllRequests([]);
        setSelectedId(null);
        setError(err?.message ?? "讀取申請失敗");
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }, [activeTab]);

  useEffect(() => {
    fetchRequests(activeTab);
    setComment("");
  }, [activeTab, fetchRequests]);

  useAutoRefresh(() => fetchRequests(activeTab, true));

  useEffect(() => {
    if (
      !selected?.rawId ||
      selected.source !== "vm" ||
      selected.reviewStatus !== "pending"
    ) {
      setContext(null);
      setContextError("");
      setContextLoading(false);
      return;
    }

    let cancelled = false;
    setContextLoading(true);
    setContextError("");
    setContext(null);
    VmRequestsService.getReviewContext(selected.rawId)
      .then((res) => {
        if (!cancelled) setContext(res);
      })
      .catch((err) => {
        if (!cancelled) setContextError(err?.message ?? "讀取審核資訊失敗");
      })
      .finally(() => {
        if (!cancelled) setContextLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected?.rawId, selected?.source, selected?.reviewStatus]);

  async function submitReview(status) {
    if (!selected?.rawId || reviewing || selected.reviewStatus !== "pending") return;
    setReviewing(true);
    try {
      const body = {
        status,
        review_comment: comment.trim() || null,
      };
      if (selected.source === "vm") {
        await VmRequestsService.review(selected.rawId, body);
      } else if (selected.source === "spec") {
        await SpecChangeRequestsService.review(selected.rawId, body);
      } else if (selected.source === "ai") {
        await AiApiService.reviewRequest(selected.rawId, body);
      } else {
        return;
      }
      toast.success(status === "approved" ? "申請已核准" : "申請已拒絕");
      setComment("");
      await fetchRequests(activeTab);
    } catch (err) {
      toast.error(err?.message ?? "審核失敗");
    } finally {
      setReviewing(false);
    }
  }

  const isPending = selected?.reviewStatus === "pending";
  const stats = useMemo(() => {
    const source = allRequests.length ? allRequests : requests;
    const pending = source.filter((request) => request.reviewStatus === "pending").length;
    const approved = source.filter((request) => request.reviewStatus === "approved").length;
    const rejected = source.filter((request) => request.reviewStatus === "rejected").length;
    return { total: source.length, pending, approved, rejected };
  }, [allRequests, requests]);

  const visibleRequests = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return requests;
    return requests.filter((request) => {
      const searchable = [
        sourceLabel(request.source),
        request.title,
        request.user,
        request.userSubtext,
        request.status,
        request.specText,
        request.timeText,
        request.gpuText,
      ]
        .join(" ")
        .toLowerCase();
      return searchable.includes(q);
    });
  }, [query, requests]);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>申請審核</h1>
          <p className={styles.pageSubtitle}>
            集中查看建立、規格調整、AI API 金鑰與刪除請求；刪除資源不會扣除原本已通過的審核數量
          </p>
        </div>
      </div>

      <div className={styles.statRow}>
        <div className={styles.statCard}>
          <div className={styles.statIcon}>
            <MIcon name="assignment" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>總申請</span>
            <span className={styles.statValue}>{stats.total}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconBusy}`}>
            <MIcon name="pending_actions" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>待審核</span>
            <span className={styles.statValue}>{stats.pending}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconOk}`}>
            <MIcon name="task_alt" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>已通過</span>
            <span className={styles.statValue}>{stats.approved}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconDanger}`}>
            <MIcon name="block" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>已拒絕</span>
            <span className={styles.statValue}>{stats.rejected}</span>
          </div>
        </div>
      </div>

      <div className={styles.tabs}>
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={`${styles.tab} ${activeTab === tab.key ? styles.tabActive : ""}`}
            onClick={() => setActiveTab(tab.key)}
          >
            <MIcon name={tab.icon} size={16} />
            {tab.label}
          </button>
        ))}
      </div>

      <div className={styles.toolbar}>
        <div className={styles.search}>
          <MIcon name="search" size={16} />
          <input
            type="text"
            className={styles.searchInput}
            placeholder="搜尋類型、主機、申請人、狀態或 GPU"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>

      <div className={styles.content}>
        <div className={styles.reviewGrid}>
          <section className={styles.listPane}>
            {loading ? (
              <div className={styles.stateBox}>讀取申請中...</div>
            ) : error ? (
              <div className={styles.stateBox}>
                <span>{error}</span>
                <button type="button" className={styles.btnSecondary} onClick={() => fetchRequests(activeTab)}>
                  重試
                </button>
              </div>
            ) : visibleRequests.length === 0 ? (
              <EmptyState tab={activeTab} />
            ) : (
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      {REVIEW_COLUMNS.map((column) => (
                        <th key={column} className={styles.th}>{column}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {visibleRequests.map((request) => (
                      <tr
                        key={request.id}
                        className={`${styles.tr} ${selected?.id === request.id ? styles.trActive : ""}`}
                        onClick={() => { setSelectedId(request.id); setComment(""); }}
                      >
                        <td className={styles.td}>{sourceLabel(request.source)}</td>
                        <td className={styles.td}>
                          <div className={styles.nameCell}>
                            <div className={styles.nameIcon}>
                              <MIcon name={sourceIcon(request)} size={18} />
                            </div>
                            <div>
                              <div className={styles.namePrimary}>{request.title}</div>
                              <div className={styles.nameSub}>{request.paramText}</div>
                            </div>
                          </div>
                        </td>
                        <td className={styles.td}>{request.user}</td>
                        <td className={styles.td}>{request.timeText}</td>
                        <td className={styles.td}>{request.specText}</td>
                        <td className={styles.td}><StatusBadge status={request.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className={styles.detailPane}>
            {!selected ? (
              <div className={styles.stateBox}>請選擇一筆申請</div>
            ) : (
              <>
                <div className={styles.detailHeader}>
                  <div>
                    <h2>{selected.title}</h2>
                    <p>{selected.user}</p>
                  </div>
                  <StatusBadge status={selected.status} />
                </div>

                <div className={styles.infoGrid}>
                  <InfoRow label="申請類型" value={sourceLabel(selected.source)} />
                  <InfoRow label="規格 / 摘要" value={selected.specText} />
                  <InfoRow label="時間" value={selected.timeText} />
                  <InfoRow label={selected.paramLabel} value={selected.paramText} />
                  <InfoRow label="GPU" value={selected.gpuText} />
                  <InfoRow label="節點 / VMID" value={context?.projected_node || selected.nodeText} />
                </div>

                <div className={styles.reasonBox}>
                  <span>申請原因 / 備註</span>
                  <p>{selected.reason}</p>
                </div>

                {contextLoading && <div className={styles.stateBox}>讀取資源評估中...</div>}
                {contextError && selected.source === "vm" && (
                  <div className={`${styles.stateBox} ${styles.stateError}`}>
                    {contextError}
                  </div>
                )}
                {context && selected.source === "vm" && (
                  <div className={styles.contextBox}>
                    <div className={styles.contextTitle}>
                      <MIcon name={context.feasible ? "check_circle" : "warning"} size={18} />
                      <span>{context.feasible ? "目前可分配" : "資源可能不足"}</span>
                    </div>
                    <p>{context.summary}</p>
                    {context.warnings?.length > 0 && (
                      <div className={styles.warningList}>
                        {context.warnings.map((warning) => (
                          <span key={warning}>{warning}</span>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                <label className={styles.commentField}>
                  <span>審核備註</span>
                  <textarea
                    value={comment}
                    onChange={(event) => setComment(event.target.value)}
                    disabled={!isPending || reviewing || selected.source === "deletion"}
                    placeholder="可填寫核准原因或退回說明"
                  />
                </label>

                <div className={styles.rowActions}>
                  {isPending && selected.source !== "deletion" ? (
                    <>
                      <button
                        type="button"
                        className={styles.btnApprove}
                        disabled={reviewing || (selected.source === "vm" && context && !context.feasible)}
                        onClick={() => submitReview("approved")}
                      >
                        核准
                      </button>
                      <button
                        type="button"
                        className={styles.btnReject}
                        disabled={reviewing}
                        onClick={() => submitReview("rejected")}
                      >
                        拒絕
                      </button>
                    </>
                  ) : selected.source === "deletion" ? (
                    <span className={styles.doneText}>刪除請求只作為申請紀錄，不計入審核通過數量。</span>
                  ) : (
                    <span className={styles.doneText}>這筆申請已完成審核。</span>
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
