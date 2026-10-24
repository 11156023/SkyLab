import { beforeAll, describe, expect, test } from "vitest";

import i18n from "../../../i18n";
import { changeTypeLabel, specChangeLabel } from "./RequestReviewPage";

// en／ja 語系檔改為用到才載入；三語都要有譯文
beforeAll(() => i18n.loadLanguages(["en", "ja"]));

const tFor = (lng) => i18n.getFixedT(lng, "resource");

describe("規格調整的審核欄位", () => {
  test("變更類型顯示三語標籤，不是後端的 enum 值", () => {
    for (const lng of ["zh-TW", "en", "ja"]) {
      const t = tFor(lng);
      for (const type of ["cpu", "memory", "disk", "combined", "expiry"]) {
        const label = changeTypeLabel(type, t);
        expect(label, `${lng} ${type}`).not.toBe(type === "cpu" ? "cpu" : type);
        expect(label, `${lng} ${type}`).not.toContain("RequestReviewPage.");
      }
    }
    expect(changeTypeLabel("combined", tFor("zh-TW"))).toBe("多項規格");
    expect(changeTypeLabel("expiry", tFor("en"))).toBe("Expiry extension");
  });

  test("沒見過的類型照原樣顯示，沒有類型顯示 -", () => {
    const t = tFor("zh-TW");
    expect(changeTypeLabel("gpu", t)).toBe("gpu");
    expect(changeTypeLabel(null, t)).toBe("-");
  });

  test("記憶體跟建立申請一樣以 GB 顯示，箭頭與到期日一致", () => {
    const t = tFor("en");
    const text = specChangeLabel({ current_cpu: 2, requested_cpu: 4, current_memory: 16384, requested_memory: 32768 }, t);
    expect(text).toBe("CPU 2 → 4 / RAM 16.0 → 32.0 GB");
    expect(text).not.toContain("->");
  });

  test("沒有任何數值時退回變更類型的標籤", () => {
    expect(specChangeLabel({ change_type: "combined" }, tFor("zh-TW"))).toBe("多項規格");
  });
});
