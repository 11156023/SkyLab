/**
 * i18n/index.js
 * i18next 初始化。語系資源以靜態 import 打包（無執行期抓取），
 * 命名空間對應 src/locales/<lang>/<namespace>.json。
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

import commonEn from "../locales/en/common.json";
import landingEn from "../locales/en/landing.json";
import componentsEn from "../locales/en/components.json";
import servicesEn from "../locales/en/services.json";
import loginEn from "../locales/en/login.json";
import personalEn from "../locales/en/personal.json";
import resourceEn from "../locales/en/resource.json";
import aiEn from "../locales/en/ai.json";
import teachingEn from "../locales/en/teaching.json";
import systemEn from "../locales/en/system.json";
import networkEn from "../locales/en/network.json";

import commonJa from "../locales/ja/common.json";
import landingJa from "../locales/ja/landing.json";
import componentsJa from "../locales/ja/components.json";
import servicesJa from "../locales/ja/services.json";
import loginJa from "../locales/ja/login.json";
import personalJa from "../locales/ja/personal.json";
import resourceJa from "../locales/ja/resource.json";
import aiJa from "../locales/ja/ai.json";
import teachingJa from "../locales/ja/teaching.json";
import systemJa from "../locales/ja/system.json";
import networkJa from "../locales/ja/network.json";

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

i18n.use(initReactI18next).init({
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
    en: {
      common: commonEn,
      landing: landingEn,
      components: componentsEn,
      services: servicesEn,
      login: loginEn,
      personal: personalEn,
      resource: resourceEn,
      ai: aiEn,
      teaching: teachingEn,
      system: systemEn,
      network: networkEn,
    },
    ja: {
      common: commonJa,
      landing: landingJa,
      components: componentsJa,
      services: servicesJa,
      login: loginJa,
      personal: personalJa,
      resource: resourceJa,
      ai: aiJa,
      teaching: teachingJa,
      system: systemJa,
      network: networkJa,
    },
  },
  lng: loadStoredLanguage(),
  fallbackLng: DEFAULT_LANGUAGE,
  supportedLngs: SUPPORTED_LANGUAGES,
  ns: NAMESPACES,
  defaultNS: "common",
  interpolation: { escapeValue: false },
  returnEmptyString: false,
});

/** 切換語系並持久化到 localStorage（Sidebar 語言選單使用） */
export function setLanguage(lang) {
  if (!SUPPORTED_LANGUAGES.includes(lang)) return;
  i18n.changeLanguage(lang);
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, lang);
  } catch {
    // localStorage 不可用時（無痕模式等）僅本次 session 生效
  }
}

export default i18n;
