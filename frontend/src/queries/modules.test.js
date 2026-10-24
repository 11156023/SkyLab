import { describe, expect, test } from "vitest";

/* 每個查詢模組都要能載入，且匯出 *Keys 與至少一個 use…Query；新模組加進來這裡就會被檢查 */
const modules = import.meta.glob("./*.js", { eager: true });

describe("queries modules", () => {
  const names = Object.keys(modules).filter((name) => !/\.test\.js$/.test(name) && !/queryClient\.js$/.test(name));

  test("every domain module exports keys and at least one query hook", () => {
    expect(names.length).toBeGreaterThan(0);
    for (const name of names) {
      const mod = modules[name];
      const keyExports = Object.keys(mod).filter((k) => /Keys$/.test(k));
      const hookExports = Object.keys(mod).filter((k) => /^use[A-Z].*Query$/.test(k));
      expect(keyExports.length, `${name} keys`).toBeGreaterThan(0);
      expect(hookExports.length, `${name} hooks`).toBeGreaterThan(0);
      for (const k of keyExports) expect(Object.isFrozen(mod[k]), `${name}.${k} frozen`).toBe(true);
    }
  });
});
