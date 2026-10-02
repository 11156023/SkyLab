import { createI18n } from "vue-i18n";
import enUS from "./en-US";
import ja from "./ja";
import zhTW from "./zh-TW";

const messages = {
  "zh-TW": zhTW,
  "en-US": enUS,
  ja
};

const i18n = createI18n({
  locale: "zh-TW",
  fallbackLocale: "en-US",
  legacy: false,
  messages
});

export default i18n;
