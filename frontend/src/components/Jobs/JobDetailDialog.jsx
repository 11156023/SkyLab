import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { useAuth } from "../../contexts/AuthContext";
import useDialogPresence from "../../hooks/useDialogPresence";
import { JobsService } from "../../services/jobs";
import { JOB_KIND_LABEL_KEYS, JOB_STATUS_META_KEYS } from "./JobRow";
import styles from "./Jobs.module.scss";
import { formatDateTime } from "../../utils/formatDate";

const fmt = (iso) => formatDateTime(iso);

const formatExtraValue = (v, t) => {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? t("JobDetailDialog.boolYes") : t("JobDetailDialog.boolNo");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
};

/* extra 欄位的 key → 顯示名稱的翻譯 key（EXTRA_LABEL_KEYS 為模組層級常數，無法呼叫 hook） */
const EXTRA_LABEL_KEYS = {
  request_id: "JobDetailDialog.extraRequestId",
  vmid: "JobDetailDialog.extraVmid",
  source_node: "JobDetailDialog.extraSourceNode",
  target_node: "JobDetailDialog.extraTargetNode",
  attempt_count: "JobDetailDialog.extraAttemptCount",
  rebalance_epoch: "JobDetailDialog.extraRebalanceEpoch",
  claimed_by: "JobDetailDialog.extraClaimedBy",
  requested_at: "JobDetailDialog.extraRequestedAt",
  available_at: "JobDetailDialog.extraAvailableAt",
  claimed_at: "JobDetailDialog.extraClaimedAt",
  started_at: "JobDetailDialog.extraStartedAt",
  finished_at: "JobDetailDialog.extraFinishedAt",
  hostname: "JobDetailDialog.extraHostname",
  task_id: "JobDetailDialog.extraTaskId",
  template_slug: "JobDetailDialog.extraTemplateSlug",
  template_name: "JobDetailDialog.extraTemplateName",
  raw_status: "JobDetailDialog.extraRawStatus",
  progress_text: "JobDetailDialog.extraProgressText",
  resource_type: "JobDetailDialog.extraResourceType",
  cores: "JobDetailDialog.extraCores",
  memory: "JobDetailDialog.extraMemory",
  storage: "JobDetailDialog.extraStorage",
  disk_size: "JobDetailDialog.extraDiskSize",
  rootfs_size: "JobDetailDialog.extraRootfsSize",
  ostemplate: "JobDetailDialog.extraOstemplate",
  template_id: "JobDetailDialog.extraTemplateId",
  assigned_node: "JobDetailDialog.extraAssignedNode",
  actual_node: "JobDetailDialog.extraActualNode",
  desired_node: "JobDetailDialog.extraDesiredNode",
  migration_status: "JobDetailDialog.extraMigrationStatus",
  expiry_date: "JobDetailDialog.extraExpiryDate",
  start_at: "JobDetailDialog.extraStartAt",
  end_at: "JobDetailDialog.extraEndAt",
  reason: "JobDetailDialog.extraReason",
  review_comment: "JobDetailDialog.extraReviewComment",
  change_type: "JobDetailDialog.extraChangeType",
  current_cpu: "JobDetailDialog.extraCurrentCpu",
  current_memory: "JobDetailDialog.extraCurrentMemory",
  current_disk: "JobDetailDialog.extraCurrentDisk",
  requested_cpu: "JobDetailDialog.extraRequestedCpu",
  requested_memory: "JobDetailDialog.extraRequestedMemory",
  requested_disk: "JobDetailDialog.extraRequestedDisk",
  applied_at: "JobDetailDialog.extraAppliedAt",
};

/** 任務還在跑就每 3 秒刷新詳情 */
const ACTIVE_STATUSES = new Set(["pending", "running", "blocked"]);

