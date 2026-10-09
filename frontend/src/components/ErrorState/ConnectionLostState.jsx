import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import styles from "./ConnectionLostState.module.scss";

/**
 * 連不到伺服器（登入前的服務檢查失敗、後端沒回應）時的狀態畫面。
 * 插圖比照 CrashState／404 頁的語言：左邊是 SkyLab 的雲、右邊是瀏覽器視窗，中間的線插頭拔開了。
 * 待機時插頭每 6 秒往插座推一次、碰到時閃一下紅色火花又彈開（＝連不上）；
 * 按「重新連線」後插頭插回去、線上跑主色的流動虛線（＝正在重試）。
 * 動畫全是 CSS，prefers-reduced-motion 時停在拔開的樣子。
 *
 * @param {func}    onRetry    重新連線
 * @param {boolean} [retrying] 重試中：插頭接上、按鈕停用
 * @param {boolean} [fullPage] 撐滿整個視窗（登入前沒有側邊欄等外框）
 */
export default function ConnectionLostState({ onRetry, retrying = false, fullPage = false }) {
  const { t } = useTranslation("common");

  return (
    <div
      role={retrying ? "status" : "alert"}
      className={`${styles.page} ${fullPage ? styles.fullPage : ""} ${retrying ? styles.retrying : ""}`}
    >
      <div className={styles.scene}>
        <svg className={styles.art} viewBox="0 0 360 210" aria-hidden="true" focusable="false">
          {/* SkyLab 的雲（同 404 頁的雲朵造型：三圓一矩形），上面三顆狀態燈 */}
          <g className={styles.cloud} transform="translate(96 92) scale(1.15)">
            <circle cx="-26" cy="4" r="22" />
            <circle cx="2" cy="-12" r="28" />
            <circle cx="30" cy="6" r="20" />
            <rect x="-40" y="6" width="84" height="20" rx="10" />
          </g>
          <rect className={styles.light} x="76" y="100" width="10" height="5" rx="2.5" />
          <rect className={styles.light} x="91" y="100" width="10" height="5" rx="2.5" />
          <rect className={styles.lightOff} x="106" y="100" width="10" height="5" rx="2.5" />

          {/* 瀏覽器視窗（同 CrashState 的視窗：純白、淡主色區塊） */}
          <rect className={styles.window} x="212" y="52" width="116" height="92" rx="12" />
          <line className={styles.titleRule} x1="212" y1="70" x2="328" y2="70" />
          <circle className={styles.dot} cx="226" cy="61" r="3.5" />
          <circle className={styles.dot} cx="238" cy="61" r="3.5" />
          <circle className={styles.dotAlert} cx="250" cy="61" r="3.5" />
          <rect className={styles.block} x="224" y="80" width="92" height="12" rx="4" />
          <rect className={styles.line} x="224" y="100" width="60" height="6" rx="3" />
          <rect className={styles.line} x="224" y="112" width="44" height="6" rx="3" />
          <rect className={styles.line} x="224" y="124" width="52" height="6" rx="3" />

          {/* 雲那一側的線＋插座（固定不動） */}
          <path className={styles.cable} d="M 100 124 C 100 152, 116 166, 146 166" />
          <rect className={styles.socket} x="144" y="153" width="26" height="26" rx="6" />
          <rect className={styles.socketHole} x="164" y="159" width="4" height="5" rx="1.5" />
          <rect className={styles.socketHole} x="164" y="168" width="4" height="5" rx="1.5" />

          {/* 視窗那一側的線：固定段從 245 開始，跟著插頭動的那段延伸到 260，位移 15px 時剛好接上、看不出接縫 */}
          <path className={styles.cable} d="M 245 166 C 266 166, 274 160, 274 144" />

          {/* 斷開的缺口：紅色虛線框，只在拔開時出現 */}
          <rect className={styles.gap} x="171" y="155" width="12" height="22" rx="3" />

          {/* 插頭（往左推 15px、插腳插進插座孔，再彈回來） */}
          <g className={styles.plug}>
            <rect className={styles.prong} x="184" y="159" width="12" height="5" rx="2" />
            <rect className={styles.prong} x="184" y="168" width="12" height="5" rx="2" />
            <rect className={styles.plugBody} x="193" y="152" width="28" height="28" rx="7" />
            <line className={styles.plugGrip} x1="203" y1="159" x2="203" y2="173" />
            <line className={styles.plugGrip} x1="211" y1="159" x2="211" y2="173" />
            <path className={styles.cable} d="M 221 166 L 260 166" />
          </g>

          {/* 重試中：插頭接上後線上跑的流動虛線 */}
          <path className={styles.flow} d="M 100 124 C 100 152, 116 166, 146 166 L 245 166 C 266 166, 274 160, 274 144" />

          {/* 碰到插座那一下的火花 */}
          <g className={styles.sparks}>
            <line x1="174" y1="148" x2="169" y2="139" />
            <line x1="179" y1="146" x2="181" y2="136" />
            <line x1="175" y1="184" x2="170" y2="193" />
          </g>
        </svg>

        <h2 className={styles.title}>{t("App.connectionUnavailable")}</h2>
        <p className={styles.desc}>{t("App.connectionUnavailableDesc")}</p>

        <div className={styles.actions}>
          <button type="button" className={styles.btnPrimary} disabled={retrying} onClick={onRetry}>
            <MIcon name="refresh" size={16} spin={retrying} />
            {retrying ? t("App.retrying") : t("App.retryConnect")}
          </button>
        </div>
      </div>
    </div>
  );
}
