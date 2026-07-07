/**
 * derivePrimaryShades.test.js
 * 驗證主色衍生色階：
 *   - 一般色：light 比主色亮、dark 比主色暗，色相不變
 *   - 極亮 / 極暗輸入：lightness 被 clamp 在可視範圍，且 light 恆亮於 dark
 *   - 色碼正規化與不合法輸入
 */

import { describe, expect, test } from "vitest";
import {
  deriveBackgroundPalettes,
  derivePrimaryShades,
  derivePrimaryTheme,
  hexToHsl,
  normalizeHex,
} from "./derivePrimaryShades";

const HEX6_RE = /^#[0-9a-f]{6}$/;

function lightnessOf(hex) {
  return hexToHsl(hex).l;
}

describe("derivePrimaryShades — 一般色", () => {
  test("預設主色 #5471bf：light 較亮、dark 較暗", () => {
    const { primary, light, dark } = derivePrimaryShades("#5471bf");
    expect(primary).toBe("#5471bf");
    expect(lightnessOf(light)).toBeGreaterThan(lightnessOf(primary));
    expect(lightnessOf(dark)).toBeLessThan(lightnessOf(primary));
  });

  test("色相維持不變", () => {
    const { primary, light, dark } = derivePrimaryShades("#e91e63");
    const baseHue = hexToHsl(primary).h;
    expect(hexToHsl(light).h).toBeCloseTo(baseHue, 0);
    expect(hexToHsl(dark).h).toBeCloseTo(baseHue, 0);
  });

  test("輸出皆為合法的 #rrggbb", () => {
    const { primary, light, dark } = derivePrimaryShades("#00FF7F");
    for (const hex of [primary, light, dark]) {
      expect(hex).toMatch(HEX6_RE);
    }
  });
});

describe("derivePrimaryShades — 極亮輸入", () => {
  test("純白 #ffffff：色階被 clamp，light 仍亮於 dark", () => {
    const { light, dark } = derivePrimaryShades("#ffffff");
    expect(light).toMatch(HEX6_RE);
    expect(dark).toMatch(HEX6_RE);
    // clamp 在 HSL 空間做，轉回 8-bit hex 會有 ±0.5% 的量化誤差
    expect(lightnessOf(light)).toBeLessThanOrEqual(94.5);
    expect(lightnessOf(dark)).toBeLessThan(lightnessOf(light));
  });

  test("接近純白 #fdfdfd：dark 明顯比主色暗", () => {
    const base = lightnessOf("#fdfdfd");
    const { dark } = derivePrimaryShades("#fdfdfd");
    expect(lightnessOf(dark)).toBeLessThan(base - 10);
  });
});

describe("derivePrimaryShades — 極暗輸入", () => {
  test("純黑 #000000：色階被 clamp，light 仍亮於 dark", () => {
    const { light, dark } = derivePrimaryShades("#000000");
    expect(light).toMatch(HEX6_RE);
    expect(dark).toMatch(HEX6_RE);
    // clamp 在 HSL 空間做，轉回 8-bit hex 會有 ±0.5% 的量化誤差
    expect(lightnessOf(dark)).toBeGreaterThanOrEqual(5.5);
    expect(lightnessOf(light)).toBeGreaterThan(lightnessOf(dark));
  });

  test("接近純黑 #050505：light 明顯比主色亮", () => {
    const base = lightnessOf("#050505");
    const { light } = derivePrimaryShades("#050505");
    expect(lightnessOf(light)).toBeGreaterThan(base + 10);
  });
});

describe("derivePrimaryTheme — 文字用色", () => {
  test("淺色模式文字壓在可讀範圍，即使主色是純白", () => {
    const { light } = derivePrimaryTheme("#ffffff");
    expect(lightnessOf(light.text)).toBeLessThanOrEqual(60.5);
    expect(lightnessOf(light.textPrimary)).toBeLessThanOrEqual(45.5);
  });

  test("深色模式文字維持高亮度，即使主色是純黑", () => {
    const { dark } = derivePrimaryTheme("#000000");
    expect(lightnessOf(dark.text)).toBeGreaterThanOrEqual(85);
    expect(lightnessOf(dark.textPrimary)).toBeGreaterThanOrEqual(90);
  });

  test("文字沿用主色色相", () => {
    const { light, dark } = derivePrimaryTheme("#e91e63");
    const baseHue = hexToHsl("#e91e63").h;
    // 高亮度下 8-bit hex 量化會讓色相偏移 1-2 度
    expect(Math.abs(hexToHsl(light.text).h - baseHue)).toBeLessThan(3);
    expect(Math.abs(hexToHsl(dark.text).h - baseHue)).toBeLessThan(3);
  });

  test("兩種模式都帶完整的 primary 色階", () => {
    const theme = derivePrimaryTheme("#2e7d32");
    for (const mode of [theme.light, theme.dark]) {
      expect(mode.primary).toBe("#2e7d32");
      expect(mode.primaryLight).toMatch(HEX6_RE);
      expect(mode.primaryDark).toMatch(HEX6_RE);
    }
  });
});

