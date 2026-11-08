import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./AccountSettingsPage.module.scss";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { useToast } from "../../../hooks/useToast";
import { useUnsavedChangesOptional } from "../../../contexts/UnsavedChangesContext";
import {
  FEEDBACK_CATEGORIES,
  FEEDBACK_CONTENT_MAX,
  FEEDBACK_CONTENT_MIN,
  FeedbackService,
} from "../../../services/feedback";

/**
 * 「關於」分頁右欄的使用者回饋：類型＋內容，送出後存進後端、管理員在「使用者回饋」頁看。
 * 不需要 GitHub 帳號、內容不公開。送出成功清空內容（類型保留），打到一半跳離會先確認。
 *
 * @param {string} appVersion 送出時一併記下的前端版本（建置資訊；沒有就不帶）
 */
export default function FeedbackCard({ appVersion }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const [category, setCategory] = useState(FEEDBACK_CATEGORIES[0].value);
  const [content, setContent] = useState("");
  const [invalid, setInvalid] = useState(false);
  const [sending, setSending] = useState(false);
  const textareaRef = useRef(null);

  /* 打了字還沒送出就算未儲存：側欄導覽、站內連結會先問；
     這個分頁也可能被單獨渲染（測試），沒有 Provider 就略過 */
  const unsaved = useUnsavedChangesOptional();
  const setDirty = unsaved?.setDirty;
  const dirty = content.trim().length > 0;
  useEffect(() => {
    if (!setDirty) return undefined;
    setDirty(dirty);
    return () => setDirty(false);
  }, [dirty, setDirty]);

  async function submit(e) {
    e.preventDefault();
    if (sending) return;
    const trimmed = content.trim();
    if (trimmed.length < FEEDBACK_CONTENT_MIN) {
      setInvalid(true);
      textareaRef.current?.focus();
      return;
    }
    setSending(true);
    try {
      await FeedbackService.submit({
        category,
        content: trimmed,
        app_version: appVersion || undefined,
      });
      setContent("");
      setInvalid(false);
      toast.success(t("AboutTab.feedbackSent"));
    } catch (err) {
      toast.error(err?.message ?? t("AboutTab.feedbackFailed"));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className={styles.card} data-guide="account-feedback">
      <div className={styles.aboutHead}>
        <h2 className={styles.cardTitle}>{t("AboutTab.feedbackTitle")}</h2>
      </div>
      <p className={styles.aboutLead}>{t("AboutTab.feedbackHint")}</p>
      <form className={styles.feedbackForm} onSubmit={submit} noValidate>
        <SegmentedControl
          options={FEEDBACK_CATEGORIES.map((c) => ({
            value: c.value,
            icon: c.icon,
            label: t(c.labelKey, { ns: "common" }),
          }))}
          value={category}
          onChange={setCategory}
          ariaLabel={t("AboutTab.feedbackCategory")}
        />
        <label className={styles.feedbackField}>
          <textarea
            ref={textareaRef}
            className={invalid ? styles.fieldInvalid : undefined}
            aria-label={t("AboutTab.feedbackContent")}
            aria-invalid={invalid}
            rows={5}
            maxLength={FEEDBACK_CONTENT_MAX}
            placeholder={t("AboutTab.feedbackPlaceholder")}
            value={content}
            disabled={sending}
            onChange={(e) => {
              setContent(e.target.value);
              if (invalid && e.target.value.trim().length >= FEEDBACK_CONTENT_MIN) setInvalid(false);
            }}
          />
          {invalid && (
            <em className={styles.fieldError}>
              {t("AboutTab.feedbackTooShort", { min: FEEDBACK_CONTENT_MIN })}
            </em>
          )}
        </label>
        <div className={styles.feedbackFoot}>
          <span className={styles.feedbackCount}>
            {content.length} / {FEEDBACK_CONTENT_MAX}
          </span>
          <button type="submit" className={styles.btnPrimary} disabled={sending}>
            {sending ? t("AboutTab.feedbackSending") : t("AboutTab.feedbackSubmit")}
          </button>
        </div>
      </form>
    </div>
  );
}
