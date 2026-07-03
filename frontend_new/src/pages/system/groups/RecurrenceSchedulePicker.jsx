import { useState } from "react";
import styles from "./RecurrenceSchedulePicker.module.scss";
import MIcon from "../../../components/MIcon";

/**
 * 批量建立資源的週期排程（RRULE）設定元件。
 * 預設模式（每週特定日 / 每天）涵蓋多數課堂情境，進階模式可直接編輯 RRULE 字串。
 * 透過 onChange 回傳 { recurrence_rule, recurrence_duration_minutes, schedule_timezone }，
 * 未啟用或規則無效時三個欄位皆為 null，呼叫端可直接判斷 recurrence_rule 是否要送出。
 */

const WEEKDAYS = [
  { code: "MO", label: "一" },
  { code: "TU", label: "二" },
  { code: "WE", label: "三" },
  { code: "TH", label: "四" },
  { code: "FR", label: "五" },
  { code: "SA", label: "六" },
  { code: "SU", label: "日" },
];

const TIMEZONES = ["Asia/Taipei", "UTC", "Asia/Tokyo", "America/Los_Angeles"];

const EMPTY = {
  recurrence_rule: null,
  recurrence_duration_minutes: null,
  schedule_timezone: null,
};

const EN_TO_CODE = { Mon: "MO", Tue: "TU", Wed: "WE", Thu: "TH", Fri: "FR", Sat: "SA", Sun: "SU" };
const CODE_TO_ZH = { MO: "一", TU: "二", WE: "三", TH: "四", FR: "五", SA: "六", SU: "日" };

function buildRule(s) {
  if (s.mode === "advanced") return s.advancedRule.trim();
  if (s.mode === "preset_daily") return `FREQ=DAILY;BYHOUR=${s.hour};BYMINUTE=${s.minute}`;
  const byDay = s.days.length ? s.days.join(",") : "FR";
  return `FREQ=WEEKLY;BYDAY=${byDay};BYHOUR=${s.hour};BYMINUTE=${s.minute}`;
}

function buildValue(s) {
  if (!s.enabled) return EMPTY;
  const rule = buildRule(s);
  return {
    recurrence_rule: rule || null,
    recurrence_duration_minutes: rule && s.durationHours > 0 ? s.durationHours * 60 : null,
    schedule_timezone: rule ? s.timezone : null,
  };
}

/** 解析預覽支援的 RRULE 子集（FREQ=WEEKLY/DAILY + BYDAY/BYHOUR/BYMINUTE），不支援回傳 null */
function parseRule(rule) {
  const parts = {};
  for (const seg of rule.split(";")) {
    const [k, v] = seg.split("=");
    if (k && v !== undefined) parts[k.trim().toUpperCase()] = v.trim().toUpperCase();
  }
  const freq = parts.FREQ;
  if (freq !== "WEEKLY" && freq !== "DAILY") return null;
  const hour = Number(parts.BYHOUR ?? 0);
  const minute = Number(parts.BYMINUTE ?? 0);
  if (!Number.isInteger(hour) || hour < 0 || hour > 23) return null;
  if (!Number.isInteger(minute) || minute < 0 || minute > 59) return null;
  let byday = null;
  if (freq === "WEEKLY") {
    byday = (parts.BYDAY ?? "").split(",").filter(Boolean);
    if (byday.length === 0 || byday.some((d) => !(d in CODE_TO_ZH))) return null;
  }
  return { freq, byday, hour, minute };
}

function tzParts(date, timeZone) {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone,
    weekday: "short",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const parts = {};
  for (const p of fmt.formatToParts(date)) parts[p.type] = p.value;
  return parts;
}

/** 以指定時區推算接下來 count 次執行時間；規則不支援預覽時回傳 null */
function nextOccurrences(rule, timeZone, count = 3) {
  const parsed = parseRule(rule);
  if (!parsed) return null;
  try {
    const results = [];
    const now = new Date();
    const startMinutes = parsed.hour * 60 + parsed.minute;
    // 掃 29 天：單一星期日的週規則要 3 次至少需要 22 天
    for (let i = 0; i < 29 && results.length < count; i++) {
      const p = tzParts(new Date(now.getTime() + i * 86400000), timeZone);
      const code = EN_TO_CODE[p.weekday];
      if (parsed.freq === "WEEKLY" && !parsed.byday.includes(code)) continue;
      // 今天的執行時間已過就跳過
      if (i === 0 && Number(p.hour) * 60 + Number(p.minute) >= startMinutes) continue;
      const hh = String(parsed.hour).padStart(2, "0");
      const mm = String(parsed.minute).padStart(2, "0");
      results.push(`${p.month}/${p.day}（${CODE_TO_ZH[code]}）${hh}:${mm}`);
    }
    return results;
  } catch {
    return null; // 無效時區
  }
}

