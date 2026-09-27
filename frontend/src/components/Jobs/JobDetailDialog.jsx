import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import Modal from "../Modal/Modal";
import { useAuth } from "../../contexts/AuthContext";
import useDialogPresence from "../../hooks/useDialogPresence";
import { JobsService } from "../../services/jobs";
import { JOB_STATUS_META_KEYS } from "./JobRow";
import styles from "./Jobs.module.scss";
import { formatDate, formatDateTime } from "../../utils/formatDate";

const fmt = (iso) => formatDateTime(iso);

/* 後端 extra 的時間欄位是 ISO 字串，顯示時轉成全站統一的日期格式 */
const ISO_DATETIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

const formatExtraValue = (v, t) => {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? t("JobDetailDialog.boolYes") : t("JobDetailDialog.boolNo");
  if (typeof v === "object") return JSON.stringify(v, null, 2);
  if (typeof v === "string" && ISO_DATETIME.test(v)) return formatDateTime(v);
  if (typeof v === "string" && ISO_DATE.test(v)) return formatDate(v);
  return String(v);
};

/* 已在上方時間區塊「完成」顯示的欄位（刪除為 completed_at、範本任務為 finished_at），詳細裡不重複列 */
const HIDDEN_EXTRA_KEYS = new Set(["completed_at", "finished_at"]);

/* extra 欄位的 key → 顯示名稱的翻譯 key（EXTRA_LABEL_KEYS 為模組層級常數，無法呼叫 hook） */
const EXTRA_LABEL_KEYS = {
  request_id: "JobDetailDialog.extraRequestId",
  vmid: "JobDetailDialog.extraVmid",
  resource_vmid: "JobDetailDialog.extraVmid",
  node: "JobDetailDialog.extraNode",
  name: "JobDetailDialog.extraName",
  purge: "JobDetailDialog.extraPurge",
  force: "JobDetailDialog.extraForce",
  provisioning_status: "JobDetailDialog.extraProvisioningStatus",
  task_type: "JobDetailDialog.extraTaskType",
  payload: "JobDetailDialog.extraPayload",
  result: "JobDetailDialog.extraResult",
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

  if (!presence.open) return null;

  const item = data?.item;
  const statusMeta = item ? JOB_STATUS_META_KEYS[item.status] : null;
  const extraEntries = data
    ? Object.entries(data.extra ?? {}).filter(
        ([k, v]) =>
          v !== null && v !== undefined && v !== ""
          && !HIDDEN_EXTRA_KEYS.has(k)
          /* VMID 是系統內部編號，僅管理員／老師看得到 */
          && (showVmid || (k !== "vmid" && k !== "resource_vmid")),
      )
      /* JSON 物件獨占整列，排到最後，一般欄位的三欄格線才不會被切斷 */
      .sort(([, a], [, b]) => (typeof a === "object") - (typeof b === "object"))
    : [];

  /* 外框（遮罩、標題列、Esc、焦點、捲動鎖）交給共用 Modal；
     標題本身已含任務類型（「刪除 xxx」「開機申請：xxx」），不再另掛類型標籤 */
  return (
    <Modal
      closing={presence.closing}
      onClose={onClose}
      closeButton
      size="md"
      title={item ? item.title : t("JobDetailDialog.dialogAriaLabel")}
    >
      <div className={styles.dialogJobId}>{presence.item}</div>

      {loading && !data && <JobDetailLoading />}

      {error && (
        <div className={styles.dialogError}>
          <MIcon name="error_outline" size={16} />
          <div>
            <div className={styles.dialogErrorTitle}>{t("Error.generic", { ns: "common" })}</div>
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
                <MIcon name={statusMeta.icon} size={14} spin={statusMeta.spin} />
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
            <div className={styles.dialogExtraItem}>
              <span className={styles.dialogExtraKey}>{t("JobDetailDialog.createdAt")}</span>
              <span className={styles.dialogValue}>{fmt(item.created_at)}</span>
            </div>
            <div className={styles.dialogExtraItem}>
              <span className={styles.dialogExtraKey}>{t("JobDetailDialog.updatedAt")}</span>
              <span className={styles.dialogValue}>{fmt(item.updated_at)}</span>
            </div>
            <div className={styles.dialogExtraItem}>
              <span className={styles.dialogExtraKey}>{t("JobDetailDialog.completedAt")}</span>
              <span className={styles.dialogValue}>{fmt(item.completed_at)}</span>
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
              {/* 同上方時間區塊：標籤在上、值在下，各欄左緣對齊；JSON 物件（範本任務的 payload／result）獨占一整列 */}
              <div className={styles.dialogExtraGrid}>
                {extraEntries.map(([k, v]) => {
                  const isObject = typeof v === "object";
                  return (
                    <div key={k} className={`${styles.dialogExtraItem} ${isObject ? styles.dialogExtraWide : ""}`}>
                      <span className={styles.dialogExtraKey}>{EXTRA_LABEL_KEYS[k] ? t(EXTRA_LABEL_KEYS[k]) : k}</span>
                      {isObject ? (
                        <pre className={styles.dialogExtraJson}>{formatExtraValue(v, t)}</pre>
                      ) : (
                        <span className={styles.dialogValue}>{formatExtraValue(v, t)}</span>
                      )}
                    </div>
                  );
                })}
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
    </Modal>
  );
}

function JobDetailLoading() {
  const { t } = useTranslation("components");
  return (
    <div className={styles.jobLoading}>
      <MIcon name="refresh" size={16} spin />
      <span>{t("JobDetailDialog.loading")}</span>
    </div>
  );
}
