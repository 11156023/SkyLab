import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./ResourceMgmtPage.module.scss";
import PowerMenu from "../../../components/PowerMenu/PowerMenu";
import BatchBar, { BatchAction, BatchDivider } from "../../../components/DataToolbar/BatchBar";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { useToast } from "../../../hooks/useToast";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { ResourcesService } from "../../../services/resources";
import { joinList } from "../../../utils/joinList";
import { applicableVmids } from "./batchActions";

/* 確認框裡最多列出幾台的名字，其餘寫「等 N 台」 */
const DELETE_NAME_PREVIEW = 5;

function useActions() {
  const { t } = useTranslation("resource");
  return {
    start:    { label: t("ResourceMgmtPage.actionStart"),    icon: "play_arrow",         tone: "ok" },
    shutdown: { label: t("ResourceMgmtPage.actionShutdown"), icon: "power_settings_new" },
    reboot:   { label: t("ResourceMgmtPage.actionReboot"),   icon: "replay" },
    stop:     { label: t("ResourceMgmtPage.actionStop"),     icon: "stop",               tone: "warn" },
    reset:    { label: t("ResourceMgmtPage.actionReset"),    icon: "restart_alt",        tone: "warn" },
    delete:   { label: t("ResourceMgmtPage.delete"),         icon: "delete" },
  };
}

/**
 * 資源管理的批量列（共用 DataToolbar／BatchBar 的資源版）。
 * - 動作只送給適用的機器（關機只送執行中的…），全都不適用就停用；略過幾台在提示與結果裡講明
 * - 常用的開機、關機放外面，重新啟動／強制停止／強制重置收進「更多」；刪除獨立放最右
 * - 走後端的批量 API（ResourcesService.batchActionInChunks），進度依分批回報
 *
 * @param {Array}    selected 勾選中的資源物件（要看 status 判斷適用與否）
 * @param {Function} onDone   (failedVmids: number[] | null) => void；null＝整批請求失敗，選取不動
 * @param {Function} onClear  清除選取
 */
export default function BatchActionBar({ selected, onDone, onClear }) {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const confirm = useConfirm();
  const actions = useActions();
  const [progress, setProgress] = useState(null); // { label, done, total }
  const [moreOpen, setMoreOpen] = useState(false);
  const more = useDialogPresence(moreOpen, 130);
  const moreBtnRef = useRef(null);
  const count = selected.length;

  if (count === 0) return null;

  const applicable = (action) => applicableVmids(selected, action);

  async function confirmed(action, vmids) {
    const label = actions[action].label;
    if (action === "delete") {
      const names = selected.filter((r) => vmids.includes(r.vmid)).map((r) => r.name || `VMID ${r.vmid}`);
      const preview = joinList(names.slice(0, DELETE_NAME_PREVIEW));
      const rest = names.length - DELETE_NAME_PREVIEW;
      return confirm({
        title: t("ResourceMgmtPage.batchDeleteTitle", { count: vmids.length }),
        message: `${t("ResourceMgmtPage.batchDeleteDesc")}\n${preview}${rest > 0 ? ` ${t("ResourceMgmtPage.batchDeleteNamesMore", { count: rest })}` : ""}`,
        confirmText: t("ResourceMgmtPage.confirmDelete"),
        danger: true,
      });
    }
    /* 強制停止／強制重置等同直接拔電，未存檔的資料會遺失：這兩個先確認 */
    if (action === "stop" || action === "reset") {
      return confirm({
        title: t("ResourceMgmtPage.batchForceTitle", { count: vmids.length, label }),
        message: t(action === "stop" ? "ResourceMgmtPage.batchForceStopDesc" : "ResourceMgmtPage.batchForceResetDesc"),
        confirmText: label,
        danger: true,
      });
    }
    return true;
  }

  async function run(action) {
    const vmids = applicable(action);
    if (!vmids.length || progress) return;
    if (!(await confirmed(action, vmids))) return;
    const label = actions[action].label;
    const skipped = count - vmids.length;
    setProgress({ label, done: 0, total: vmids.length });
    try {
      const res = await ResourcesService.batchActionInChunks(vmids, action, {
        onProgress: ({ done, total }) => setProgress((prev) => prev && { ...prev, done, total }),
      });
      if (res.failed === 0) {
        toast.success(skipped > 0
          ? t("ResourceMgmtPage.batchSuccessSkippedToast", { count: res.succeeded, label, skipped })
          : t("ResourceMgmtPage.batchSuccessToast", { count: res.succeeded, label }));
      } else {
        toast.error(t("ResourceMgmtPage.batchFailedKeptToast", { label, succeeded: res.succeeded, failed: res.failed }));
      }
      onDone((res.results ?? []).filter((item) => !item.success).map((item) => item.vmid));
    } catch (err) {
      toast.error(err?.message ?? t("ResourceMgmtPage.batchActionFailed"));
      onDone(null);
    } finally {
      setProgress(null);
    }
  }

  /* 適用台數少於勾選台數時，滑過說明會略過幾台；一台都不適用就停用 */
  function hintFor(action) {
    const n = applicable(action).length;
    if (n === 0) return t("ResourceMgmtPage.batchNotApplicable");
    return n < count ? t("ResourceMgmtPage.batchApplicableHint", { count: n, skipped: count - n }) : undefined;
  }

  function actionButton(action, danger = false) {
    const { label, icon } = actions[action];
    return (
      <BatchAction
        key={action}
        icon={icon}
        label={label}
        danger={danger}
        disabled={applicable(action).length === 0}
        title={hintFor(action)}
        onClick={() => run(action)}
      />
    );
  }

  return (
    <BatchBar
      countLabel={t("ResourceMgmtPage.selectedCount", { count })}
      onClear={onClear}
      progress={progress && {
        text: t("ResourceMgmtPage.batchRunning", { label: progress.label, done: progress.done, total: progress.total }),
        done: progress.done,
        total: progress.total,
      }}
      /* 「更多」選單開著時 Esc 是關選單的 */
      escapeToClear={!moreOpen}
    >
      {actionButton("start")}
      {actionButton("shutdown")}
      <div className={styles.menuWrap}>
        {more.open && (
          <PowerMenu
            title={t("ResourceMgmtPage.batchMore")}
            items={["reboot", "stop", "reset"].map((action) => ({
              action,
              ...actions[action],
              disabled: applicable(action).length === 0,
            }))}
            onControl={run}
            onClose={() => setMoreOpen(false)}
            anchorRef={moreBtnRef}
            closing={more.closing}
          />
        )}
        <BatchAction
          ref={moreBtnRef}
          icon="more_horiz"
          label={t("ResourceMgmtPage.batchMore")}
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen((open) => !open)}
        />
      </div>
      <BatchDivider />
      {actionButton("delete", true)}
    </BatchBar>
  );
}
