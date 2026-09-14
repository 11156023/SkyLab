import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./BatchReviewPage.module.scss";
import MIcon from "../../../components/MIcon";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { BatchProvisionService } from "../../../services/batchProvision";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import LoadingState from "../../../components/LoadingState/LoadingState";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { formatShortDateTime } from "../../../utils/formatDate";

/* 這些 hook 的回傳值會進 useCallback / useMemo 的相依陣列，
   必須 useMemo 固定身分，否則載入 effect 會無限重跑 */
function useTabs() {
  const { t } = useTranslation("resource");
  return useMemo(() => [
    { key: "pending", label: t("BatchReviewPage.tabPending") },
    { key: "approved", label: t("BatchReviewPage.tabApproved") },
    { key: "rejected", label: t("BatchReviewPage.tabRejected") },
    { key: "all", label: t("BatchReviewPage.tabAll") },
  ], [t]);
}

function useStatusMeta() {
  const { t } = useTranslation("resource");
  return useMemo(() => ({
    pending_review: { label: t("BatchReviewPage.statusPendingReview"), tone: "info" },
    approved:       { label: t("BatchReviewPage.statusApproved"), tone: "success" },
    rejected:       { label: t("BatchReviewPage.statusRejected"), tone: "danger" },
    cancelled:      { label: t("BatchReviewPage.statusCancelled"), tone: "muted" },
    pending:        { label: t("BatchReviewPage.statusPending"), tone: "info" },
    running:        { label: t("BatchReviewPage.statusRunning"), tone: "info" },
    completed:      { label: t("BatchReviewPage.statusCompleted"), tone: "success" },
    failed:         { label: t("BatchReviewPage.statusFailed"), tone: "danger" },
  }), [t]);
}

/* 批次任務狀態 → 審核分頁；cancelled 只出現在「全部」 */
const REVIEW_STATUS_BY_STATUS = {
  pending_review: "pending",
  approved: "approved",
  pending: "approved",
  running: "approved",
  completed: "approved",
  failed: "approved",
  rejected: "rejected",
  cancelled: "other",
};

function formatDateTime(value, t) {
  return formatShortDateTime(value, t("BatchReviewPage.notSet"));
}

function specLabel(spec, resourceType, t) {
  if (!spec) return "-";
  const isLxc = resourceType === "lxc";
  const diskSize = isLxc ? spec.rootfs_size : spec.disk_size;
  const disk = isLxc
    ? t("BatchReviewPage.rootfsLabel", { size: diskSize ?? 0 })
    : t("BatchReviewPage.diskLabel", { size: diskSize ?? 0 });
  if (!spec.cores && !spec.memory) return disk;
  return t("BatchReviewPage.specSummary", {
    cores: spec.cores ?? "-",
    memory: spec.memory ? (spec.memory / 1024).toFixed(1) : "-",
    disk,
  });
}

function osLabel(spec, t) {
  if (!spec) return t("BatchReviewPage.notSet");
  return (
    spec.os_info ||
    spec.ostemplate ||
    (spec.template_id ? `Template #${spec.template_id}` : null) ||
    spec.vm_template_id ||
    t("BatchReviewPage.notSet")
  );
}

function uniqueJoin(values) {
  return [...new Set(values.filter(Boolean))].join(" ・ ");
}

function toRow(jobs, t) {
  const first = jobs[0];
  const isClassGroup = Boolean(first.teaching_class_id);
  const resourceTypes = [...new Set(jobs.map((job) => job.resource_type).filter(Boolean))];

  return {
    id: isClassGroup ? `class-${first.teaching_class_id}-${first.status}` : `job-${first.id}`,
    jobs,
    classId: isClassGroup ? first.teaching_class_id : null,
    className: first.teaching_class_name ?? null,
    status: first.status,
    reviewStatus: REVIEW_STATUS_BY_STATUS[first.status] ?? "other",
    title:
      isClassGroup && jobs.length > 1
        ? t("BatchReviewPage.classGroupLabel", {
            className: first.teaching_class_name ?? t("BatchReviewPage.defaultClassName"),
            count: jobs.length,
          })
        : first.hostname_prefix,
    applicant:
      first.initiated_by_name || first.initiated_by_email || t("BatchReviewPage.unknownUser"),
    applicantSubtext: first.initiated_by_email || "-",
    resourceTypeText: resourceTypes.map((type) => type.toUpperCase()).join(" / ") || "-",
    /* 班級批次的每個機器節點規格可能不同，逐一列出才不會只看到第一台 */
    specText: uniqueJoin(jobs.map((job) => specLabel(job.spec, job.resource_type, t))),
    osText: uniqueJoin(jobs.map((job) => osLabel(job.spec, t))),
    total: jobs.reduce((sum, job) => sum + (job.total ?? 0), 0),
    done: jobs.reduce((sum, job) => sum + (job.done ?? 0), 0),
    failed: jobs.reduce((sum, job) => sum + (job.failed_count ?? 0), 0),
    tasks: jobs.flatMap((job) => job.tasks ?? []),
    recurrenceRule: first.recurrence_rule,
    previewJobId: first.id,
    createdAt: jobs.map((job) => job.created_at).sort()[0],
    reviewedAt: first.reviewed_at,
    reviewer: first.reviewer_email,
    reviewComment: first.review_comment,
  };
}