describe("derivePrimaryTheme — 介面用色（hover / 邊框 / 底色）", () => {
  test("淺色模式：hover / 邊框 / 底色為高亮度、次要文字在中間段", () => {
    const { light } = derivePrimaryTheme("#e91e63");
    for (const c of [light.hover, light.border, light.divider, light.bgBase]) {
      expect(lightnessOf(c)).toBeGreaterThanOrEqual(90);
    }
    expect(lightnessOf(light.textMuted)).toBeGreaterThan(45);
    expect(lightnessOf(light.textMuted)).toBeLessThan(70);
  });

  test("深色模式：hover / 邊框 / 底色壓暗", () => {
    const { dark } = derivePrimaryTheme("#e91e63");
    for (const c of [dark.hover, dark.border, dark.divider, dark.bgBase]) {
      expect(lightnessOf(c)).toBeLessThanOrEqual(25);
    }
  });

  test("主色上的文字依亮度自動選色：極亮主色配深字、一般主色配白字", () => {
    const bright = derivePrimaryTheme("#ffffff");
    expect(lightnessOf(bright.light.textOnPrimary)).toBeLessThan(25);
    expect(bright.light.textOnPrimary).toBe(bright.dark.textOnPrimary);

    const normal = derivePrimaryTheme("#5471bf");
    expect(normal.light.textOnPrimary).toBe("#ffffff");
  });

  test("flowBg 為帶透明度的 color-mix 值", () => {
    const { light, dark } = derivePrimaryTheme("#5471bf");
    expect(light.flowBg).toMatch(/^color-mix\(in srgb, #[0-9a-f]{6} \d+%, transparent\)$/);
    expect(dark.flowBg).toMatch(/^color-mix\(in srgb, #[0-9a-f]{6} \d+%, transparent\)$/);
  });
});

describe("deriveBackgroundPalettes — 柔和雙色 / 對角三色", () => {
  test("淺色套組為粉彩（高亮度）、深色套組壓暗", () => {
    const { duo, tri } = deriveBackgroundPalettes("#5471bf");
    for (const c of [...duo.light, ...tri.light]) {
      expect(lightnessOf(c)).toBeGreaterThanOrEqual(88);
    }
    for (const c of [...duo.dark, ...tri.dark]) {
      expect(lightnessOf(c)).toBeLessThanOrEqual(18);
    }
  });

  test("雙色 2 色、三色 3 色，且互不相同", () => {
    const { duo, tri } = deriveBackgroundPalettes("#e91e63");
    expect(new Set(duo.light).size).toBe(2);
    expect(new Set(tri.light).size).toBe(3);
  });

  test("極亮 / 極暗基準色仍產生有效色碼", () => {
    for (const base of ["#ffffff", "#000000"]) {
      const { duo, tri } = deriveBackgroundPalettes(base);
      for (const c of [...duo.light, ...duo.dark, ...tri.light, ...tri.dark]) {
        expect(c).toMatch(HEX6_RE);
      }
    }
  });
});

describe("色碼正規化", () => {
  test("縮寫 #abc 等同 #aabbcc", () => {
    expect(derivePrimaryShades("#abc")).toEqual(derivePrimaryShades("#aabbcc"));
  });

  test("大寫轉為小寫", () => {
    expect(normalizeHex("#5471BF")).toBe("#5471bf");
  });

  test("不合法輸入丟出 TypeError", () => {
    for (const bad of ["blue", "#12", "5471bf", "", null, undefined, 0x5471bf]) {
      expect(() => derivePrimaryShades(bad)).toThrow(TypeError);
    }
  });
});
