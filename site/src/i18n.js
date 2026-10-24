/**
 * 每個語言頁建一個獨立的 i18next 實例，經 I18nextProvider 傳給頁面：
 * 預渲染在同一個行程裡依序畫三種語言，不能共用全域實例。
 * 語系檔很小，三種語言全部靜態打包，初始化是同步的。
 */
import i18next from "i18next";
import { DEFAULT_LANGUAGE, languageInfo } from "./languages";
import zhTW from "./locales/zh-TW.json";
import en from "./locales/en.json";
import ja from "./locales/ja.json";

const RESOURCES = {
  "zh-TW": { landing: zhTW },
  en: { landing: en },
  ja: { landing: ja },
};

export function createI18n(lang) {
  languageInfo(lang);
  const instance = i18next.createInstance();
  instance.init({
    resources: RESOURCES,
    lng: lang,
    fallbackLng: DEFAULT_LANGUAGE,
    ns: ["landing"],
    defaultNS: "landing",
    interpolation: { escapeValue: false },
    returnEmptyString: false,
    initAsync: false,
    react: { useSuspense: false },
  });
  return instance;
}
