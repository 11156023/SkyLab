import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES } from "../../i18n";
import styles from "./RotatingWelcome.module.scss";

const HOLD_MS = 2100;
const FADE_MS = 400;

/* 系統設定「減少動態效果」時為 true，設定變動會即時跟上 */
function usePrefersReducedMotion() {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => typeof window !== "undefined" && Boolean(window.matchMedia?.(query).matches));
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return undefined;
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

/**
 * 歡迎語輪播（h1）：同一句歡迎語依序用每種支援語言顯示，還沒選語言的人也看得懂。
 * 初始化精靈與首次登入引導的歡迎畫面共用。
 *
 * - 從目前介面語言開始輪，換了介面語言就從那個語言重新開始
 * - 離場動畫只在換句前一刻才播：分頁在背景被節流時文字仍停在畫面上，不會變空白
 * - 螢幕閱讀器只讀目前介面語言那一句；系統要求減少動態效果時停在目前語言、不輪播
 *
 * @param {string} i18nKey   歡迎語的翻譯 key
 * @param {string} ns        翻譯 namespace，預設 login
 * @param {string} className h1 的樣式（字級、顏色由頁面決定）
 */
export default function RotatingWelcome({ i18nKey, ns = "login", className }) {
  const { t, i18n } = useTranslation(ns);
  const reducedMotion = usePrefersReducedMotion();
  const current = SUPPORTED_LANGUAGES.includes(i18n.language) ? i18n.language : DEFAULT_LANGUAGE;
  const greetings = useMemo(
    () => SUPPORTED_LANGUAGES.map((lang) => ({ lang, text: i18n.getFixedT(lang, ns)(i18nKey) })),
    [i18n, ns, i18nKey],
  );
  const startIndex = Math.max(0, greetings.findIndex((greeting) => greeting.lang === current));
  const [index, setIndex] = useState(startIndex);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    setIndex(startIndex);
    setLeaving(false);
  }, [startIndex]);

  useEffect(() => {
    if (reducedMotion) return undefined;
    const leaveTimer = setTimeout(() => setLeaving(true), HOLD_MS);
    const nextTimer = setTimeout(() => {
      setLeaving(false);
      setIndex((i) => (i + 1) % greetings.length);
    }, HOLD_MS + FADE_MS);
    return () => {
      clearTimeout(leaveTimer);
      clearTimeout(nextTimer);
    };
  }, [index, reducedMotion, greetings.length]);

  const shown = greetings[reducedMotion ? startIndex : index];
  return (
    <h1 className={className}>
      <span className={styles.srOnly}>{t(i18nKey)}</span>
      <span className={styles.rotator} aria-hidden="true">
        <span key={shown.lang} lang={shown.lang} className={`${styles.text} ${leaving ? styles.leaving : ""}`}>
          {shown.text}
        </span>
      </span>
    </h1>
  );
}
