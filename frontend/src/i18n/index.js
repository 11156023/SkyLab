/**
 * i18n/index.js
 * i18next 初始化。命名空間對應 src/locales/<lang>/<namespace>.json。
 * 預設語言（zh-TW，也是 fallback）靜態打包；en／ja 由 lazyLocaleBackend 在
 * 切換到該語言時才以 dynamic import 載入（三語全部打包會讓每個人首次載入
 * 多下載約 700 KB）。changeLanguage() 會等資源載完才切換，呼叫端不必改。
 *
 * 使用方式：
 *   import { useTranslation } from "react-i18next";
 *   const { t } = useTranslation("resource");
 *   t("resource:someKey")  // 或帶 namespace 呼叫 useTranslation 後直接 t("someKey")
 *
 * 非 React 模組（如 services/*.js）：
 *   import i18n from "@/i18n";
 *   i18n.t("someKey", { ns: "services" });
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import commonZhTW from "../locales/zh-TW/common.json";
import landingZhTW from "../locales/zh-TW/landing.json";
import componentsZhTW from "../locales/zh-TW/components.json";
import servicesZhTW from "../locales/zh-TW/services.json";
import loginZhTW from "../locales/zh-TW/login.json";
import personalZhTW from "../locales/zh-TW/personal.json";
import resourceZhTW from "../locales/zh-TW/resource.json";
import aiZhTW from "../locales/zh-TW/ai.json";
import teachingZhTW from "../locales/zh-TW/teaching.json";
import systemZhTW from "../locales/zh-TW/system.json";
import networkZhTW from "../locales/zh-TW/network.json";

export const SUPPORTED_LANGUAGES = ["zh-TW", "en", "ja"];
export const DEFAULT_LANGUAGE = "zh-TW";
export const LANGUAGE_STORAGE_KEY = "skylab.lang";

export const NAMESPACES = [
  "common",
  "landing",
  "components",
  "services",
  "login",
  "personal",
  "resource",
  "ai",
  "teaching",
  "system",
  "network",
];

function loadStoredLanguage() {
  if (typeof window === "undefined") return DEFAULT_LANGUAGE;
  try {
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    return SUPPORTED_LANGUAGES.includes(stored) ? stored : DEFAULT_LANGUAGE;
  } catch {
    return DEFAULT_LANGUAGE;
  }
}

// 非預設語言的語系檔：vite 會把每個 JSON 拆成獨立 chunk，用到才下載
const lazyLocales = import.meta.glob(["../locales/en/*.json", "../locales/ja/*.json"]);

const lazyLocaleBackend = {
  type: "backend",
  init() {},
  read(language, namespace, callback) {
    const load = lazyLocales[`../locales/${language}/${namespace}.json`];
    if (!load) {
      // zh-TW 已靜態打包，其他沒有對應檔案的組合當成空資源（t() 會退回 fallback）
      callback(null, {});
      return;
    }
    load().then(
      (mod) => callback(null, mod.default ?? mod),
      (error) => callback(error, null),
    );
  },
};

/** 初始化（含使用者上次選的語言的資源）完成；main.jsx 等它再渲染，避免先閃一下中文 */
export const i18nReady = i18n.use(lazyLocaleBackend).use(initReactI18next).init({
  resources: {
    "zh-TW": {
      common: commonZhTW,
      landing: landingZhTW,
      components: componentsZhTW,
      services: servicesZhTW,
      login: loginZhTW,
      personal: personalZhTW,
      resource: resourceZhTW,
      ai: aiZhTW,
      teaching: teachingZhTW,
      system: systemZhTW,
      network: networkZhTW,
    },
  },
  partialBundledLanguages: true,
  lng: loadStoredLanguage(),
  fallbackLng: DEFAULT_LANGUAGE,
  supportedLngs: SUPPORTED_LANGUAGES,
  ns: NAMESPACES,
  defaultNS: "common",
  interpolation: { escapeValue: false },
  returnEmptyString: false,
});

/**
 * 把語系代碼收斂到支援清單內；不支援（或尚未初始化）時退回預設語言。
 * 元件內請傳 useTranslation() 拿到的 i18n.language，切換語言時才會跟著重新 render。
 */
export function currentLanguage(lang = i18n.language) {
  return SUPPORTED_LANGUAGES.includes(lang) ? lang : DEFAULT_LANGUAGE;
}

/** 切換語系並持久化到 localStorage（各處語言選單使用）；非預設語言會先載入語系檔 */
export function setLanguage(lang) {
  if (!SUPPORTED_LANGUAGES.includes(lang)) return undefined;
  const switched = i18n.changeLanguage(lang);
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, lang);
  } catch {
    // localStorage 不可用時（無痕模式等）僅本次 session 生效
  }
  return switched;
}

export default i18n;
