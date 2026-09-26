/**
 * 首頁機器卡的終端輸出：把機器的零散資訊（VMID、系統、上次使用、運行時間、IP、
 * 用量、到期、自動關機）排成幾行「終端輸出」。這裡只做「資料 → 行」，
 * 外觀交給 MachineTerminal；沒有的欄位就不印那一行，不補假資料。
 * 終端的欄位名（last、uptime…）刻意維持英文當終端語彙，需要文字的值才走 i18n。
 */

export const TERMINAL_ROWS = 7;
/* 用量過這條線，格子與數字轉成警示色 */
export const USAGE_HIGH = 0.85;

const KEY_WIDTH = 8;
const GB = 1024 ** 3;
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/* 與 OverviewTab、LifecycleCard 共用同一組原因文案 */
const AUTO_STOP_REASON_KEYS = {
  window_grace: "LifecycleCard.reasonWindowGrace",
  practice_quota: "LifecycleCard.reasonPracticeQuota",
  ttl_expired: "LifecycleCard.reasonTtlExpired",
  idle: "LifecycleCard.reasonIdle",
};

/* 開機沒有真實進度可讀，逐行印出這段開機訊息當作「正在開」的回饋 */
const BOOT_LINES = {
  vm: [
    ["dim", "SeaBIOS (version 1.16.3)"],
    ["dim", "Booting from Hard Disk..."],
    ["ok", "Mounted /boot/efi."],
    ["ok", "Reached target Network."],
    ["ok", "Started ssh.service."],
    ["ok", "Reached target Login Prompts."],
  ],
  lxc: [
    ["dim", "lxc-start: starting container"],
    ["ok", "Started Journal Service."],
    ["ok", "Reached target Network."],
    ["ok", "Started ssh.service."],
    ["ok", "Reached target Login Prompts."],
  ],
};

export const padKey = (key) => key.padEnd(KEY_WIDTH);
const clamp01 = (value) => Math.min(1, Math.max(0, Number(value) || 0));
const pad2 = (n) => String(n).padStart(2, "0");
const shortDate = (date) => `${date.getMonth() + 1}/${date.getDate()}`;
const clock = (date) => `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;

/* expiry_date 是純日期字串（YYYY-MM-DD）；用本地時區拆解，避免 UTC 解析在時區邊界差一天（同 OverviewTab） */
function parseDateOnly(value) {
  const [y, m, d] = String(value).slice(0, 10).split("-").map(Number);
  return y && m && d ? new Date(y, m - 1, d) : new Date(value);
}

export function daysUntil(dateStr, now = Date.now()) {
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  return Math.round((parseDateOnly(dateStr).getTime() - today.getTime()) / DAY);
}

/** 「3 小時前」「昨天」：交給 Intl 依介面語系產生，不另開翻譯字串 */
export function formatRelative(timestamp, now, lang) {
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  const ago = now - timestamp;
  if (ago < MINUTE) return rtf.format(0, "second");
  if (ago < HOUR) return rtf.format(-Math.floor(ago / MINUTE), "minute");
  if (ago < DAY) return rtf.format(-Math.floor(ago / HOUR), "hour");
  return rtf.format(-Math.floor(ago / DAY), "day");
}

/** 終端語彙的運行時間：2 h 14 min、3 d 4 h */
export function formatUptime(seconds) {
  if (seconds == null || seconds < 0) return null;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return "<1 min";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours} h ${minutes % 60} min` : `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

function gib(bytes) {
  const value = bytes / GB;
  return value >= 10 || Number.isInteger(value) ? String(Math.round(value)) : value.toFixed(1);
}

function header(machine) {
  const type = machine.type === "lxc" ? "lxc" : "vm";
  const os = machine.guest_os?.pretty_name ?? machine.os_info ?? "";
  return { kind: "comment", text: `# ${type} ${machine.vmid ?? "—"}${os ? `  ${os}` : ""}` };
}

/* 只在七天內（或已過期）才值得佔一行 */
function expiryRow(machine, now, t) {
  if (!machine.expiry_date) return null;
  const days = daysUntil(machine.expiry_date, now);
  if (days > 7) return null;
  const when = days < 0
    ? t("OverviewTab.expiryExpired", { count: -days })
    : days === 0 ? t("OverviewTab.expiryToday") : t("OverviewTab.expiryDaysLeft", { count: days });
  return {
    kind: "kv",
    key: days < 0 ? "expired" : "expires",
    value: `${shortDate(parseDateOnly(machine.expiry_date))}  ${when}`,
    tone: days < 0 ? "danger" : "warn",
  };
}

