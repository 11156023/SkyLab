import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import styles from "./AiFloatingChat.module.scss";

/** Display passively; activating the message is an explicit request to open the assistant. */
export default function PageHelpHint({ enabled, pageKey, userId, onActivate }) {
  const { t } = useTranslation("components");
  const [visible, setVisible] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const seen = useRef(new Set());
  const storageKey = userId && pageKey ? `skylab:page-help-hint:${userId}:${pageKey}` : null;

  useEffect(() => {
    if (!enabled) return undefined;
    // Let dialogs and spotlight tours finish before showing a secondary hint.
    const update = () => setBlocked(document.hidden || Boolean(document.querySelector('[aria-modal="true"]')));
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-modal"] });
    document.addEventListener("visibilitychange", update);
    return () => {
      observer.disconnect();
      document.removeEventListener("visibilitychange", update);
    };
  }, [enabled]);

  useEffect(() => {
    setVisible(false);
    if (!enabled || blocked || !storageKey || seen.current.has(storageKey)) return undefined;
    try {
      if (localStorage.getItem(storageKey) === "seen") return undefined;
    } catch { /* In-memory history still prevents repeats when storage is unavailable. */ }
    let hideTimer;
    const showTimer = window.setTimeout(() => {
      seen.current.add(storageKey);
      try { localStorage.setItem(storageKey, "seen"); } catch { /* Best-effort browser preference. */ }
      setVisible(true);
      hideTimer = window.setTimeout(() => setVisible(false), 8000);
    }, 1500);
    return () => {
      window.clearTimeout(showTimer);
      window.clearTimeout(hideTimer);
    };
  }, [enabled, blocked, storageKey]);

  if (!enabled || blocked || !visible) return null;
  return (
    <div className={styles.pageHelpHint}>
      <button type="button" className={styles.hintMessage} onClick={() => {
        setVisible(false);
        onActivate?.();
      }}>
        <span role="status">{t("AiFloatingChat.pageHelpHint")}</span>
      </button>
      <button type="button" className={styles.hintDismiss} onClick={() => setVisible(false)}
        aria-label={t("AiFloatingChat.dismissPageHelpHint")}>
        <MIcon name="close" size={15} />
      </button>
    </div>
  );
}