/** 同一個班級、同一個狀態的機器節點併成一列審核（本來就是一起核准的） */
function buildRows(jobs, t) {
  const rows = [];
  const classGroups = new Map();

  for (const job of jobs) {
    if (!job.teaching_class_id) {
      rows.push(toRow([job], t));
      continue;
    }
    const key = `${job.teaching_class_id}::${job.status}`;
    const group = classGroups.get(key) ?? [];
    group.push(job);
    classGroups.set(key, group);
  }
  for (const group of classGroups.values()) rows.push(toRow(group, t));

  return rows.sort(
    (a, b) => new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime(),
  );
}

function StatusBadge({ status }) {
  const statusMeta = useStatusMeta();
  const meta = statusMeta[status] ?? { label: status ?? "-", tone: "muted" };
  return <span className={`${styles.badge} ${styles[`badge_${meta.tone}`]}`}>{meta.label}</span>;
}

function EmptyState() {
  const { t } = useTranslation("resource");
  return <SharedEmptyState icon="library_add_check" title={t("BatchReviewPage.emptyTitle")} />;
}

function InfoRow({ label, value }) {
  return (
    <div className={styles.infoRow}>
      <span>{label}</span>
      <strong>{value || "-"}</strong>
    </div>
  );
}

function ProgressInline({ done, failed, total }) {
  const d = total ? (done / total) * 100 : 0;
  const f = total ? (failed / total) * 100 : 0;
  return (
    <div className={styles.progressInline}>
      <div className={styles.progressBar}>
        <div className={styles.progressApproved} style={{ width: `${d}%` }} />
        <div className={styles.progressRejected} style={{ width: `${f}%` }} />
      </div>
      <span className={styles.progressLabel}>
        {done}/{total}
      </span>
    </div>
  );
}

/* RRULE 轉人話：只處理後端 build_weekly_rule 產生的 FREQ=WEEKLY 形式，
   解析不了（含未知 BYDAY 代碼）回傳 null，由呼叫端改顯示原始字串 */
const RRULE_DAY_KEYS = {
  MO: "BatchReviewPage.dayMO", TU: "BatchReviewPage.dayTU", WE: "BatchReviewPage.dayWE",
  TH: "BatchReviewPage.dayTH", FR: "BatchReviewPage.dayFR", SA: "BatchReviewPage.daySA", SU: "BatchReviewPage.daySU",
};

function describeRecurrence(rule, t) {
  const parts = Object.fromEntries(String(rule ?? "").split(";").map((pair) => pair.split("=")));
  if (parts.FREQ !== "WEEKLY" || !parts.BYDAY) return null;
  const dayKeys = parts.BYDAY.split(",").map((code) => RRULE_DAY_KEYS[code]);
  if (dayKeys.some((key) => !key)) return null;
  const days = dayKeys.map((key) => t(key)).join(t("BatchReviewPage.recurDaySeparator"));
  const time = `${String(parts.BYHOUR ?? 0).padStart(2, "0")}:${String(parts.BYMINUTE ?? 0).padStart(2, "0")}`;
  return t("BatchReviewPage.recurWeekly", { days, time });
}

function filterByTab(rows, tab) {
  if (tab === "all") return rows;
  return rows.filter((row) => row.reviewStatus === tab);
}

