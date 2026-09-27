import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import styles from "./CrashState.module.scss";

/**
 * 畫面當掉（ErrorBoundary 攔到 render 錯誤）時的狀態畫面。
 * 插圖是一個應用程式視窗：其中一塊介面脫落掉下、留下紅色虛線缺口，接著彈回原位（6 秒一輪），
 * 對應「這一塊畫面壞了，重試會把它重新裝回去」。動畫全是 CSS，prefers-reduced-motion 時停在脫落的樣子。
 *
 * 錯誤訊息預設收合；展開後可以複製（含網址、時間與堆疊），方便回報給管理員。
 *
 * @param {Error}  error     攔到的錯誤
 * @param {string} [componentStack] React 提供的元件堆疊，只放進複製內容
 * @param {func}   onRetry   重新掛載出錯的畫面
 * @param {boolean} [fullPage] 根層 boundary 用：撐滿整個視窗（此時沒有側邊欄等外框）
 */
export default function CrashState({ error, componentStack, onRetry, fullPage = false }) {
  const { t } = useTranslation("common");
  /* idle／copied（已寫進剪貼簿）／selected（瀏覽器不給寫，改成選取文字讓使用者自己複製） */
  const [copyState, setCopyState] = useState("idle");
  const timerRef = useRef(null);
  const preRef = useRef(null);
  const message = error?.message ?? String(error);
  const copyLabel = copyState === "copied" ? t("ErrorBoundary.copied")
    : copyState === "selected" ? t("ErrorBoundary.selectedToCopy")
      : t("ErrorBoundary.copy");

  useEffect(() => () => window.clearTimeout(timerRef.current), []);

  async function copyDetails() {
    const report = [
      message,
      "",
      `URL: ${window.location.href}`,
      `Time: ${new Date().toISOString()}`,
      error?.stack ? `\n${error.stack}` : "",
      componentStack ? `\nComponent stack:${componentStack}` : "",
    ].join("\n").trim();
    let next = "copied";
    try {
      await navigator.clipboard.writeText(report);
    } catch {
      /* 瀏覽器不給寫剪貼簿（非安全連線、權限被擋）：選取畫面上的錯誤訊息，至少能自己按 Ctrl+C */
      if (preRef.current) window.getSelection()?.selectAllChildren(preRef.current);
      next = "selected";
    }
    setCopyState(next);
    window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setCopyState("idle"), next === "copied" ? 2000 : 4000);
  }

  return (
    <div role="alert" className={`${styles.page} ${fullPage ? styles.fullPage : ""}`}>
      <div className={styles.scene}>
        <svg className={styles.art} viewBox="0 0 360 210" aria-hidden="true" focusable="false">
          {/* 視窗 */}
          <rect className={styles.window} x="36" y="18" width="288" height="164" rx="14" />
          <line className={styles.titleRule} x1="36" y1="42" x2="324" y2="42" />
          <circle className={styles.dot} cx="56" cy="30" r="4" />
          <circle className={styles.dot} cx="70" cy="30" r="4" />
          <circle className={styles.dot} cx="84" cy="30" r="4" />

          {/* 其他介面區塊：側邊欄、頁首、一張卡片 */}
          <rect className={styles.block} x="52" y="56" width="56" height="110" rx="6" />
          <rect className={styles.line} x="60" y="68" width="38" height="5" rx="2.5" />
          <rect className={styles.line} x="60" y="80" width="30" height="5" rx="2.5" />
          <rect className={styles.line} x="60" y="92" width="34" height="5" rx="2.5" />
          <rect className={styles.block} x="120" y="56" width="188" height="14" rx="5" />
          <rect className={styles.block} x="120" y="80" width="88" height="86" rx="7" />
          <rect className={styles.line} x="130" y="94" width="52" height="6" rx="3" />
          <rect className={styles.line} x="130" y="106" width="38" height="6" rx="3" />
          <rect className={styles.bar} x="130" y="130" width="10" height="24" rx="2" />
          <rect className={styles.bar} x="146" y="122" width="10" height="32" rx="2" />
          <rect className={styles.bar} x="162" y="138" width="10" height="16" rx="2" />

          {/* 脫落後留下的缺口 */}
          <rect className={styles.slot} x="220" y="80" width="88" height="86" rx="7" />

          {/* 脫落的那一塊：以左上角為支點掉下來、再彈回去 */}
          <g className={styles.broken}>
            <rect className={styles.brokenCard} x="220" y="80" width="88" height="86" rx="7" />
            <rect className={styles.line} x="230" y="94" width="50" height="6" rx="3" />
            <rect className={styles.line} x="230" y="106" width="34" height="6" rx="3" />
            <path className={styles.crack} d="M 262 118 L 270 130 L 262 140 L 274 156" />
          </g>

          {/* 斷開瞬間的火花 */}
          <g className={styles.sparks}>
            <line x1="314" y1="74" x2="322" y2="64" />
            <line x1="318" y1="84" x2="330" y2="82" />
            <line x1="310" y1="68" x2="310" y2="58" />
          </g>
        </svg>

        <h2 className={styles.title}>{t("ErrorBoundary.title")}</h2>
        <p className={styles.desc}>{t("ErrorBoundary.desc")}</p>

        <div className={styles.actions}>
          <button type="button" className={styles.btnPrimary} onClick={onRetry}>
            <MIcon name="refresh" size={16} />
            {t("Error.retry")}
          </button>
          <button type="button" className={styles.btnSecondary} onClick={() => window.location.reload()}>
            {t("ErrorBoundary.reload")}
          </button>
        </div>

        <details className={styles.details}>
          <summary>{t("ErrorBoundary.details")}</summary>
          {/* 一列：訊息過長時截斷（完整內容在 title 與複製結果裡），右側是純圖示的複製鈕 */}
          <div className={styles.detailsRow}>
            <pre ref={preRef} title={message}>{message}</pre>
            <button
              type="button"
              className={styles.copyBtn}
              onClick={copyDetails}
              aria-label={copyLabel}
              title={copyLabel}
            >
              <MIcon name={copyState === "copied" ? "check" : copyState === "selected" ? "select_all" : "content_copy"} size={16} />
            </button>
            {/* 圖示鈕沒有文字，複製結果改由這裡唸給螢幕閱讀器 */}
            <span className={styles.srOnly} aria-live="polite">
              {copyState === "idle" ? "" : copyLabel}
            </span>
          </div>
        </details>
      </div>
    </div>
  );
}
