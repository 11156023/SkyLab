import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import card from "./HomeCard.module.scss";
import MachineTerminal from "./MachineTerminal";
import { buildTerminal } from "./terminalLines";
import styles from "./MachineCard.module.scss";

const KNOWN_STATUSES = ["running", "starting", "stopped", "provisioning", "failed", "expired"];
const STATUS_DOT = { running: card.dotSuccess, starting: card.dotPending, provisioning: card.dotPending, failed: card.dotDanger };
/* 終端亮著＝機器有在動；其餘（關機、失敗、到期、未知）螢幕轉灰 */
const SCREEN_ON = ["running", "starting", "provisioning"];
/* 使用時段擋住開機時，按鈕的文字與滑過看到的完整說明 */
const WINDOW_BLOCKED = {
  window_ended: { action: "HomeOverview.actionWindowEnded", hint: "HomeOverview.windowEndedHint" },
  window_not_started: { action: "HomeOverview.actionWindowNotStarted", hint: "HomeOverview.windowNotStartedHint" },
};
/* 相對時間（上次使用）、到期天數、自動關機只需要分鐘級的時鐘 */
const CLOCK_INTERVAL = 30_000;

function useNow(interval) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), interval);
    return () => clearInterval(timer);
  }, [interval]);
  return now;
}

/**
 * 首頁「最近使用機器」卡：單層卡片，左上狀態＋名稱、右上類型圖示磚，
 * VMID、系統、上次使用、運行時間、IP、用量、到期與自動關機印在中間的終端裡，
 * 底部是進入（或啟動並進入）、執行中才有的關機、詳情。
 */
export default function MachineCard({ machine, openingMachineId, onOpen, onInfo, onShutdown, shuttingDown = false }) {
  const { t, i18n } = useTranslation("personal");
  const now = useNow(CLOCK_INTERVAL);
  const isLxc = machine.type === "lxc";
  const status = KNOWN_STATUSES.includes(machine.status) ? machine.status : "unknown";
  const openingThis = openingMachineId === machine.vmid;
  /* 開機 task 還沒跑完（starting）時跟「剛按下開機」一樣顯示開機中，按鈕保持停用 */
  const opening = openingThis || machine.status === "starting";
  /* 按下「啟動並進入」後父層要等開完才更新狀態，這段期間卡片就當作開機中 */
  const booting = machine.status === "starting" || (openingThis && machine.status === "stopped");
  const shownStatus = booting ? "starting" : status;
  /* 個人申請的使用時段沒開始／已結束：後端一定擋開機，卡片先講清楚，不讓人按了才失敗 */
  const windowBlocked = machine.status !== "running" ? machine.start_blocked_reason ?? null : null;
  const launchable = ["running", "stopped"].includes(machine.status) && !windowBlocked;
  const provisioning = machine.status === "provisioning";
  const actionKey = opening
    ? "StudentHomePage.actionStarting"
    : windowBlocked
      ? WINDOW_BLOCKED[windowBlocked].action
      : machine.status === "running"
        ? "StudentHomePage.actionEnter"
        : machine.status === "stopped"
          ? "StudentHomePage.actionStartAndEnter"
          : provisioning ? "HomeOverview.actionProvisioning" : "HomeOverview.unavailable";
  const actionIcon = windowBlocked ? "event_busy" : opening || provisioning ? "hourglass_top" : "play_arrow";
  /* 關機只給執行中、且沒有正在開機／連線的機器 */
  const canShutdown = Boolean(onShutdown) && machine.status === "running" && !openingThis;
  const view = openingThis && status === "running" ? "connecting" : shownStatus;
  const terminal = buildTerminal(machine, { view, now, lang: i18n?.language, t });

  return (
    <article className={styles.card}>
      <div className={styles.head}>
        <div className={styles.headText}>
          <span className={`${card.dot} ${STATUS_DOT[shownStatus] ?? ""}`}>{t(`HomeOverview.machineStatus.${shownStatus}`)}</span>
          <h3 className={styles.name}>{machine.name}</h3>
        </div>
        <span className={styles.typeIcon} aria-hidden="true">
          <MIcon name={isLxc ? "terminal" : "desktop_windows"} size={20} />
        </span>
      </div>
      <MachineTerminal {...terminal} off={!SCREEN_ON.includes(shownStatus)}
        label={t("HomeOverview.machineTerminalLabel", { name: machine.name })} />
      <div className={styles.actions}>
        <button type="button" className={styles.launchButton} onClick={() => onOpen(machine)}
          disabled={openingMachineId !== null || !launchable || shuttingDown}
          title={windowBlocked ? t(WINDOW_BLOCKED[windowBlocked].hint) : undefined}>
          {/* 開機中／環境配置中的沙漏：全站處理中圖示統一轉圈（MIcon spin） */}
          <MIcon name={actionIcon} size={18} spin={!windowBlocked && (opening || provisioning)} />
          {t(actionKey)}
        </button>
        {canShutdown && (
          <button type="button" className={styles.iconButtonDanger} onClick={() => onShutdown(machine)}
            disabled={shuttingDown || openingMachineId !== null}
            aria-label={t("HomeOverview.shutdownAria", { name: machine.name })}
            title={t("HomeOverview.shutdown")}>
            <MIcon name="power_settings_new" size={18} />
          </button>
        )}
        <button type="button" className={styles.iconButton} onClick={() => onInfo(machine)}
          aria-label={t("StudentHomePage.machineInfoAria", { name: machine.name })}
          title={t("StudentHomePage.machineInfoAria", { name: machine.name })}>
          <MIcon name="info" size={18} />
        </button>
      </div>
    </article>
  );
}
