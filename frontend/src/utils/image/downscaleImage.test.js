import { afterEach, beforeAll, expect, test, vi } from "vitest";
import i18n from "../../i18n";
import { downscaleImage } from "./downscaleImage";

/* 與 locale_changes 提交的英文字串一致；locales/en/services.json 補上後這段只是覆寫同值 */
const EN_READ_FAILED = "Couldn't read this image. Please choose a JPG, PNG or WebP file.";

beforeAll(() => {
  i18n.addResources("en", "services", { "image.readFailed": EN_READ_FAILED });
});

afterEach(async () => {
  vi.unstubAllGlobals();
  await i18n.changeLanguage("zh-TW");
});

test("讀不了的圖片（例如瀏覽器不支援的 HEIC）回傳目前語系的錯誤訊息", async () => {
  await i18n.changeLanguage("en");
  vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: vi.fn() });
  vi.stubGlobal("Image", class {
    set src(_value) {
      queueMicrotask(() => this.onerror?.());
    }
  });

  await expect(downscaleImage(new Blob(["x"]))).rejects.toThrow(EN_READ_FAILED);
});
