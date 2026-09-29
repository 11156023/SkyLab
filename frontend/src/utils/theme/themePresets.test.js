/**
 * themePresets.test.js
 * 驗證系統配色主題：
 *   - 每組主色對白字都達 4.5:1（按鈕白字、白底上的主色文字都要看得清楚）
 *   - id 不重複、色碼是小寫 #rrggbb、花色是存在的選項
 *   - 主題按鈕（底色＝主題背景、字色＝主題文字色）在明暗模式下文字都達 4.5:1
 *   - findActivePreset 三項全等才算套用中
 */

import { describe, expect, test } from "vitest";
import { THEME_PRESET_FAMILIES, findActivePreset, presetColors } from "./themePresets";

const allPresets = THEME_PRESET_FAMILIES.flatMap((family) => family.presets);

function luminance(hex) {
  const [r, g, b] = [1, 3, 5]
    .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

describe("THEME_PRESET_FAMILIES", () => {
  test.each(allPresets.map((p) => [p.id, p]))("%s 主色對白字達 4.5:1", (_id, preset) => {
    const ratio = (1 + 0.05) / (luminance(preset.primaryColor) + 0.05);
    expect(ratio).toBeGreaterThanOrEqual(4.5);
  });

  test("id 不重複，色碼與花色合法", () => {
    const ids = allPresets.map((p) => p.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const preset of allPresets) {
      expect(preset.primaryColor).toMatch(/^#[0-9a-f]{6}$/);
      expect(preset.backgroundColor).toMatch(/^(#[0-9a-f]{6})?$/);
      expect(["auto-gradient", "preset-2", "preset-3"]).toContain(preset.backgroundId);
    }
  });
});

describe("findActivePreset", () => {
  const ocean = allPresets.find((p) => p.id === "nature-ocean");

  test("三項全等（大小寫不拘）才算套用中", () => {
    const hit = findActivePreset({
      primaryColor: ocean.primaryColor.toUpperCase(),
      backgroundColor: ocean.backgroundColor.toUpperCase(),
      backgroundId: ocean.backgroundId,
    });
    expect(hit?.preset.id).toBe("nature-ocean");
    expect(hit?.family.id).toBe("nature");
  });

  test("改過任何一項就不算", () => {
    expect(findActivePreset({ ...ocean, backgroundId: "preset-2" })).toBeNull();
    expect(findActivePreset({ ...ocean, backgroundColor: "" })).toBeNull();
    expect(findActivePreset({ primaryColor: "#5471bf", backgroundColor: "", backgroundId: "preset-2" })).toBeNull();
  });

  test("系統預設外觀對應到「預設」主題", () => {
    const hit = findActivePreset({ primaryColor: "#5471bf", backgroundColor: "", backgroundId: "auto-gradient" });
    expect(hit?.preset.id).toBe("default-classic");
  });
});

const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

describe("presetColors", () => {
  test.each(allPresets.flatMap((p) => ["light", "dark"].map((mode) => [p.id, mode, p])))(
    "%s（%s）按鈕文字對每個背景色標都達 4.5:1",
    (_id, mode, preset) => {
      const { text, stops } = presetColors(preset, mode);
      for (const stop of stops) expect(contrast(text, stop)).toBeGreaterThanOrEqual(4.5);
    }
  );

  test("明暗模式各有一套底色，圓點是主色", () => {
    const candy = allPresets.find((p) => p.id === "candy-strawberry");
    const light = presetColors(candy, "light");
    const dark = presetColors(candy, "dark");
    expect(light.primary).toBe(candy.primaryColor);
    expect(light.background).not.toBe(dark.background);
  });
});
