/**
 * AvailabilityPanel — 月曆日期範圍選擇器版
 * Props:
 *   draft         { resource_type, cores, memory, disk_size?, rootfs_size?, gpu_required?, gpu_mapping_id? }
 *   startAt/endAt Current form range; keeps direct input, AI import, and calendar selection in sync
 *   onChange      ({ start_at: string|null, end_at: string|null }) => void  — fired only on calendar clicks
 *   onDataChange  (data: object|null) => void  — latest availability preview (or null) for the parent
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { VmRequestAvailabilityService } from "../../services/vmRequestAvailability";
import styles from "./AvailabilityPanel.module.scss";
import MIcon from "../MIcon";

const MONTH_KEYS = [
  "AvailabilityPanel.month1", "AvailabilityPanel.month2", "AvailabilityPanel.month3", "AvailabilityPanel.month4",
  "AvailabilityPanel.month5", "AvailabilityPanel.month6", "AvailabilityPanel.month7", "AvailabilityPanel.month8",
  "AvailabilityPanel.month9", "AvailabilityPanel.month10", "AvailabilityPanel.month11", "AvailabilityPanel.month12",
];
const DAY_HEADER_KEYS = [
  "AvailabilityPanel.daySun", "AvailabilityPanel.dayMon", "AvailabilityPanel.dayTue", "AvailabilityPanel.dayWed",
  "AvailabilityPanel.dayThu", "AvailabilityPanel.dayFri", "AvailabilityPanel.daySat",
];

/* picking state: "extend" = waiting for user to pick end date; "idle" = can start fresh */
const PICK_EXTEND = "extend";
const PICK_IDLE   = "idle";
const AVAILABILITY_DEBOUNCE_MS = 450;
const AVAILABILITY_CACHE_TTL_MS = 30_000;
const AVAILABILITY_CACHE_MAX = 50;
const availabilityCache = new Map();

function toDateStr(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function localDateAt(dateStr, hour, minute = 0, second = 0) {
  const [year, month, day] = String(dateStr || "").split("-").map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day, hour, minute, second, 0);
}

function dateStrFromValue(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : toDateStr(date);
}

function isDraftReady(draft) {
  if (!draft?.resource_type || !draft?.cores || !draft?.memory) return false;
  return draft.resource_type === "vm" ? Boolean(draft.disk_size) : Boolean(draft.rootfs_size);
}

/* 依失敗原因給主訊息 key；api.js 逾時丟 {status:408, timeout:true}、
   Proxmox 全斷後端回 502、連不上後端則沒有 status */
function availabilityErrorKey(error) {
  if (error?.timeout) return "AvailabilityPanel.errorTimeout";
  if (error?.status === 502) return "AvailabilityPanel.errorCluster";
  if (error && !error.status) return "AvailabilityPanel.errorNetwork";
  return "AvailabilityPanel.errorGeneric";
}

function cacheAvailability(key, data) {
  availabilityCache.set(key, { data, ts: Date.now() });
  while (availabilityCache.size > AVAILABILITY_CACHE_MAX) {
    availabilityCache.delete(availabilityCache.keys().next().value);
  }
}

