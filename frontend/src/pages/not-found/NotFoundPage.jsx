import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../components/MIcon";
import styles from "./NotFoundPage.module.scss";

/** 雲朵造型（三圓一矩形的聯集；外框陰影靠父層 drop-shadow 做在聯集輪廓上） */
function Cloud({ className }) {
  return (
    <g className={className}>
      <circle cx="-26" cy="4" r="22" />
      <circle cx="2" cy="-12" r="28" />
      <circle cx="30" cy="6" r="20" />
      <rect x="-40" y="6" width="84" height="20" rx="10" />
    </g>
  );
}

/** 航線：起點在畫面外左下，繞過雲朵後收在右上（終點 452,56，末段切線約 -47°） */
const FLIGHT_D = "M -16 196 C 60 214, 118 178, 168 146 S 260 74, 318 88 S 408 104, 452 56";
/**
 * 循環時間軸（9 秒一輪，無限重複）：
 * 0 ─ 28% 飛行（keyPoints 按路徑距離＋linear＝等速，與遮罩描繪同速同步）
 * 28% ─ 82% 停在終點浮動
 * 82% ─ 93% 淡出（蓋掉 SMIL 循環重置的瞬間跳位；重置點飛機在畫面外的航線起點）
 */
const CYCLE_DUR = "9s";
const FLIGHT_KEYTIMES = "0;0.28;1";
const FADE_VALUES = "1;1;0;0";
const FADE_KEYTIMES = "0;0.82;0.93;1";

/** SMIL 不理會 prefers-reduced-motion，減少動態時直接渲染完成狀態（飛機停在終點、航跡全顯） */
const prefersReducedMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/**
 * 404 頁：登入後開到不存在（或無權限）的路徑會落到這裡，不再靜默導回儀表板。
 * 紙飛機沿航線飛行、虛線航跡跟在後面浮現，停留浮動後淡出重飛（9 秒一輪循環）；
 * 雲朵浮沉與虛線流動為純 CSS，prefers-reduced-motion 時全部靜止。
 */
export default function NotFoundPage() {
  const { t } = useTranslation("common");
  const navigate = useNavigate();
  const reduced = prefersReducedMotion();

  return (
    <div className={styles.page}>
      <div className={styles.scene}>
        <svg
          className={styles.sky}
          viewBox="0 0 480 240"
          role="img"
          aria-hidden="true"
          focusable="false"
        >
          {!reduced && (
            <defs>
              {/* animateMotion 的參考路徑 */}
              <path id="nf-flight-path" d={FLIGHT_D} />
              {/* 遮罩沿路徑描繪，讓虛線航跡跟著飛機逐段浮現 */}
              <mask
                id="nf-trail-mask"
                maskUnits="userSpaceOnUse"
                x="-40"
                y="-20"
                width="560"
                height="300"
              >
                <path
                  d={FLIGHT_D}
                  pathLength="100"
                  fill="none"
                  stroke="#fff"
                  strokeWidth="8"
                  strokeLinecap="round"
                  strokeDasharray="100"
                  strokeDashoffset="100"
                >
                  <animate
                    attributeName="stroke-dashoffset"
                    values="100;0;0"
                    keyTimes={FLIGHT_KEYTIMES}
                    dur={CYCLE_DUR}
                    calcMode="linear"
                    repeatCount="indefinite"
                  />
                </path>
              </mask>
            </defs>
          )}

          {/* 背景淡雲：兩個深度、不同週期的漂移 */}
          <g transform="translate(88 52) scale(0.52)">
            <Cloud className={`${styles.cloudFaint} ${styles.driftSlow}`} />
          </g>
          <g transform="translate(396 176) scale(0.4)">
            <Cloud className={`${styles.cloudFaint} ${styles.driftSlower}`} />
          </g>

          {/* 航跡：畫在雲與數字之前，讓雲朵能遮住穿過的部分 */}
          <path
            className={styles.trail}
            d={FLIGHT_D}
            mask={reduced ? undefined : "url(#nf-trail-mask)"}
          >
            {!reduced && (
              <animate
                attributeName="opacity"
                values={FADE_VALUES}
                keyTimes={FADE_KEYTIMES}
                dur={CYCLE_DUR}
                repeatCount="indefinite"
              />
            )}
          </path>

          {/* 4 ─ 雲(0) ─ 4 */}
          <text className={styles.digit} x="120" y="162" textAnchor="middle">4</text>
          {/* 定位與動畫分兩層：CSS transform 動畫會覆寫同元素的 transform 屬性 */}
          <g transform="translate(240 118) scale(1.12)">
            <g className={styles.cloudZeroWrap}>
              <Cloud className={styles.cloudZero} />
            </g>
          </g>
          <text className={styles.digit} x="360" y="162" textAnchor="middle">4</text>

          {/* 紙飛機：循環沿航線飛行（機身朝 +x，rotate="auto" 會對齊切線方向） */}
          {reduced ? (
            <g transform="translate(452 56) rotate(-47)">
              <g className={styles.plane}>
                <path d="M -14 6 L 16 0 L -10 -8 L -8 -1 Z" />
                <path className={styles.planeFold} d="M -8 -1 L 16 0 L -9 4 Z" />
              </g>
            </g>
          ) : (
            <g>
              <animateMotion
                dur={CYCLE_DUR}
                rotate="auto"
                calcMode="linear"
                keyPoints="0;1;1"
                keyTimes={FLIGHT_KEYTIMES}
                repeatCount="indefinite"
              >
                <mpath href="#nf-flight-path" />
              </animateMotion>
              <animate
                attributeName="opacity"
                values={FADE_VALUES}
                keyTimes={FADE_KEYTIMES}
                dur={CYCLE_DUR}
                repeatCount="indefinite"
              />
              <g className={styles.planeWrap}>
                <g className={styles.plane}>
                  <path d="M -14 6 L 16 0 L -10 -8 L -8 -1 Z" />
                  <path className={styles.planeFold} d="M -8 -1 L 16 0 L -9 4 Z" />
                </g>
              </g>
            </g>
          )}
        </svg>

        <h2 className={styles.title}>{t("NotFoundPage.title")}</h2>
        <p className={styles.desc}>{t("NotFoundPage.desc")}</p>
        <button
          type="button"
          className={styles.btnPrimary}
          onClick={() => navigate("/dashboard", { replace: true })}
        >
          <MIcon name="home" size={16} />
          {t("NotFoundPage.backHome")}
        </button>
      </div>
    </div>
  );
}