function clampNumber(raw, min, max) {
  return Math.max(min, Math.min(max, Number(raw)));
}

export default function RecurrenceSchedulePicker({ onChange }) {
  const [state, setState] = useState({
    enabled: false,
    mode: "preset_weekly", // preset_weekly | preset_daily | advanced
    days: ["FR"],
    hour: 13,
    minute: 0,
    durationHours: 4,
    timezone: "Asia/Taipei",
    advancedRule: "",
  });

  function update(patch) {
    const next = { ...state, ...patch };
    setState(next);
    onChange(buildValue(next));
  }

  function toggleDay(code) {
    update({
      days: state.days.includes(code)
        ? state.days.filter((d) => d !== code)
        : [...state.days, code],
    });
  }

  const rule = state.enabled ? buildRule(state) : "";
  const preview = rule ? nextOccurrences(rule, state.timezone) : null;

  return (
    <div className={styles.picker}>
      <div className={styles.head}>
        <div className={styles.headText}>
          <span className={styles.title}>
            <MIcon name="schedule" size={16} />
            啟用週期排程
          </span>
          <span className={styles.desc}>到時間自動開機，課程結束後自動關機</span>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={state.enabled}
          aria-label="啟用週期排程"
          className={state.enabled ? `${styles.switch} ${styles.switchOn}` : styles.switch}
          onClick={() => update({ enabled: !state.enabled })}
        />
      </div>

      {state.enabled && (
        <>
          <label className={styles.field}>
            <span>排程模式</span>
            <select value={state.mode} onChange={(e) => update({ mode: e.target.value })}>
              <option value="preset_weekly">每週特定日</option>
              <option value="preset_daily">每天</option>
              <option value="advanced">進階 (RRULE)</option>
            </select>
          </label>

          {state.mode === "preset_weekly" && (
            <div className={styles.field}>
              <span>星期</span>
              <div className={styles.dayRow}>
                {WEEKDAYS.map((d) => (
                  <button
                    type="button"
                    key={d.code}
                    className={
                      state.days.includes(d.code)
                        ? `${styles.dayBtn} ${styles.dayBtnActive}`
                        : styles.dayBtn
                    }
                    onClick={() => toggleDay(d.code)}
                  >
                    {d.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {state.mode !== "advanced" ? (
            <div className={styles.timeGrid}>
              <label className={styles.field}>
                <span>開始時 (24h)</span>
                <input
                  type="number"
                  min={0}
                  max={23}
                  value={state.hour}
                  onChange={(e) => update({ hour: clampNumber(e.target.value, 0, 23) })}
                />
              </label>
              <label className={styles.field}>
                <span>分鐘</span>
                <input
                  type="number"
                  min={0}
                  max={59}
                  value={state.minute}
                  onChange={(e) => update({ minute: clampNumber(e.target.value, 0, 59) })}
                />
              </label>
              <label className={styles.field}>
                <span>持續 (小時)</span>
                <input
                  type="number"
                  min={1}
                  max={24}
                  value={state.durationHours}
                  onChange={(e) => update({ durationHours: clampNumber(e.target.value, 1, 24) })}
                />
              </label>
            </div>
          ) : (
            <>
              <label className={styles.field}>
                <span>RRULE</span>
                <textarea
                  value={state.advancedRule}
                  onChange={(e) => update({ advancedRule: e.target.value })}
                  placeholder="FREQ=WEEKLY;BYDAY=FR;BYHOUR=13;BYMINUTE=0"
                  rows={2}
                />
              </label>
              <label className={styles.field}>
                <span>持續 (小時)</span>
                <input
                  type="number"
                  min={1}
                  max={24}
                  value={state.durationHours}
                  onChange={(e) => update({ durationHours: clampNumber(e.target.value, 1, 24) })}
                />
              </label>
            </>
          )}

          <label className={styles.field}>
            <span>時區</span>
            <select value={state.timezone} onChange={(e) => update({ timezone: e.target.value })}>
              {TIMEZONES.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </select>
          </label>

          <div className={styles.preview}>
            <span className={styles.previewTitle}>
              <MIcon name="update" size={14} />
              接下來 3 次執行時間（{state.timezone}）
            </span>
            {preview && preview.length > 0 ? (
              <div className={styles.previewList}>
                {preview.map((item) => (
                  <span key={item} className={styles.previewChip}>
                    {item}
                  </span>
                ))}
              </div>
            ) : (
              <span className={styles.previewEmpty}>
                {state.mode === "advanced"
                  ? "無法預覽此 RRULE（僅支援 FREQ=WEEKLY / DAILY 預覽，仍會照規則送出）"
                  : "請完成排程設定以顯示預覽"}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
