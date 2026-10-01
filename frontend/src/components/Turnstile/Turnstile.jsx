import { forwardRef, useContext, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ThemeContext } from "../../contexts/ThemeContext";
import styles from "./Turnstile.module.scss";

/*
 * Cloudflare Turnstile 機器人驗證框（登入、註冊）。
 * site key 由後端 /login/methods 提供；沒有 site key 時呼叫端不要渲染這個元件。
 * token 只能用一次：送出失敗後呼叫端要 ref.reset() 換一個新的。
 */

const SCRIPT_ID = "cf-turnstile-script";
const SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const LANGUAGES = { "zh-TW": "zh-tw", en: "en", ja: "ja" };
let scriptPromise;

function loadTurnstileScript() {
  if (window.turnstile) return Promise.resolve();
  if (!scriptPromise) {
    scriptPromise = new Promise((resolve, reject) => {
      const script = document.getElementById(SCRIPT_ID) ?? document.createElement("script");
      script.addEventListener("load", resolve, { once: true });
      script.addEventListener("error", reject, { once: true });
      if (!script.id) {
        script.id = SCRIPT_ID;
        script.src = SCRIPT_SRC;
        script.async = true;
        script.defer = true;
        document.head.appendChild(script);
      }
    }).catch((err) => {
      // 載入失敗（網路被擋等）：清掉快取與 script，下次掛載可以重試
      scriptPromise = undefined;
      document.getElementById(SCRIPT_ID)?.remove();
      throw err;
    });
  }
  return scriptPromise;
}

const Turnstile = forwardRef(function Turnstile({ siteKey, action, onToken }, ref) {
  const { t, i18n } = useTranslation("login");
  const theme = useContext(ThemeContext)?.theme ?? "auto";
  const language = LANGUAGES[i18n.language] ?? "auto";
  const containerRef = useRef(null);
  const widgetIdRef = useRef(null);
  const onTokenRef = useRef(onToken);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    onTokenRef.current = onToken;
  }, [onToken]);

  useImperativeHandle(
    ref,
    () => ({
      reset() {
        onTokenRef.current("");
        if (widgetIdRef.current != null) window.turnstile?.reset(widgetIdRef.current);
      },
    }),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    setLoadFailed(false);
    loadTurnstileScript()
      .then(() => {
        if (cancelled || !containerRef.current) return;
        widgetIdRef.current = window.turnstile.render(containerRef.current, {
          sitekey: siteKey,
          action,
          theme,
          language,
          size: "flexible",
          callback: (token) => onTokenRef.current(token),
          "expired-callback": () => onTokenRef.current(""),
          // 不回傳 true：讓 Turnstile 自己在框內顯示錯誤並重試
          "error-callback": () => onTokenRef.current(""),
        });
      })
      .catch(() => {
        if (!cancelled) setLoadFailed(true);
      });

    return () => {
      cancelled = true;
      if (widgetIdRef.current != null) {
        window.turnstile?.remove(widgetIdRef.current);
        widgetIdRef.current = null;
      }
      // 重新渲染（切換主題／語言）後舊 token 作廢，等新的驗證結果
      onTokenRef.current("");
    };
  }, [siteKey, action, theme, language]);

  if (loadFailed) {
    return (
      <p className={styles.loadFailed} role="alert">
        {t("LoginPage.turnstileLoadFailed")}
      </p>
    );
  }
  return <div ref={containerRef} className={styles.widget} />;
});

export default Turnstile;