export default function AvailabilityPanel({ draft, startAt, endAt, onChange, onDataChange }) {
  const { t } = useTranslation("components");
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);
  const [retryToken, setRetryToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false); // 已有月曆時的背景更新
  const dataRef = useRef(null);
  dataRef.current = data;
  const hasRequestedRef = useRef(false);

  const today = useMemo(() => new Date(), []);
  const todayStr = useMemo(() => toDateStr(today), [today]);
  const maxDate = useMemo(() => {
    const value = new Date(today);
    value.setDate(value.getDate() + 90);
    return value;
  }, [today]);
  const maxDateStr = useMemo(() => toDateStr(maxDate), [maxDate]);

  const [viewYear, setViewYear]   = useState(today.getFullYear());
  const [viewMonth, setViewMonth] = useState(today.getMonth());

  const [startDate, setStartDate] = useState(() => dateStrFromValue(startAt));
  const [endDate, setEndDate]     = useState(() => dateStrFromValue(endAt));
  const [hoverDate, setHoverDate] = useState(null);
  const [picking, setPicking]     = useState(PICK_IDLE);

  const onChangeRef = useRef(onChange);
  useEffect(() => { onChangeRef.current = onChange; }, [onChange]);

  const onDataChangeRef = useRef(onDataChange);
  useEffect(() => { onDataChangeRef.current = onDataChange; }, [onDataChange]);

  useEffect(() => {
    const nextStartDate = dateStrFromValue(startAt);
    const nextEndDate = dateStrFromValue(endAt);
    if (startDate === nextStartDate && endDate === nextEndDate) return;
    setStartDate(nextStartDate);
    setEndDate(nextEndDate);
    setPicking(PICK_IDLE);
  }, [startAt, endAt]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    onDataChangeRef.current?.(data);
  }, [data]);

  /* ── Fetch ── */
  const draftReady = isDraftReady(draft);
  const draftKey = draftReady
    ? `${draft.resource_type}|${draft.cores}|${draft.memory}|${draft.disk_size ?? ""}|${draft.rootfs_size ?? ""}|${draft.gpu_required ?? 0}|${draft.gpu_mapping_id ?? ""}|${draft.ostemplate ?? ""}|${draft.template_id ?? ""}`
    : null;

  useEffect(() => {
    if (!draftKey) {
      setData(null);
      setLoading(false);
      setRefreshing(false);
      return;
    }
    let cancelled = false;

    const cached = availabilityCache.get(draftKey);
    if (cached && Date.now() - cached.ts < AVAILABILITY_CACHE_TTL_MS) {
      setData(cached.data);
      setError(null);
      setLoading(false);
      setRefreshing(false);
      return;
    }

    const controller = new AbortController();
    /* 已有月曆時保留舊資料只標示「更新中」，不要清空後閃成骨架屏 */
    const hasStale = dataRef.current != null;
    if (hasStale) setRefreshing(true);
    else setLoading(true);
    setError(null);
    /* 送出過第一次請求後，之後每次規格異動一律等使用者停手再送：
       舊寫法在清空 data 後 debounce 會退化成 0ms，連續拖曳滑桿會對後端連發請求 */
    const delay = hasRequestedRef.current ? AVAILABILITY_DEBOUNCE_MS : 0;
    const timeoutId = window.setTimeout(() => {
      if (cancelled) return;
      hasRequestedRef.current = true;
      VmRequestAvailabilityService.preview(draft, { signal: controller.signal })
        .then((res) => {
          if (cancelled) return;
          cacheAvailability(draftKey, res);
          setData(res);
        })
        .catch((err) => {
          if (cancelled || err?.name === "AbortError") return;
          setError({ status: err?.status, timeout: Boolean(err?.timeout) });
        })
        .finally(() => {
          if (cancelled) return;
          setLoading(false);
          setRefreshing(false);
        });
    }, delay);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [draftKey, retryToken]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── Day map ── */
  const dayMap = useMemo(() => {
    const map = {};
    data?.days.forEach((d) => { map[d.date] = d; });
    return map;
  }, [data]);

  function getDayLevel(dateStr) {
    const day = dayMap[dateStr];
    if (!day) return null;
    const total     = day.slots.length;
    const available = day.slots.filter((s) => s.status === "available").length;
    const selectable = day.slots.filter((s) => s.status === "available" || s.status === "limited").length;
    if (selectable === 0) return "none";
    if (available / total >= 0.5) return "good";
    return "limited";
  }

  function isDateSelectable(dateStr) {
    const level = getDayLevel(dateStr);
    return Boolean(dateStr) && dateStr >= todayStr && dateStr <= maxDateStr && level && level !== "none";
  }

  useEffect(() => {
    if (!data || !startDate || !endDate) return;
    if (!isDateSelectable(startDate) || !isDateSelectable(endDate)) {
      setStartDate(null);
      setEndDate(null);
      setPicking(PICK_IDLE);
    }
  }, [data, startDate, endDate, todayStr]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── Calendar grid ── */
  const calendarDays = useMemo(() => {
    const first = new Date(viewYear, viewMonth, 1);
    const last  = new Date(viewYear, viewMonth + 1, 0);
    const days  = Array(first.getDay()).fill(null);
    for (let d = 1; d <= last.getDate(); d++) days.push(new Date(viewYear, viewMonth, d));
    return days;
  }, [viewYear, viewMonth]);

  function prevMonth() {
    if (viewYear === today.getFullYear() && viewMonth === today.getMonth()) return;
    if (viewMonth === 0) { setViewYear((y) => y - 1); setViewMonth(11); }
    else setViewMonth((m) => m - 1);
  }
  function nextMonth() {
    if (viewYear === maxDate.getFullYear() && viewMonth === maxDate.getMonth()) return;
    if (viewMonth === 11) { setViewYear((y) => y + 1); setViewMonth(0); }
    else setViewMonth((m) => m + 1);
  }

  const canGoPrevious = viewYear > today.getFullYear()
    || (viewYear === today.getFullYear() && viewMonth > today.getMonth());
  const canGoNext = viewYear < maxDate.getFullYear()
    || (viewYear === maxDate.getFullYear() && viewMonth < maxDate.getMonth());

  /* ── Notify parent ── */
  /* 只在使用者「點擊月曆」時通知表單。不能用 effect 監聽 startDate/endDate：
     那會把表單 prop 同步進來的變化也回推出去（拍平成 00:00–23:59 的回聲），
     蓋掉使用者在 datetime-local 輸入框選的時間。 */
  function notifyRange(nextStart, nextEnd) {
    if (!nextStart || !nextEnd) {
      onChangeRef.current?.({ start_at: null, end_at: null });
      return;
    }
    const start = nextStart === todayStr ? new Date() : localDateAt(nextStart, 0);
    const end = localDateAt(nextEnd, 23, 59, 59);
    onChangeRef.current?.({
      start_at: start?.toISOString() ?? null,
      end_at: end?.toISOString() ?? null,
    });
  }

  /* ── Day click ── */
  function handleDayClick(dateStr, level) {
    if (!level || level === "none") return;
    if (picking === PICK_IDLE || !startDate) {
      setStartDate(dateStr); setEndDate(dateStr);
      setPicking(PICK_EXTEND);
      notifyRange(dateStr, dateStr);
    } else {
      if (dateStr > startDate) {
        setEndDate(dateStr);
        setPicking(PICK_IDLE);
        notifyRange(startDate, dateStr);
      } else {
        // Same or earlier date: restart as single-day
        setStartDate(dateStr); setEndDate(dateStr);
        notifyRange(dateStr, dateStr);
      }
    }
  }

  /* ── Early returns ── */
  if (!draftReady) return (
    <div className={styles.root}>
      <p className={styles.hint}>{t("AvailabilityPanel.hintFillSpecFirst")}</p>
    </div>
  );
  if (loading) return (
    <div className={styles.root}>
      <div className={styles.skeletonWrap}>
        <div className={`${styles.skeleton} ${styles.skeletonCalendar}`} />
      </div>
    </div>
  );
  if (error || !data) return (
    <div className={styles.root}>
      <div className={styles.errorBox}>
        <div className={styles.errorText}>
          <span className={styles.errorTitle}>{t(availabilityErrorKey(error))}</span>
          <span className={styles.errorDesc}>{t("AvailabilityPanel.errorGuide")}</span>
        </div>
        <button
          type="button"
          className={styles.retryBtn}
          onClick={() => setRetryToken((token) => token + 1)}
        >
          <MIcon name="refresh" size={13} />
          {t("AvailabilityPanel.retry")}
        </button>
      </div>
    </div>
  );

  const isComplete     = startDate && endDate;
  const isHoverPreview = picking === PICK_EXTEND && Boolean(hoverDate) && hoverDate > startDate;
  const effectiveEnd   = isHoverPreview ? hoverDate : endDate;
  const hasRange       = picking === PICK_IDLE && Boolean(startDate) && Boolean(endDate) && startDate !== endDate;

  return (
    <div className={styles.root} aria-busy={refreshing || undefined}>

      {refreshing && (
        <p className={styles.refreshingHint} aria-live="polite">{t("AvailabilityPanel.refreshingHint")}</p>
      )}

      {/* ── Calendar ── */}
      <div className={`${styles.calendar} ${refreshing ? styles.calendarRefreshing : ""}`}>
        <div className={styles.calendarNav}>
          <button type="button" className={styles.calendarNavBtn} onClick={prevMonth} disabled={!canGoPrevious}>
            <MIcon name="chevron_left" size={18} />
          </button>
          <span className={styles.calendarTitle}>{t(MONTH_KEYS[viewMonth])} {viewYear}</span>
          <button type="button" className={styles.calendarNavBtn} onClick={nextMonth} disabled={!canGoNext}>
            <MIcon name="chevron_right" size={18} />
          </button>
        </div>
        <div className={styles.calendarGrid}>
          {DAY_HEADER_KEYS.map((key) => (
            <div key={key} className={styles.calendarDayHeader}>{t(key)}</div>
          ))}
          {calendarDays.map((d, i) => {
            if (!d) return <div key={`pad-${i}`} />;
            const dateStr  = toDateStr(d);
            const level    = getDayLevel(dateStr);
            const isPast   = dateStr < todayStr;
            const unavailable = isPast || dateStr > maxDateStr || !level || level === "none";
            const isStart   = dateStr === startDate;
            const isEnd     = dateStr === endDate;
            const inRange   = Boolean(startDate) && Boolean(effectiveEnd)
              && dateStr > startDate && dateStr < effectiveEnd;
            const isPreview = isHoverPreview && inRange;

            const dayClass = [
              styles.calendarDay,
              !unavailable && level === "good"    && styles.calendarDayGood,
              !unavailable && level === "limited" && styles.calendarDayLimited,
              isStart                          && styles.calendarDayStart,
              isEnd                            && styles.calendarDayEnd,
              isStart && hasRange              && styles.calendarDayStartBar,
              isEnd   && hasRange              && styles.calendarDayEndBar,
              inRange && !isPreview            && styles.calendarDayInRange,
              isPreview                        && styles.calendarDayPreview,
              unavailable                      && styles.calendarDayDisabled,
            ].filter(Boolean).join(" ");

            return (
              <button
                key={dateStr}
                type="button"
                disabled={unavailable}
                className={dayClass}
                onClick={() => handleDayClick(dateStr, level)}
                onMouseEnter={() => picking === PICK_EXTEND && setHoverDate(dateStr)}
                onMouseLeave={() => setHoverDate(null)}
              >
                <span className={styles.dayInner}>{d.getDate()}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Legend ── */}
      <div className={styles.legend}>
        {[
          { cls: styles.calendarDayGood,    labelKey: "AvailabilityPanel.legendAvailable" },
          { cls: styles.calendarDayLimited, labelKey: "AvailabilityPanel.legendLimited" },
          { cls: styles.calendarDayNone,    labelKey: "AvailabilityPanel.legendFull" },
        ].map(({ cls, labelKey }) => (
          <div key={labelKey} className={styles.legendItem}>
            <span className={`${styles.legendDot} ${cls}`} />
            <span>{t(labelKey)}</span>
          </div>
        ))}
      </div>

      {/* ── Summary ── */}
      {isComplete && (
        <div className={styles.summary}>
          <MIcon name="check_circle" size={14} />
          <span>
            {startDate}
            {startDate !== endDate ? ` → ${endDate}` : ""}
          </span>
        </div>
      )}

    </div>
  );
}
