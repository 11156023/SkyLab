import "animate.css";
import ElementPlus from "element-plus";
import { createPinia } from "pinia";
import { createApp, watch } from "vue";
import App from "./App.vue";
import {
  IconifyIconOffline,
  IconifyIconOnline
} from "./components/IconifyIcon";
import i18n from "./lang";
import router from "./router";
import { useAppStore } from "./store/app";
import "./styles/index.scss";

const pinia = createPinia();

const app = createApp(App);
app.component("IconifyIconOffline", IconifyIconOffline);
app.component("IconifyIconOnline", IconifyIconOnline);

app.use(i18n).use(router).use(ElementPlus).use(pinia);

const appStore = useAppStore(pinia);

app.mount("#app").$nextTick(() => {
  appStore.registerListeners();
  appStore.refreshAuth();
  appStore.refreshSettings();

  watch(
    () => appStore.language,
    lang => {
      if (lang) {
        (i18n.global.locale as any).value = lang;
      }
    },
    { immediate: true }
  );

  postMessage({ payload: "removeLoading" }, "*");
});