/* 已排定的自動關機（閒置、時段結束、練習時數用完…），過去的時間不再提 */
function autoStopRow(machine, now, t) {
  if (!machine.auto_stop_at) return null;
  const at = new Date(machine.auto_stop_at);
  if (Number.isNaN(at.getTime()) || at.getTime() <= now) return null;
  const when = at.toDateString() === new Date(now).toDateString() ? clock(at) : `${shortDate(at)} ${clock(at)}`;
  const reasonKey = AUTO_STOP_REASON_KEYS[machine.auto_stop_reason];
  return { kind: "kv", key: "stop", value: reasonKey ? `${when}  ${t(reasonKey)}` : when, tone: "warn" };
}

/* 個人申請的核准使用時段擋住開機時（後端 start_blocked_reason），印出時段的起訖 */
function windowRow(machine, t) {
  if (machine.start_blocked_reason === "window_ended" && machine.window_end_at) {
    const end = new Date(machine.window_end_at);
    return { kind: "kv", key: "window", value: `${shortDate(end)}  ${t("HomeOverview.windowEnded")}`, tone: "danger" };
  }
  if (machine.start_blocked_reason === "window_not_started" && machine.window_start_at) {
    const start = new Date(machine.window_start_at);
    return { kind: "kv", key: "window", value: `${shortDate(start)} ${clock(start)}  ${t("HomeOverview.windowStarts")}`, tone: "warn" };
  }
  return null;
}

function lastRow(machine, now, lang) {
  if (!machine.usedAt) return null;
  return {
    kind: "kv",
    key: "last",
    value: formatRelative(machine.usedAt, now, lang),
    fresh: now - machine.usedAt < MINUTE,
  };
}

function meter(key, share, label) {
  return { kind: "meter", key, share, label, high: share >= USAGE_HIGH };
}

/**
 * view：running / connecting（執行中且正在開主控台）/ starting / stopped /
 *       provisioning / failed / expired / unknown
 * 回傳 rows（固定行）、boot（開機時逐行印出的訊息）、footer（貼底的一行）。
 * 警示（自動關機、到期）排在最前面，行數超過時先捨棄後面的用量。
 */
export function buildTerminal(machine, { view, now = Date.now(), lang = "zh-TW", t = (key) => key }) {
  const rows = [header(machine)];
  const last = lastRow(machine, now, lang);
  const expiry = expiryRow(machine, now, t);
  let boot = null;
  let footer = null;

  switch (view) {
    case "running":
    case "connecting": {
      rows.push(autoStopRow(machine, now, t), expiry, last);
      const uptime = formatUptime(machine.uptime);
      if (uptime) rows.push({ kind: "kv", key: "uptime", value: uptime });
      if (machine.ip_address) rows.push({ kind: "kv", key: "ip", value: machine.ip_address });
      if (machine.cpu != null) {
        const share = clamp01(machine.cpu);
        rows.push(meter("cpu", share, `${Math.round(share * 100)}%`));
      }
      if (machine.mem != null && machine.maxmem > 0) {
        rows.push(meter("memory", clamp01(machine.mem / machine.maxmem), `${gib(machine.mem)}/${gib(machine.maxmem)}G`));
      }
      if (view === "connecting") footer = { text: "$ console", cursor: true };
      break;
    }
    case "starting":
      boot = BOOT_LINES[machine.type === "lxc" ? "lxc" : "vm"].map(([tone, text]) => (
        tone === "ok" ? { kind: "log", tag: "OK", text } : { kind: "text", text, tone }
      ));
      footer = { text: "login: ", cursor: true };
      break;
    case "stopped":
      rows.push(windowRow(machine, t), expiry, last);
      footer = { text: "○ powered off", tone: "dim" };
      break;
    case "provisioning":
      rows.push({ kind: "text", text: "cloud-init  building…" });
      footer = { progress: true };
      break;
    case "failed":
      rows.push({ kind: "log", tag: "FAIL", text: "provisioning failed" }, last);
      footer = { text: "○ unavailable", tone: "dim" };
      break;
    case "expired":
      rows.push(expiry ?? { kind: "kv", key: "status", value: "expired", tone: "danger" }, last);
      footer = { text: "○ expired", tone: "dim" };
      break;
    default:
      rows.push({ kind: "kv", key: "status", value: "unknown", tone: "dim" }, last);
  }

  const budget = TERMINAL_ROWS - (footer ? 1 : 0);
  return { rows: rows.filter(Boolean).slice(0, budget), boot, footer };
}
