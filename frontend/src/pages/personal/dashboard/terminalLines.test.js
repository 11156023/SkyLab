import { expect, it } from "vitest";
import { TERMINAL_ROWS, buildTerminal, daysUntil, formatRelative, formatUptime } from "./terminalLines";

const GB = 1024 ** 3;
const HOUR = 3_600_000;
const now = new Date(2026, 8, 26, 12, 0).getTime();
const t = (key, options) => (options ? `${key}:${JSON.stringify(options)}` : key);
const dateOnly = (daysFromToday) => {
  const date = new Date(now);
  date.setDate(date.getDate() + daysFromToday);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
};
const keysOf = ({ rows }) => rows.map((row) => row.key ?? row.kind);
const build = (machine, view = machine.status) => buildTerminal(machine, { view, now, lang: "zh-TW", t });

const running = {
  vmid: 104, name: "ml-lab-03", type: "qemu", status: "running",
  guest_os: { pretty_name: "Ubuntu 24.04.1 LTS" }, usedAt: now - 3 * HOUR,
  uptime: 134 * 60, ip_address: "10.20.3.41", cpu: 0.18, maxcpu: 4, mem: 3.1 * GB, maxmem: 8 * GB,
};

it("prints a running machine's details, with usage as text-mode meters", () => {
  const terminal = build(running);
  expect(keysOf(terminal)).toEqual(["comment", "last", "uptime", "ip", "cpu", "memory"]);
  const [header, last, uptime, , cpu, memory] = terminal.rows;
  expect(header.text).toBe("# vm 104  Ubuntu 24.04.1 LTS");
  expect(last.value).toBe("3 小時前");
  expect(uptime.value).toBe("2 h 14 min");
  expect(cpu).toMatchObject({ share: 0.18, label: "18%", high: false });
  expect(memory).toMatchObject({ label: "3.1/8G", high: false });
  expect(terminal.footer).toBeNull();
});

it("leaves out whatever the API did not send instead of printing placeholders", () => {
  const terminal = build({ vmid: 5, name: "bare", type: "lxc", status: "running" });
  expect(keysOf(terminal)).toEqual(["comment"]);
  expect(terminal.rows[0].text).toBe("# lxc 5");
  expect(JSON.stringify(terminal)).not.toMatch(/undefined|NaN/);
});

it("puts a close expiry and a scheduled auto-stop first, dropping meters when out of room", () => {
  const autoStop = new Date(now);
  autoStop.setHours(18, 30);
  const terminal = build({ ...running, expiry_date: dateOnly(3), auto_stop_at: autoStop.toISOString(), auto_stop_reason: "idle" });
  expect(keysOf(terminal).slice(0, 4)).toEqual(["comment", "stop", "expires", "last"]);
  expect(terminal.rows).toHaveLength(TERMINAL_ROWS);
  expect(keysOf(terminal)).not.toContain("memory");
  expect(terminal.rows[1]).toMatchObject({ value: "18:30  LifecycleCard.reasonIdle", tone: "warn" });
  expect(terminal.rows[2].value).toContain('OverviewTab.expiryDaysLeft:{"count":3}');
});

it("skips far-off expiry dates and auto-stops that already passed", () => {
  const terminal = build({ ...running, expiry_date: dateOnly(30), auto_stop_at: new Date(now - HOUR).toISOString() });
  expect(keysOf(terminal)).not.toContain("expires");
  expect(keysOf(terminal)).not.toContain("stop");
});

it("keeps a stopped machine to what still matters and marks it powered off", () => {
  const terminal = build({ ...running, status: "stopped", expiry_date: dateOnly(0) });
  expect(keysOf(terminal)).toEqual(["comment", "expires", "last"]);
  expect(terminal.rows[1].value).toContain("OverviewTab.expiryToday");
  expect(terminal.footer).toMatchObject({ text: "○ powered off", tone: "dim" });
});

it("tells how long ago an expired machine ran out", () => {
  const terminal = build({ ...running, status: "expired", expiry_date: dateOnly(-5) });
  expect(terminal.rows[1]).toMatchObject({ key: "expired", tone: "danger" });
  expect(terminal.rows[1].value).toContain('OverviewTab.expiryExpired:{"count":5}');
});

it("prints the boot log while starting and a console prompt while connecting", () => {
  const booting = build({ ...running, type: "lxc" }, "starting");
  expect(booting.boot[0].text).toContain("lxc-start");
  expect(booting.boot.every((line) => line.kind === "log" || line.tone === "dim")).toBe(true);
  expect(booting.footer.cursor).toBe(true);
  expect(build(running, "connecting").footer).toMatchObject({ text: "$ console", cursor: true });
  expect(build({ ...running, status: "failed" }).rows[1]).toMatchObject({ kind: "log", tag: "FAIL" });
});

it("flags meters that are nearly full", () => {
  const [, , , , cpu, memory] = build({ ...running, cpu: 0.91, mem: 7.5 * GB }).rows;
  expect(cpu.high).toBe(true);
  expect(memory).toMatchObject({ high: true, label: "7.5/8G" });
});

it("formats relative times in the interface language", () => {
  expect(formatRelative(now - 20_000, now, "zh-TW")).toBe("現在");
  expect(formatRelative(now - 3 * 60_000, now, "ja")).toBe("3 分前");
  expect(formatRelative(now - 26 * HOUR, now, "en")).toBe("yesterday");
});

it("formats uptime and date-only expiry in local time", () => {
  expect(formatUptime(20)).toBe("<1 min");
  expect(formatUptime(45 * 60)).toBe("45 min");
  expect(formatUptime((3 * 24 + 4) * 3600)).toBe("3 d 4 h");
  expect(formatUptime(null)).toBeNull();
  expect(daysUntil(dateOnly(2), now)).toBe(2);
});

it("prints the approved time window when it blocks starting a stopped machine", () => {
  const stopped = { vmid: 484, type: "lxc", status: "stopped" };
  const ended = build({ ...stopped, start_blocked_reason: "window_ended", window_end_at: new Date(2026, 8, 9, 23, 59).toISOString() });
  expect(ended.rows[1]).toMatchObject({ key: "window", value: "9/9  HomeOverview.windowEnded", tone: "danger" });

  const upcoming = build({ ...stopped, start_blocked_reason: "window_not_started", window_start_at: new Date(2026, 9, 1, 9, 0).toISOString() });
  expect(upcoming.rows[1]).toMatchObject({ key: "window", value: "10/1 09:00  HomeOverview.windowStarts", tone: "warn" });

  expect(keysOf(build(stopped))).not.toContain("window");
});
