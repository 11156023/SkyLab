import { Fragment } from "react";
import { useTranslation } from "react-i18next";
import { formatTime } from "../../../utils/formatDate";
import { ticketBarcode } from "./ticketBarcode";
import styles from "./CourseTicket.module.scss";

/* unscheduled：課程頁上沒有排課的課程（首頁的資料全來自課表，不會出現） */
const STATES = new Set(["now", "later", "available", "ended", "unscheduled"]);
const DAY = 86_400_000;

/* 課表的 session_date 是課程時區（預設台北）的日曆日，今天也用同一個時區取 */
function taipeiToday(now) {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now);
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return Date.UTC(Number(value.year), Number(value.month) - 1, Number(value.day));
}

/* 今天／明天／後天交給 Intl（不必另開翻譯字串），更遠的寫月日與星期 */
export function sessionDayLabel(sessionDate, now, lang) {
  const [year, month, day] = String(sessionDate ?? "").split("-").map(Number);
  if (!year || !month || !day) return "";
  const target = Date.UTC(year, month - 1, day);
  const days = Math.round((target - taipeiToday(now)) / DAY);
  if (days >= 0 && days <= 2) {
    const text = new Intl.RelativeTimeFormat(lang, { numeric: "auto" }).format(days, "day");
    return text.charAt(0).toLocaleUpperCase(lang) + text.slice(1);
  }
  return new Intl.DateTimeFormat(lang, {
    month: "numeric", day: "numeric", weekday: "short", timeZone: "UTC",
  }).format(target);
}

/**
 * 課程票券（首頁「目前加入的課堂」與「我的課程」頁共用）：整張卡是一張票。兩側缺口與撕線把票面（何時、何地、誰教）和票根分開，
 * 票根上的條碼就是練習進度——一條一題，做完的在前（見 ticketBarcode）。
 * 時間地點欄位來自 /courses/schedule；沒有的資料（例如未設定教室）就不畫那一格。
 *
 * @param {boolean} [demo]      導覽專用的模擬課程：虛線外框＋「安全模擬」標籤（導覽文案有提到這兩個）
 * @param {string}  [guide]     整張票的 data-guide
 * @param {string}  [openGuide] 票根的 data-guide（導覽的「點進課程」步驟點這裡）
 */
export default function CourseTicket({ path, onOpen, now = Date.now(), demo = false, guide, openGuide }) {
  const { t, i18n } = useTranslation("personal");
  const state = STATES.has(path.state) ? path.state : null;
  const statusText = demo ? t("StudentCoursesPage.guideDemoBadge")
    : state ? t(`CourseTicket.status.${state}`) : null;
  const day = sessionDayLabel(path.session_date, now, i18n.language);
  const start = formatTime(path.start_at, "");
  const end = formatTime(path.end_at, "");
  /* 老師沒有填姓名時後端回 email，這時不加稱謂 */
  const teacher = !path.teacher ? ""
    : path.teacher.includes("@") ? path.teacher
      : t("CourseTicket.teacher", { name: path.teacher });
  const fields = [
    day && { key: "date", label: t("CourseTicket.date"), value: day },
    start && end && { key: "time", label: t("CourseTicket.time"), value: `${start}–${end}` },
    path.location && { key: "room", label: t("CourseTicket.room"), value: path.location },
  ].filter(Boolean);
  const total = Math.max(0, Number(path.total_questions) || 0);
  const completed = Math.min(Math.max(0, Number(path.completed_questions) || 0), total);
  const bars = ticketBarcode(path.id, total, completed);

  return (
    <button type="button" className={styles.ticket} data-state={state ?? undefined} onClick={onOpen}
      data-demo={demo || undefined} data-guide={guide} data-guide-demo={demo ? "true" : undefined}>
      <span className={styles.paper}>
        <span className={styles.main}>
          {statusText && <span className={styles.status}>{statusText}</span>}
          <strong className={styles.title}>{path.title}</strong>
          {teacher && <span className={styles.teacher}>{teacher}</span>}
          {fields.length > 0 && (
            <span className={styles.fields}>
              {fields.map((field) => (
                <span key={field.key} className={styles.field}>
                  <span className={styles.fieldLabel}>{field.label}</span>
                  <span className={styles.fieldValue}>{field.value}</span>
                </span>
              ))}
            </span>
          )}
        </span>

        <span className={styles.stub} data-guide={openGuide}>
          {bars.length > 0 ? (
            <>
              <span className={styles.barcode} aria-hidden="true">
                {bars.map((bar, index) => (
                  <Fragment key={index}>
                    <span className={bar.done ? styles.barDone : styles.bar} style={{ flexGrow: bar.width }} />
                    {bar.gap > 0 && <span style={{ flexGrow: bar.gap }} />}
                  </Fragment>
                ))}
              </span>
              <span className={styles.count}>
                <strong>{t("StudentCoursesPage.questions", { completed, total })}</strong>
                <small>{completed >= total ? t("CourseTicket.allSolved") : t("CourseTicket.solved")}</small>
              </span>
            </>
          ) : (
            <span className={styles.empty}>{t("CourseTicket.noQuestions")}</span>
          )}
        </span>
      </span>
    </button>
  );
}
