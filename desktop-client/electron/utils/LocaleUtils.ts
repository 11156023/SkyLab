type LocalizedText = { "zh-TW": string; "en-US": string; ja: string };

/**
 * 主程序少數直接給使用者看的字（系統匣選單、系統通知）依設定的介面語言挑選；
 * 其餘介面文字都在 src/lang，錯誤則由畫面依錯誤碼翻譯（見 core/BusinessError）。
 */
export const localize = (
  language: string | null | undefined,
  text: LocalizedText
): string =>
  language === "ja"
    ? text.ja
    : language === "zh-TW"
      ? text["zh-TW"]
      : text["en-US"];
