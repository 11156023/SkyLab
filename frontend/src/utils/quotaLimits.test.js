import { describe, expect, it } from "vitest";
import { growthRange, quotaRemaining, sliderRange, sliderTicks, snapToRange } from "./quotaLimits";

const usage = (quota, used = {}) => ({
  used_cpu_cores: used.cores ?? 0,
  used_memory_mb: used.memory ?? 0,
  used_disk_gb: used.disk ?? 0,
  used_instances: used.instances ?? 0,
  quota: {
    max_cpu_cores: quota.cores ?? 0,
    max_memory_mb: quota.memory ?? 0,
    max_disk_gb: quota.disk ?? 0,
    max_instances: quota.instances ?? 0,
  },
});

describe("quotaRemaining", () => {
  it("上限扣掉已用（已用含待審申請單，由後端算好）", () => {
    expect(quotaRemaining(usage(
      { cores: 8, memory: 16384, disk: 100, instances: 5 },
      { cores: 6, memory: 4096, disk: 30, instances: 2 },
    ))).toEqual({ cores: 2, memoryMb: 12288, diskGb: 70, instances: 3 });
  });

  it("上限 0 是無限制，回 null", () => {
    expect(quotaRemaining(usage({ cores: 0, memory: 0, disk: 0, instances: 0 }, { cores: 4 })))
      .toEqual({ cores: null, memoryMb: null, diskGb: null, instances: null });
  });

  it("已超用時剩餘量為 0，不會是負數", () => {
    expect(quotaRemaining(usage({ cores: 4 }, { cores: 6 })).cores).toBe(0);
  });

  it("還沒載入或載入失敗時全部當無限制（由後端兜底）", () => {
    expect(quotaRemaining(null).cores).toBeNull();
  });
});

describe("sliderRange", () => {
  it("沒有配額時維持表單原本的範圍", () => {
    expect(sliderRange({ min: 1, max: 8 })).toEqual({ min: 1, max: 8, quotaCapped: false, short: false });
  });

  it("剩餘量小於原本上限時以剩餘量為上限", () => {
    expect(sliderRange({ min: 1, max: 8, remaining: 3 }))
      .toEqual({ min: 1, max: 3, quotaCapped: true, short: false });
  });

  it("剩餘量大於原本上限時不放寬", () => {
    expect(sliderRange({ min: 1, max: 8, remaining: 20 }).max).toBe(8);
  });

  it("上限往下對齊步進，送出的值才不會超過剩餘量", () => {
    expect(sliderRange({ min: 512, max: 32768, step: 512, remaining: 5000 }).max).toBe(4608);
  });

  it("剩餘量不到下限時標記不足，滑桿停在下限", () => {
    expect(sliderRange({ min: 20, max: 500, remaining: 10 }))
      .toEqual({ min: 20, max: 20, quotaCapped: true, short: true });
  });
});

describe("growthRange", () => {
  it("上限是目前值加上剩餘配額", () => {
    expect(growthRange({ min: 1, max: 32, current: 2, remaining: 3 }))
      .toEqual({ min: 1, max: 5, quotaCapped: true, exhausted: false });
  });

  it("沒有配額限制或剩餘很多時維持表單原本上限", () => {
    expect(growthRange({ min: 1, max: 32, current: 2 }).max).toBe(32);
    expect(growthRange({ min: 1, max: 32, current: 2, remaining: 100 }).max).toBe(32);
  });

  it("記憶體增量往下對齊步進", () => {
    expect(growthRange({ min: 512, max: 65536, step: 512, current: 2048, remaining: 1500 }).max)
      .toBe(3072);
  });

  it("剩餘 0 時只能維持或調小", () => {
    expect(growthRange({ min: 1, max: 32, current: 4, remaining: 0 }))
      .toEqual({ min: 1, max: 4, quotaCapped: true, exhausted: true });
  });

  it("目前值已超過表單上限時，上限至少容得下目前值", () => {
    expect(growthRange({ min: 1, max: 32, current: 40, remaining: 0 }).max).toBe(40);
  });
});

describe("sliderTicks", () => {
  it("沒被配額壓縮時刻度跟原本一樣", () => {
    const ticks = sliderTicks([1, 2, 4, 6, 8], { min: 1, max: 8 });
    expect(ticks.map((tick) => tick.value)).toEqual([1, 2, 4, 6, 8]);
    expect(ticks.at(-1).left).toBe(100);
  });

  it("上限縮小時只留範圍內的刻度，最後一格是上限", () => {
    const ticks = sliderTicks([1, 2, 4, 6, 8], { min: 1, max: 5 });
    expect(ticks.map((tick) => tick.value)).toEqual([1, 2, 4, 5]);
    expect(ticks[2].left).toBe(75);
  });

  it("離上限太近的刻度拿掉，避免標籤重疊", () => {
    const ticks = sliderTicks([1024, 8192], { min: 512, max: 8704 });
    expect(ticks.map((tick) => tick.value)).toEqual([1024, 8704]);
  });

  it("上限等於下限時只有一格", () => {
    expect(sliderTicks([1, 2], { min: 1, max: 1 })).toEqual([{ value: 1, label: "1", left: 0 }]);
  });
});

describe("snapToRange", () => {
  const range = { min: 20, max: 500 };
  it("範圍內原值不動，超出就壓回上下限", () => {
    expect(snapToRange(30, range)).toBe(30);
    expect(snapToRange(3, range)).toBe(20);
    expect(snapToRange(9999, range)).toBe(500);
  });
  it("以 min 為基準對齊步進", () => {
    expect(snapToRange(2.3, { min: 0.5, max: 64, step: 0.5 })).toBe(2.5);
    expect(snapToRange(2.2, { min: 0.5, max: 64, step: 0.5 })).toBe(2);
    expect(snapToRange(700, { min: 512, max: 65536, step: 512 })).toBe(512);
    expect(snapToRange(800, { min: 512, max: 65536, step: 512 })).toBe(1024);
  });
  it("上限沒對齊步進時，對齊後超過上限仍停在上限", () => {
    expect(snapToRange(10, { min: 1, max: 7.3, step: 2 })).toBe(7);
    expect(snapToRange(6.3, { min: 1, max: 6.3, step: 2 })).toBe(6.3);
  });
  it("範本預設記憶體不是 512 MB 的倍數時對齊到最近的步進，GB 數字框才不會出現小數", () => {
    const memory = { min: 512, max: 32768, step: 512 };
    expect(snapToRange(1000, memory)).toBe(1024);
    expect(snapToRange(3000, memory)).toBe(3072);
    expect(snapToRange(1000, memory) / 1024).toBe(1);
    /* 配額把上限壓到 2560 時，3000 不是只壓回上限，而是落在範圍內最近的步進 */
    expect(snapToRange(3000, { ...memory, max: 2560 })).toBe(2560);
  });
  it("帶入的磁碟低於範本下限時抬到下限（克隆只能放大），超過上限壓回上限", () => {
    expect(snapToRange(20, { min: 32, max: 500, step: 1 })).toBe(32);
    expect(snapToRange(20.5, { min: 20, max: 500, step: 1 })).toBe(21);
    expect(snapToRange(600, { min: 20, max: 500, step: 1 })).toBe(500);
  });
  it("配額不足時上限等於下限，任何值都停在下限", () => {
    expect(snapToRange(2048, { min: 512, max: 512, step: 512 })).toBe(512);
  });
});
