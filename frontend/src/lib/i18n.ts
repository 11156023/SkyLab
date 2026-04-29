import i18n from "i18next"
import LanguageDetector from "i18next-browser-languagedetector"
import { initReactI18next } from "react-i18next"
import adminEN from "@/locales/en/admin.json"
import aiManagementEN from "@/locales/en/aiManagement.json"
import applicationsEN from "@/locales/en/applications.json"
import approvalsEN from "@/locales/en/approvals.json"
import authEN from "@/locales/en/auth.json"
// Import translations
import commonEN from "@/locales/en/common.json"
import consoleEN from "@/locales/en/console.json"
import firewallEN from "@/locales/en/firewall.json"
import groupsEN from "@/locales/en/groups.json"
import messagesEN from "@/locales/en/messages.json"
import navigationEN from "@/locales/en/navigation.json"
import networkEN from "@/locales/en/network.json"
import resourceDetailEN from "@/locales/en/resourceDetail.json"
import resourcesEN from "@/locales/en/resources.json"
import reverseProxyEN from "@/locales/en/reverseProxy.json"
import settingsEN from "@/locales/en/settings.json"
import validationEN from "@/locales/en/validation.json"
import adminJA from "@/locales/ja/admin.json"
import aiManagementJA from "@/locales/ja/aiManagement.json"
import applicationsJA from "@/locales/ja/applications.json"
import approvalsJA from "@/locales/ja/approvals.json"
import authJA from "@/locales/ja/auth.json"
import commonJA from "@/locales/ja/common.json"
import consoleJA from "@/locales/ja/console.json"
import firewallJA from "@/locales/ja/firewall.json"
import groupsJA from "@/locales/ja/groups.json"
import messagesJA from "@/locales/ja/messages.json"
import navigationJA from "@/locales/ja/navigation.json"
import networkJA from "@/locales/ja/network.json"
import resourceDetailJA from "@/locales/ja/resourceDetail.json"
import resourcesJA from "@/locales/ja/resources.json"
import reverseProxyJA from "@/locales/ja/reverseProxy.json"
import settingsJA from "@/locales/ja/settings.json"
import validationJA from "@/locales/ja/validation.json"
import adminZH from "@/locales/zh-TW/admin.json"
import aiManagementZH from "@/locales/zh-TW/aiManagement.json"
import applicationsZH from "@/locales/zh-TW/applications.json"
import approvalsZH from "@/locales/zh-TW/approvals.json"
import authZH from "@/locales/zh-TW/auth.json"
import commonZH from "@/locales/zh-TW/common.json"
import consoleZH from "@/locales/zh-TW/console.json"
import firewallZH from "@/locales/zh-TW/firewall.json"
import groupsZH from "@/locales/zh-TW/groups.json"
import messagesZH from "@/locales/zh-TW/messages.json"
import navigationZH from "@/locales/zh-TW/navigation.json"
import networkZH from "@/locales/zh-TW/network.json"
import resourceDetailZH from "@/locales/zh-TW/resourceDetail.json"
import resourcesZH from "@/locales/zh-TW/resources.json"
import reverseProxyZH from "@/locales/zh-TW/reverseProxy.json"
import settingsZH from "@/locales/zh-TW/settings.json"
import validationZH from "@/locales/zh-TW/validation.json"

const resources = {
  en: {
    common: commonEN,
    auth: authEN,
    console: consoleEN,
    navigation: navigationEN,
    resources: resourcesEN,
    resourceDetail: resourceDetailEN,
    applications: applicationsEN,
    approvals: approvalsEN,
    settings: settingsEN,
    validation: validationEN,
    messages: messagesEN,
    admin: adminEN,
    firewall: firewallEN,
    groups: groupsEN,
    aiManagement: aiManagementEN,
    reverseProxy: reverseProxyEN,
    network: networkEN,
  },
  "zh-TW": {
    common: commonZH,
    auth: authZH,
    console: consoleZH,
    navigation: navigationZH,
    resources: resourcesZH,
    resourceDetail: resourceDetailZH,
    applications: applicationsZH,
    approvals: approvalsZH,
    settings: settingsZH,
    validation: validationZH,
    messages: messagesZH,
    admin: adminZH,
    firewall: firewallZH,
    groups: groupsZH,
    aiManagement: aiManagementZH,
    reverseProxy: reverseProxyZH,
    network: networkZH,
  },
  ja: {
    common: commonJA,
    auth: authJA,
    console: consoleJA,
    navigation: navigationJA,
    resources: resourcesJA,
    resourceDetail: resourceDetailJA,
    applications: applicationsJA,
    approvals: approvalsJA,
    settings: settingsJA,
    validation: validationJA,
    messages: messagesJA,
    admin: adminJA,
    firewall: firewallJA,
    groups: groupsJA,
    aiManagement: aiManagementJA,
    reverseProxy: reverseProxyJA,
    network: networkJA,
  },
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: "en",
    defaultNS: "common",
    fallbackNS: "common",

    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
      lookupLocalStorage: "campus-cloud-language",
    },

    interpolation: {
      escapeValue: false, // React already escapes values
    },

    react: {
      useSuspense: true,
    },
  })

export default i18n
