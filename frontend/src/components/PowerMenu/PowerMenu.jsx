import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { computePosition, isAnchorOffscreen } from "./position";
import styles from "./PowerMenu.module.scss";

/* 選單 portal 到 body 並用 position: fixed 定位——列表容器（表格的
   .tableWrap、卡片的 .card）同時有 overflow 與 backdrop-filter，
   absolute 選單會被裁掉、z-index 也出不了那層 stacking context。 */

const ITEMS = [
  { action: "start",    labelKey: "PowerMenu.start",    icon: "play_arrow",         needs: "stopped", tone: "ok"   },
  { action: "stop",     labelKey: "PowerMenu.stop",     icon: "stop",               needs: "running", tone: "warn" },
  { action: "shutdown", labelKey: "PowerMenu.shutdown", icon: "power_settings_new", needs: "running"               },
  { action: "reset",    labelKey: "PowerMenu.reset",    icon: "restart_alt",        needs: "running", tone: "warn" },
  { action: "reboot",   labelKey: "PowerMenu.reboot",   icon: "replay",             needs: "running"               },
];

export default function PowerMenu({
  resource,
  actionLoading,
  onControl,
  onDeleteClick,
  onConvertTemplate,
  onClose,
  anchorRef,
  closing,
}) {
  const { t } = useTranslation("components");
  const ref = useRef(null);
  const [pos, setPos] = useState(null);

  // 捲動時要判斷是否該關閉，但 onClose 每次 render 都是新的 function，
  // 存進 ref 才不會讓監聽反覆解綁重綁
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; });

  const reposition = useCallback(() => {
    const anchor = anchorRef?.current;
    const menu   = ref.current;
    if (!anchor || !menu) return;

    const rect = anchor.getBoundingClientRect();
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    // 錨點被捲出視窗後選單只會卡在邊緣，直接收起來
    if (isAnchorOffscreen(rect, viewport)) {
      onCloseRef.current();
      return;
    }
    setPos(computePosition(rect, menu.offsetHeight, viewport));
  }, [anchorRef]);

  // 要先量到選單實際高度才知道往上或往下翻，定位完成前保持隱形
  useLayoutEffect(() => { reposition(); }, [reposition]);

  // fixed 選單不會跟著錨點捲動，捲動／縮放時重算（capture 才收得到內層容器的 scroll）
  useEffect(() => {
    const opts = { passive: true, capture: true };
    window.addEventListener("scroll", reposition, opts);
    window.addEventListener("resize", reposition);
    return () => {
      window.removeEventListener("scroll", reposition, opts);
      window.removeEventListener("resize", reposition);
    };
  }, [reposition]);

  useEffect(() => {
    function handlePointerDown(e) {
      if (!ref.current?.contains(e.target) && !anchorRef?.current?.contains(e.target)) onClose();
    }
    function handleKeyDown(e) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose, anchorRef]);

  const enabled = {
    running: resource.status === "running",
    stopped: resource.status === "stopped" || resource.status === "paused",
  };

  const className = [
    styles.powerMenu,
    pos?.openUp ? styles.powerMenuUp : styles.powerMenuDown,
    closing ? styles.powerMenuOut : "",
  ].filter(Boolean).join(" ");

  return createPortal(
    <div
      ref={ref}
      className={className}
      style={pos ? { top: pos.top, left: pos.left } : { top: 0, left: 0, visibility: "hidden" }}
    >
      <div className={styles.powerMenuTitle}>{t("PowerMenu.title")}</div>
      <div className={styles.powerMenuGrid}>
        {ITEMS.map(({ action, labelKey, icon, needs, tone }) => (
          <button
            key={action}
            type="button"
            className={`${styles.powerMenuItem} ${tone === "warn" ? styles.powerMenuItemWarn : ""}`}
            disabled={!enabled[needs] || !!actionLoading}
            onClick={() => { onClose(); onControl(action); }}
          >
            <span className={tone === "ok" ? styles.powerMenuIconOk : styles.powerMenuIcon}>
              <MIcon name={icon} size={15} />
            </span>
            {t(labelKey)}
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