export default function BatchReviewPage() {
  const { t } = useTranslation("resource");
  const tabs = useTabs();
  const statusMeta = useStatusMeta();
  const toast = useToast();
  const confirm = useConfirm();

  const [activeTab, setActiveTab] = useState("pending");
  const [batches, setBatches] = useState([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  /* 切換列時詳情欄捲回頂端，避免殘留上一筆的捲動位置 */
  const detailPaneRef = useRef(null);
  useEffect(() => { detailPaneRef.current?.scrollTo({ top: 0 }); }, [selectedId]);
  const [comment, setComment] = useState("");
  const [reviewing, setReviewing] = useState(false);
  /** jobId → "loading" | [start, end][]，點「查看時段」才載入 */
  const [previews, setPreviews] = useState({});
  /* 開合與資料分開存：收合時保留已抓的時段，收合動畫才有內容可收、再展開也不用重抓 */
  const [openPreviews, setOpenPreviews] = useState({});

  /** silent = true 時不觸發 loading 與錯誤提示，供背景自動刷新使用 */
  const load = useCallback(async (silent = false) => {
    if (!silent) {
      setLoading(true);
      setError("");
    }
    try {
      const res = await BatchProvisionService.listAll();
      setBatches(Array.isArray(res) ? res : []);
    } catch (e) {
      if (!silent) {
        setBatches([]);
        setError(e?.message ?? t("BatchReviewPage.loadFailed"));
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(() => load(true));

  useEffect(() => { setComment(""); }, [activeTab]);

  const reviewRows = useMemo(() => buildRows(batches, t), [batches, t]);
  const rows = useMemo(() => filterByTab(reviewRows, activeTab), [reviewRows, activeTab]);

  const visibleRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((row) =>
      [
        row.title,
        row.applicant,
        row.applicantSubtext,
        row.className,
        row.resourceTypeText,
        statusMeta[row.status]?.label,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [rows, query, statusMeta]);

  const selected = useMemo(
    () => visibleRows.find((row) => row.id === selectedId) ?? visibleRows[0] ?? null,
    [visibleRows, selectedId],
  );

  const stats = useMemo(() => {
    const pending = reviewRows.filter((row) => row.reviewStatus === "pending").length;
    const approved = reviewRows.filter((row) => row.reviewStatus === "approved").length;
    const rejected = reviewRows.filter((row) => row.reviewStatus === "rejected").length;
    return { total: reviewRows.length, pending, approved, rejected };
  }, [reviewRows]);

  const togglePreview = async (jobId) => {
    if (openPreviews[jobId]) {
      setOpenPreviews((p) => ({ ...p, [jobId]: false }));
      return;
    }
    if (Array.isArray(previews[jobId])) {
      setOpenPreviews((p) => ({ ...p, [jobId]: true }));
      return;
    }
    setPreviews((p) => ({ ...p, [jobId]: "loading" }));
    try {
      const res = await BatchProvisionService.getRecurrencePreview(jobId);
      setPreviews((p) => ({ ...p, [jobId]: res?.windows ?? [] }));
      /* 先讓清單以收合狀態渲染一幀，下一幀再展開，首次載入才有展開動畫 */
      window.requestAnimationFrame(() => setOpenPreviews((p) => ({ ...p, [jobId]: true })));
    } catch (e) {
      setPreviews((p) => { const n = { ...p }; delete n[jobId]; return n; });
      toast.error(e?.message ?? t("BatchReviewPage.previewLoadFailed"));
    }
  };

  const submitReview = async (decision) => {
    if (!selected || reviewing || selected.reviewStatus !== "pending") return;
    const target = selected.classId
      ? t("BatchReviewPage.reviewTargetClass", {
          className: selected.className ?? t("BatchReviewPage.defaultClassName"),
          count: selected.jobs.length,
        })
      : t("BatchReviewPage.reviewTargetDefault");
    const approved = decision === "approved";
    const ok = await confirm({
      title: approved ? t("BatchReviewPage.approveTitle") : t("BatchReviewPage.rejectTitle"),
      message: approved
        ? t("BatchReviewPage.approveMessage", { target })
        : t("BatchReviewPage.rejectMessage", { target }),
      confirmText: approved ? t("BatchReviewPage.approve") : t("BatchReviewPage.reject"),
      danger: !approved,
    });
    if (!ok) return;

    setReviewing(true);
    try {
      const body = { decision, review_comment: comment.trim() || null };
      if (selected.classId) {
        await BatchProvisionService.reviewClass(selected.classId, body);
      } else {
        await BatchProvisionService.review(selected.jobs[0].id, body);
      }
      toast.success(
        approved ? t("BatchReviewPage.approvedToast") : t("BatchReviewPage.rejectedToast"),
      );
      setComment("");
      await load();
    } catch (e) {
      toast.error(e?.message ?? t("BatchReviewPage.actionFailed"));
    } finally {
      setReviewing(false);
    }
  };

  const isPending = selected?.reviewStatus === "pending";
  const preview = selected ? previews[selected.previewJobId] : undefined;
  const previewOpen = selected ? Boolean(openPreviews[selected.previewJobId]) : false;

  /* 展開後把清單捲進可視範圍（nearest = 只捲必要的最小距離），滑鼠不用再滾 */
  const recurListRef = useRef(null);
  useEffect(() => {
    if (!previewOpen) return undefined;
    // 等收合動畫（0.22s）跑完、高度到位後再捲，才不會捲不到底
    const timer = window.setTimeout(() => {
      recurListRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }, 240);
    return () => window.clearTimeout(timer);
  }, [previewOpen]);

  return (
    <div className={styles.page}>
      <PageHeader
        title={t("BatchReviewPage.pageTitle")}
        subtitle={t("BatchReviewPage.pageSubtitle")}
      />

      <div className={styles.statRow}>
        <div className={styles.statCard}>
          <div className={styles.statIcon}>
            <MIcon name="library_add_check" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("BatchReviewPage.statTotal")}</span>
            <span className={styles.statValue}>{stats.total}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconBusy}`}>
            <MIcon name="pending_actions" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("BatchReviewPage.statPending")}</span>
            <span className={styles.statValue}>{stats.pending}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconOk}`}>
            <MIcon name="task_alt" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("BatchReviewPage.statApproved")}</span>
            <span className={styles.statValue}>{stats.approved}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconDanger}`}>
            <MIcon name="block" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("BatchReviewPage.statRejected")}</span>
            <span className={styles.statValue}>{stats.rejected}</span>
          </div>
        </div>
      </div>

      <div className={styles.tabsRow}>
        <SegmentedControl
          className={styles.tabsControl}
          options={tabs.map(({ key, label }) => ({ value: key, label }))}
          value={activeTab}
          onChange={setActiveTab}
          ariaLabel={t("BatchReviewPage.tabsAriaLabel")}
        />

        <div className={styles.search}>
          <MIcon name="search" size={16} />
          <input
            type="text"
            className={styles.searchInput}
            placeholder={t("BatchReviewPage.searchPlaceholder")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>

      <div className={styles.content}>
        <div className={styles.reviewGrid}>
          <section className={styles.listPane}>
            {loading ? (
              <LoadingState text={t("BatchReviewPage.loadingBatches")} />
            ) : error ? (
              <div className={styles.stateBox}>
                <span>{error}</span>
                <button type="button" className={styles.btnSecondary} onClick={() => load()}>
                  {t("BatchReviewPage.retry")}
                </button>
              </div>
            ) : visibleRows.length === 0 ? (
              <EmptyState />
            ) : (
              <div className={styles.list}>
                {visibleRows.map((row) => (
                  <button
                    key={row.id}
                    type="button"
                    className={`${styles.row} ${selected?.id === row.id ? styles.rowActive : ""}`}
                    onClick={() => { setSelectedId(row.id); setComment(""); }}
                  >
                    <div className={styles.rowIcon}>
                      <MIcon name={row.classId ? "school" : "dns"} size={20} />
                    </div>
                    <div className={styles.rowMain}>
                      <span className={styles.rowName}>{row.title}</span>
                      <span className={styles.rowMeta}>
                        {t("BatchReviewPage.rowMeta", {
                          count: row.total,
                          applicant: row.applicant,
                        })}
                      </span>
                    </div>
                    <div className={styles.rowSide}>
                      <StatusBadge status={row.status} />
                      <span className={styles.rowTime}>{formatDateTime(row.createdAt, t)}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className={styles.detailPane} ref={detailPaneRef}>
            {!selected ? (
              <div className={styles.stateBox}>{t("BatchReviewPage.selectABatch")}</div>
            ) : (
              <>
                <div className={styles.detailHeader}>
                  <h2>{selected.title}</h2>
                  <p>{selected.applicant}</p>
                </div>

                <div className={styles.infoGrid}>
                  <InfoRow
                    label={t("BatchReviewPage.infoLabelType")}
                    value={
                      selected.classId
                        ? t("BatchReviewPage.typeClassBatch")
                        : t("BatchReviewPage.typeSingleBatch")
                    }
                  />
                  <InfoRow label={t("BatchReviewPage.infoLabelCourse")} value={selected.className} />
                  <InfoRow
                    label={t("BatchReviewPage.infoLabelResourceType")}
                    value={selected.resourceTypeText}
                  />
                  <InfoRow
                    label={t("BatchReviewPage.infoLabelVmCount")}
                    value={String(selected.total)}
                  />
                  <InfoRow label={t("BatchReviewPage.infoLabelSpec")} value={selected.specText} />
                  <InfoRow label={t("BatchReviewPage.infoLabelOs")} value={selected.osText} />
                  <InfoRow
                    label={t("BatchReviewPage.infoLabelSubmittedAt")}
                    value={formatDateTime(selected.createdAt, t)}
                  />
                  <InfoRow
                    label={t("BatchReviewPage.infoLabelApplicantEmail")}
                    value={selected.applicantSubtext}
                  />
                </div>

                <div className={styles.reasonBox}>
                  <span>{t("BatchReviewPage.progressLabel")}</span>
                  <ProgressInline
                    done={selected.done}
                    failed={selected.failed}
                    total={selected.total}
                  />
                  {selected.failed > 0 && (
                    <p className={styles.failedNote}>
                      {t("BatchReviewPage.failedCount", { count: selected.failed })}
                    </p>
                  )}
                </div>

                {selected.recurrenceRule && (
                  <div className={styles.reasonBox}>
                    <span>{t("BatchReviewPage.recurChipLabel")}</span>
                    <p className={styles.ruleHuman}>{describeRecurrence(selected.recurrenceRule, t) ?? selected.recurrenceRule}</p>
                    {describeRecurrence(selected.recurrenceRule, t) && <p className={styles.ruleText}>{selected.recurrenceRule}</p>}
                    <button
                      type="button"
                      className={styles.recurChip}
                      title={t("BatchReviewPage.recurChipTitle")}
                      onClick={() => togglePreview(selected.previewJobId)}
                    >
                      <MIcon name={preview === "loading" || previewOpen ? "expand_less" : "expand_more"} size={12} />
                      {t("BatchReviewPage.recurPreviewToggle")}
                    </button>
                    {preview === "loading" && (
                      <span className={styles.recurLoading}>
                        {t("BatchReviewPage.previewLoading")}
                      </span>
                    )}
                    {Array.isArray(preview) && (
                      <div ref={recurListRef} className={`${styles.recurCollapse} ${previewOpen ? styles.recurCollapseOpen : ""}`}>
                        <div className={styles.recurCollapseInner}>
                          <ul className={styles.recurWindows}>
                            {preview.length === 0 && <li>{t("BatchReviewPage.noScheduledWindows")}</li>}
                            {preview.map(([start, end]) => (
                              <li key={start}>
                                {formatDateTime(start, t)} ～ {formatDateTime(end, t)}
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {selected.tasks.length > 0 && (
                  <div className={styles.reasonBox}>
                    <span>{t("BatchReviewPage.membersLabel", { count: selected.tasks.length })}</span>
                    <ul className={styles.memberList}>
                      {selected.tasks.map((task) => (
                        <li key={task.id}>
                          <span className={styles.memberName}>
                            {task.user_name || task.user_email || t("BatchReviewPage.unknownUser")}
                          </span>
                          <span className={styles.memberMeta}>
                            {task.vmid
                              ? `VMID ${task.vmid}`
                              : (statusMeta[task.status]?.label ?? task.status)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {isPending ? (
                  <div className={styles.reviewBar}>
                    <label className={styles.commentField}>
                      <span>{t("BatchReviewPage.commentLabel")}</span>
                      <textarea
                        value={comment}
                        onChange={(event) => setComment(event.target.value)}
                        disabled={reviewing}
                        placeholder={t("BatchReviewPage.commentPlaceholder")}
                      />
                    </label>
                    <div className={styles.rowActions}>
                      <button
                        type="button"
                        className={styles.btnApprove}
                        disabled={reviewing}
                        onClick={() => submitReview("approved")}
                      >
                        {t("BatchReviewPage.approve")}
                      </button>
                      <button
                        type="button"
                        className={styles.btnReject}
                        disabled={reviewing}
                        onClick={() => submitReview("rejected")}
                      >
                        {t("BatchReviewPage.reject")}
                      </button>
                    </div>
                  </div>
                ) : (
                  (selected.reviewComment || selected.reviewedAt) && (
                    <div className={styles.reasonBox}>
                      <span>
                        {t("BatchReviewPage.commentLabel")}
                        {selected.reviewedAt
                          ? t("BatchReviewPage.reviewedAtSuffix", {
                              time: formatDateTime(selected.reviewedAt, t),
                            })
                          : ""}
                      </span>
                      <p>{selected.reviewComment || t("BatchReviewPage.noReviewNote")}</p>
                      {selected.reviewer && (
                        <p className={styles.reviewerLine}>{selected.reviewer}</p>
                      )}
                    </div>
                  )
                )}
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
