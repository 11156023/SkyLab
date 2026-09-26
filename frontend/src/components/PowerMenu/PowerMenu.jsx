import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import useAnchoredMenu from "../../hooks/useAnchoredMenu";
import styles from "./PowerMenu.module.scss";

/* 選單 portal 到 body 並用 position: fixed 定位——列表容器（表格的
   .tableWrap、卡片的 .card）同時有 overflow 與 backdrop-filter，
   absolute 選單會被裁掉、z-index 也出不了那層 stacking context。 */

const ITEMS = [
  { action: "start",    labelKey: "PowerMenu.start",    icon: "play_arrow",         needs: "stopped", tone: "ok"   },
  { action: "stop",     labelKey: "PowerMenu.stop",     icon: "stop",               needs: "poweredOn", tone: "warn" },
  { action: "shutdown", labelKey: "PowerMenu.shutdown", icon: "power_settings_new", needs: "running"               },
  { action: "reset",    labelKey: "PowerMenu.reset",    icon: "restart_alt",        needs: "running", tone: "warn" },
  { action: "reboot",   labelKey: "PowerMenu.reboot",   icon: "replay",             needs: "running"               },
];

/* items 讓呼叫端自帶動作清單（例如整組機器只有開機／關機／結束練習）。
   定位、外點關閉、Esc、portal 這些難的部分共用同一份，選單外觀才會一致。 */
export default function PowerMenu({
  resource,
  items,
  title,
  actionLoading,
  onControl,
  onDeleteClick,
  onConvertTemplate,
  onClose,
  anchorRef,
  closing,
}) {
  const { t } = useTranslation("components");
  const { ref, pos } = useAnchoredMenu({ anchorRef, onClose });

  /* starting＝開機 task 還在跑：只留強制停止（開機卡住時的退路），其餘要等開完機 */
  const enabled = {
    running: resource?.status === "running",
    poweredOn: resource?.status === "running" || resource?.status === "starting",
    stopped: resource?.status === "stopped" || resource?.status === "paused",
  };
  /* 個人申請的使用時段沒開始／已結束：後端一定擋開機，選單先把開機停用並講原因 */
  const windowBlocked = resource?.status !== "running" ? resource?.start_blocked_reason ?? null : null;
  const entries = items ?? ITEMS.map((item) => ({
    ...item,
    disabled: !enabled[item.needs] || (item.action === "start" && Boolean(windowBlocked)),
  }));

  const className = [
    styles.powerMenu,
    pos?.openUp ? styles.powerMenuUp : styles.powerMenuDown,
    closing ? styles.powerMenuOut : "",
  ].filter(Boolean).join(" ");

  return createPortal(
    <div
      ref={ref}
      className={className}
      data-guide="resource-power-menu"
      style={pos ? { top: pos.top, left: pos.left } : { top: 0, left: 0, visibility: "hidden" }}
    >
      <div className={styles.powerMenuTitle}>{title ?? t("PowerMenu.title")}</div>
      {!items && windowBlocked && (
        <div className={styles.powerMenuNote}>
          <MIcon name="event_busy" size={14} />
          {t(windowBlocked === "window_ended" ? "PowerMenu.windowEnded" : "PowerMenu.windowNotStarted")}
        </div>
      )}
      <div className={styles.powerMenuGrid}>
        {entries.map(({ action, label, labelKey, icon, tone, disabled }) => (
          <button
            key={action}
            type="button"
            className={`${styles.powerMenuItem} ${tone === "warn" ? styles.powerMenuItemWarn : ""} ${tone === "danger" ? styles.powerMenuItemDanger : ""}`}
            disabled={disabled || !!actionLoading}
            onClick={() => { onClose(); onControl(action); }}
          >
            <span className={tone === "ok" ? styles.powerMenuIconOk : styles.powerMenuIcon}>
              <MIcon name={icon} size={15} />
            </span>
            {label ?? t(labelKey)}
          </button>
        ))}
        {/* 老師／管理員把調好的機器轉成範本；沒有 onConvertTemplate 就不顯示 */}
        {onConvertTemplate && <button
          type="button"
          className={styles.powerMenuItem}
          disabled={!!actionLoading}
          onClick={() => { onClose(); onConvertTemplate(); }}
        >
          <span className={styles.powerMenuIcon}><MIcon name="library_add" size={15} /></span>
          {t("PowerMenu.convertTemplate")}
        </button>}
        {/* 環境內的機器不能單台刪除，整組回收由環境層級處理；沒有 onDeleteClick 就不顯示 */}
        {onDeleteClick && <button
          type="button"
          className={`${styles.powerMenuItem} ${styles.powerMenuItemDanger}`}
          onClick={() => onDeleteClick()}
        >
          <span className={styles.powerMenuIcon}><MIcon name="delete_outline" size={15} /></span>
          {t("PowerMenu.delete")}
        </button>}
      </div>
    </div>,
    document.body,
  );
}