export default function JobDetailDialog({ jobId, onClose }) {
  const { t } = useTranslation("components");
  const open = jobId !== null;
  // 關閉時先播放離場動畫再卸載；動畫期間保留內容避免閃爍
  const presence = useDialogPresence(jobId);
  const { user } = useAuth();
  const showVmid = user?.is_superuser || user?.role === "admin" || user?.role === "teacher";
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  // 完全關閉（離場動畫結束）後才清空內容
  useEffect(() => {
    if (!presence.open) {
      setData(null);
      setError(null);
    }
  }, [presence.open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    let timer = null;

    const load = async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        const res = await JobsService.detail(jobId);
        if (cancelled) return;
        setData(res);
        setError(null);
        if (ACTIVE_STATUSES.has(res?.item?.status)) {
          timer = setTimeout(() => load(true), 3000);
        }
      } catch (e) {
        if (!cancelled) setError(e?.message ?? t("JobDetailDialog.unknownError"));
      } finally {
        if (!cancelled && !silent) setLoading(false);
      }
    };
    load();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [open, jobId]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!presence.open) return null;

  const item = data?.item;
  const statusMeta = item ? JOB_STATUS_META_KEYS[item.status] : null;
  const extraEntries = data
    ? Object.entries(data.extra ?? {}).filter(
        ([k, v]) =>
          v !== null && v !== undefined && v !== ""
          /* VMID 是系統內部編號，僅管理員／老師看得到 */
          && (showVmid || k !== "vmid"),
      )
    : [];

  // Portal 到 body：banner 的 backdrop-filter 會建立 stacking context，
  // 直接 render 會讓 fixed overlay 被限制在 banner 內
  return createPortal(
    <div
      className={`${styles.dialogOverlay} ${presence.closing ? styles.dialogOverlayOut : ""}`}
      onClick={onClose}
    >
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label={t("JobDetailDialog.dialogAriaLabel")}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.dialogHeader}>
          <div className={styles.dialogTitleRow}>
            {item ? (
              <>
                <span className={styles.jobKindChip}>{JOB_KIND_LABEL_KEYS[item.kind] ? t(JOB_KIND_LABEL_KEYS[item.kind]) : item.kind}</span>
                <h2 className={styles.dialogTitle}>{item.title}</h2>
              </>
            ) : (
              <h2 className={styles.dialogTitle}>{t("JobDetailDialog.dialogAriaLabel")}</h2>
            )}
          </div>
          <button type="button" className={styles.dialogClose} onClick={onClose} aria-label={t("JobDetailDialog.closeAriaLabel")}>
            <MIcon name="close" size={18} />
          </button>
        </div>
        <div className={styles.dialogJobId}>{jobId}</div>

        {loading && !data && <JobDetailLoading />}

        {error && (
          <div className={styles.dialogError}>
            <MIcon name="error_outline" size={16} />
            <div>
              <div className={styles.dialogErrorTitle}>{t("JobDetailDialog.loadFailedTitle")}</div>
              <div>{error}</div>
            </div>
          </div>
        )}

        {item && (
          <div className={styles.dialogBody}>
            {/* 狀態列 */}
            <div className={styles.dialogStatusRow}>
              {statusMeta && (
                <span className={`${styles.statusBadge} ${styles[statusMeta.tone]}`}>
                  <span className={statusMeta.spin ? styles.spin : ""}>
                    <MIcon name={statusMeta.icon} size={14} />
                  </span>
                  {t(statusMeta.labelKey)}
                </span>
              )}
              {typeof item.progress === "number" && (
                <span className={styles.statusBadge}>{item.progress}%</span>
              )}
              {item.user_email && (
                <span className={styles.dialogInitiator}>{t("JobDetailDialog.initiator", { email: item.user_email })}</span>
              )}
            </div>

            {/* 時間 */}
            <div className={styles.dialogTimes}>
              <div>
                <div className={styles.dialogFieldLabel}>{t("JobDetailDialog.createdAt")}</div>
                <div className={styles.dialogMono}>{fmt(item.created_at)}</div>
              </div>
              <div>
                <div className={styles.dialogFieldLabel}>{t("JobDetailDialog.updatedAt")}</div>
                <div className={styles.dialogMono}>{fmt(item.updated_at)}</div>
              </div>
              <div>
                <div className={styles.dialogFieldLabel}>{t("JobDetailDialog.completedAt")}</div>
                <div className={styles.dialogMono}>{fmt(item.completed_at)}</div>
              </div>
            </div>

            {/* 訊息 */}
            {item.message && (
              <div>
                <div className={styles.dialogFieldLabel}>{t("JobDetailDialog.message")}</div>
                <div className={styles.dialogMessage}>{item.message}</div>
              </div>
            )}

            {/* 詳細欄位 */}
            {extraEntries.length > 0 && (
              <div>
                <div className={styles.dialogFieldLabel}>{t("JobDetailDialog.detail")}</div>
                <div className={styles.dialogExtraGrid}>
                  {extraEntries.map(([k, v]) => (
                    <div key={k} className={styles.dialogExtraItem}>
                      <span className={styles.dialogExtraKey}>{t("JobDetailDialog.extraKeyLine", { label: EXTRA_LABEL_KEYS[k] ? t(EXTRA_LABEL_KEYS[k]) : k })}</span>
                      <span className={styles.dialogMono}>{formatExtraValue(v, t)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 錯誤 */}
            {data.error && (
              <div>
                <div className={`${styles.dialogFieldLabel} ${styles.toneDanger}`}>{t("JobDetailDialog.error")}</div>
                <pre className={styles.dialogErrorOutput}>{data.error}</pre>
              </div>
            )}

            {/* 輸出 */}
            {data.output && (
              <div>
                <div className={styles.dialogFieldLabel}>{t("JobDetailDialog.output")}</div>
                <pre className={styles.dialogOutput}>{data.output}</pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

function JobDetailLoading() {
  const { t } = useTranslation("components");
  return (
    <div className={styles.jobLoading}>
      <span className={styles.spin}>
        <MIcon name="refresh" size={16} />
      </span>
      <span>{t("JobDetailDialog.loading")}</span>
    </div>
  );
}
