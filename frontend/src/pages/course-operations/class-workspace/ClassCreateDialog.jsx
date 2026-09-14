import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { focusInvalidField } from "../../../utils/focusField";
import {
  BOOT_LEAD_OPTIONS,
  classSchedulePayload,
  createClassScheduleForm,
  SHUTDOWN_GRACE_OPTIONS,
} from "../classScheduleForm";
import styles from "../CourseOperations.module.scss";

const WEEKDAY_KEYS = [
  "ClassCreateDialog.weekdayMon",
  "ClassCreateDialog.weekdayTue",
  "ClassCreateDialog.weekdayWed",
  "ClassCreateDialog.weekdayThu",
  "ClassCreateDialog.weekdayFri",
  "ClassCreateDialog.weekdaySat",
  "ClassCreateDialog.weekdaySun",
];

export default function ClassCreateDialog({
  item = null,
  closing = false,
  onClose,
  onCreated,
  onUpdated,
}) {
  const { t } = useTranslation("teaching");
  const isEdit = Boolean(item);
  const [form, setForm] = useState(() => createClassScheduleForm(item));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [nameInvalid, setNameInvalid] = useState(false);
  const nameInputRef = useRef(null);

  useEffect(() => {
    function closeOnEscape(event) {
      if (event.key === "Escape" && !submitting) onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, submitting]);

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    if (!form.name.trim()) {
      setNameInvalid(true);
      focusInvalidField(nameInputRef.current);
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const payload = classSchedulePayload(form);
      const saved = isEdit
        ? await TeachingClassesService.update(item.id, payload)
        : await TeachingClassesService.create(payload);
      (isEdit ? onUpdated : onCreated)?.(saved);
    } catch (reason) {
      setError(
        reason?.message ??
          (isEdit
            ? t("ClassCreateDialog.saveFailed")
            : t("ClassCreateDialog.createFailed")),
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className={`${styles.createDialogOverlay} ${closing ? styles.createDialogOverlayOut : ""}`}
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !submitting) onClose();
      }}
    >
      <section
        className={styles.createDialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-class-title"
      >
        <header className={styles.createDialogHeader}>
          <h2 id="create-class-title">
            {isEdit ? t("ClassCreateDialog.editTitle") : t("ClassCreateDialog.createTitle")}
          </h2>
          <button
            type="button"
            className={styles.iconBtn}
            aria-label={t("ClassCreateDialog.closeAria")}
            disabled={submitting}
            onClick={onClose}
          >
            <MIcon name="close" size={19} />
          </button>
        </header>

        <form onSubmit={submit}>
          <div className={styles.createDialogBody}>
            <div className={styles.compactFormSection}>
              <h3>{t("ClassCreateDialog.sectionClassInfo")}</h3>
              <div className={styles.createFormGrid}>
                <label className={`${styles.field} ${styles.createNameField}`}>
                  <span>{t("ClassCreateDialog.fieldClassName")}</span>
                  <input
                    ref={nameInputRef}
                    className={nameInvalid ? styles.fieldInvalid : undefined}
                    value={form.name}
                    onChange={(event) => { update("name", event.target.value); setNameInvalid(false); }}
                    placeholder={t("ClassCreateDialog.classNamePlaceholder")}
                    autoFocus
                  />
                </label>
                <label className={styles.field}>
                  <span>{t("ClassCreateDialog.fieldTerm")}</span>
                  <input
                    value={form.term}
                    onChange={(event) => update("term", event.target.value)}
                  />
                </label>
                <label className={styles.field}>
                  <span>{t("ClassCreateDialog.fieldLocation")}</span>
                  <input
                    value={form.location}
                    onChange={(event) => update("location", event.target.value)}
                    placeholder={t("ClassCreateDialog.locationPlaceholder")}
                  />
                </label>
                <label className={styles.field}>
                  <span>{t("ClassCreateDialog.fieldStartDate")}</span>
                  <input
                    type="date"
                    value={form.startDate}
                    onChange={(event) =>
                      update("startDate", event.target.value)
                    }
                  />
                </label>
                <label className={styles.field}>
                  <span>{t("ClassCreateDialog.fieldEndDate")}</span>
                  <input
                    type="date"
                    value={form.endDate}
                    onChange={(event) => update("endDate", event.target.value)}
                  />
                </label>
              </div>
            </div>

            <div className={styles.compactFormSection}>
              <h3>{t("ClassCreateDialog.sectionFixedSchedule")}</h3>
              <div className={styles.createFormGrid}>
                <label className={styles.field}>
                  <span>{t("ClassCreateDialog.fieldWeekday")}</span>
                  <select
                    value={form.weekday}
                    onChange={(event) =>
                      update("weekday", Number(event.target.value))
                    }
                  >
                    {WEEKDAY_KEYS.map((labelKey, index) => (
                      <option key={labelKey} value={index}>
                        {t(labelKey)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className={styles.field}>
                  <span>{t("ClassCreateDialog.fieldClassTime")}</span>
                  <div className={styles.timePair}>
                    <input
                      type="time"
                      value={form.startTime}
                      onChange={(event) =>
                        update("startTime", event.target.value)
                      }
                    />
                    <i>{t("ClassCreateDialog.timeRangeSeparator")}</i>
                    <input
                      type="time"
                      value={form.endTime}
                      onChange={(event) => update("endTime", event.target.value)}
                    />
                  </div>
                </label>
                <label className={styles.field}>
                  <span>{t("ClassCreateDialog.fieldBootLead")}</span>
                  <select
                    value={form.bootLeadMinutes}
                    onChange={(event) =>
                      update("bootLeadMinutes", Number(event.target.value))
                    }
                  >
                    {BOOT_LEAD_OPTIONS.map((minutes) => (
                      <option key={minutes} value={minutes}>
                        {minutes === 0
                          ? t("ClassCreateDialog.bootLeadOnTime")
                          : t("ClassCreateDialog.bootLeadMinutesOption", { minutes })}
                      </option>
                    ))}
                  </select>
                </label>
                <label className={styles.field}>
                  <span>{t("ClassCreateDialog.fieldShutdownGrace")}</span>
                  <select
                    value={form.shutdownGraceMinutes}
                    onChange={(event) =>
                      update("shutdownGraceMinutes", Number(event.target.value))
                    }
                  >
                    {SHUTDOWN_GRACE_OPTIONS.map((minutes) => (
                      <option key={minutes} value={minutes}>
                        {minutes === 0
                          ? t("ClassCreateDialog.shutdownGraceImmediate")
                          : t("ClassCreateDialog.shutdownGraceMinutesOption", { minutes })}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
            {error && <p className={styles.errorMessage}>{error}</p>}
          </div>

          <footer className={styles.createDialogFooter}>
            <button
              type="button"
              className={styles.btnSecondary}
              disabled={submitting}
              onClick={onClose}
            >
              {t("ClassCreateDialog.cancelBtn")}
            </button>
            <button
              type="submit"
              className={styles.btnPrimary}
              disabled={submitting}
            >
              {submitting ? t("ClassCreateDialog.savingBtn") : isEdit ? t("ClassCreateDialog.saveChangesBtn") : t("ClassCreateDialog.createClassBtn")}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}
