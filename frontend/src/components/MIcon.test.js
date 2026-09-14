import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * MIcon 用 ligature 顯示圖示：名稱寫錯不會報錯，只會靜靜渲染成一個豆腐方塊。
 * 「監控」分頁就是這樣壞了很久（icon: "monitoring" 不在這套字型裡）。
 */
const FONT_TYPES = "node_modules/@material-design-icons/font/index.d.ts";

function availableIcons() {
  return new Set(readFileSync(FONT_TYPES, "utf8").match(/"[a-z0-9_]+"/g).map((token) => token.slice(1, -1)));
}

function sourceFiles(dir, found = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) sourceFiles(path, found);
    else if (/\.jsx?$/.test(entry.name) && !/\.test\.jsx?$/.test(entry.name)) found.push(path);
  }
  return found;
}

describe("MIcon 圖示名稱", () => {
  it("每個用到的名稱都存在於 material-icons-outlined", () => {
    const available = availableIcons();
    const unknown = new Map();

    for (const file of sourceFiles("src")) {
      const source = readFileSync(file, "utf8");
      const names = [
        ...source.matchAll(/<MIcon\s[^>]*name="([a-z0-9_]+)"/g),
        ...source.matchAll(/\bicon:\s*"([a-z0-9_]+)"/g),
      ].map((match) => match[1]);
      for (const name of names) {
        if (available.has(name)) continue;
        if (!unknown.has(name)) unknown.set(name, file);
      }
    }

    expect(Object.fromEntries(unknown)).toEqual({});
  });
});
