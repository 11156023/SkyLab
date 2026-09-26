/**
 * Modal — 全站對話框外框
 * 統一處理：portal 到 body、遮罩與進出場動畫、role／aria-modal／aria-labelledby、
 * Esc 與點遮罩關閉（busy 時不關）、開啟時焦點移進對話框並在關閉後還原、Tab 鎖在對話框內、
 * 鎖住底下頁面捲動。各頁只負責放內容。
 *
 * 開關動畫沿用 useDialogPresence，由父層決定掛載：
 *   const presence = useDialogPresence(show);
 *   {presence.open && (
 *     <Modal closing={presence.closing} onClose={() => setShow(false)} title="…" actions={…}>…</Modal>
 *   )}
 *
 * 版型：
 *   - 預設精簡卡：標題、說明、內容、按鈕列排成一欄（確認框、命名框、小表單）
 *   - closeButton：標題列帶 ×，內容區獨立捲動，按鈕列固定在底部（欄位多的表單）
 *
 * @param {boolean} closing 離場動畫中（useDialogPresence 的 closing）
 * @param {() => void} onClose Esc、點遮罩、按 × 時呼叫
 * @param {boolean} busy 送出中：Esc、點遮罩、× 都不關，跟取消鈕的 disabled 一致
 * @param {"sm"|"md"|"lg"|"xl"} size 寬度四級：400／640／1100／1280
 * @param {"dialog"|"alertdialog"} role 危險操作確認用 alertdialog
 * @param {"div"|"form"} as 表單型對話框傳 form，onSubmit 等屬性會掛在同一個元素上
 * @param {object} closeProps／actionsProps 額外掛在 × 與按鈕列上的屬性（例如導覽用的 data-guide）
 */
import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import useBodyScrollLock from "../../hooks/useBodyScrollLock";
import styles from "./Modal.module.scss";

/* 目前開著的 Modal（依開啟順序）；疊了兩層時 Esc 只關最上面那層 */
const openStack = [];

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

const SIZE_CLASS = { sm: styles.sizeSm, md: styles.sizeMd, lg: styles.sizeLg, xl: styles.sizeXl };

export default function Modal({
  closing = false,
  onClose,
  busy = false,
  title,
  icon,
  description,
  actions,
  closeButton = false,
  size = "sm",
  role = "dialog",
  as: Tag = "div",
  className = "",
  closeProps,
  actionsProps,
  onKeyDown,
  children,
  ...rest
}) {
  const { t } = useTranslation("common");
  const dialogRef = useRef(null);
  const titleId = useId();
  const descId = useId();
  const latest = useRef({ onClose, busy, closing });

  useEffect(() => {
    latest.current = { onClose, busy, closing };
  });

  useBodyScrollLock(true);

  const requestClose = () => {
    const state = latest.current;
    if (!state.busy && !state.closing) state.onClose?.();
  };

  /* Esc 關閉；掛在 window，焦點不在對話框裡時也關得掉 */
  useEffect(() => {
    const token = {};
    openStack.push(token);
    const handleKey = (event) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (openStack[openStack.length - 1] !== token) return;
      const state = latest.current;
      if (!state.busy && !state.closing) state.onClose?.();
    };
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("keydown", handleKey);
      openStack.splice(openStack.indexOf(token), 1);
    };
  }, []);

  /* 焦點移進對話框（內容有 autoFocus 就尊重它），關閉後還給原本的按鈕 */
  useEffect(() => {
    const dialog = dialogRef.current;
    const previous = document.activeElement;
    if (dialog && !dialog.contains(document.activeElement)) dialog.focus({ preventScroll: true });
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);

  /* Tab 在對話框內循環，不會跑到被遮住的頁面上 */
  const handleKeyDown = (event) => {
    onKeyDown?.(event);
    if (event.key !== "Tab" || event.defaultPrevented) return;
    const dialog = dialogRef.current;
    const items = [...dialog.querySelectorAll(FOCUSABLE)].filter((el) => el.getClientRects().length > 0);
    if (items.length === 0) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const heading = title != null && (
    <div className={styles.titleRow}>
      {icon}
      <h2 id={titleId} className={styles.title}>{title}</h2>
    </div>
  );
  const desc = description != null && (
    <p id={descId} className={styles.description}>{description}</p>
  );
  const footer = actions != null && (
    <div {...actionsProps} className={`${styles.actions} ${actionsProps?.className ?? ""}`}>
      {actions}
    </div>
  );

  const dialogClass = [
    styles.dialog,
    SIZE_CLASS[size] ?? styles.sizeSm,
    closeButton ? styles.withHeader : styles.compact,
    className,
  ].filter(Boolean).join(" ");

  return createPortal(
    <div
      className={`${styles.overlay} ${closing ? styles.overlayOut : ""}`}
      /* 用 mousedown 且只認遮罩本身：在對話框裡選字拖到外面放開，不會誤關 */
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) requestClose();
      }}
    >
      <Tag
        ref={dialogRef}
        className={dialogClass}
        role={role}
        aria-modal="true"
        aria-labelledby={title != null ? titleId : undefined}
        aria-describedby={description != null ? descId : undefined}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        {...rest}
      >
        {closeButton ? (
          <>
            <header className={styles.header}>
              {heading}
              <button
                type="button"
                className={styles.closeBtn}
                onClick={requestClose}
                disabled={busy}
                aria-label={t("Modal.close")}
                {...closeProps}
              >
                <MIcon name="close" size={20} />
              </button>
            </header>
            <div className={styles.body}>
              {desc}
              {children}
            </div>
            {footer}
          </>
        ) : (
          <>
            {heading}
            {desc}
            {children}
            {footer}
          </>
        )}
      </Tag>
    </div>,
    document.body,
  );
}
