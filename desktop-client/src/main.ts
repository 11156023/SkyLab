import ElementPlus from "element-plus";
import { createPinia } from "pinia";
import { createApp, watch } from "vue";
import App from "./App.vue";
import i18n from "./lang";
import router from "./router";
import { useAppStore } from "./store/app";
import { ipcRouters } from "../electron/core/IpcRouter";
/* 圖示字型與全站樣式（色票、reset、背景）直接用 web 端的同一份；
   EP 的深色變數檔要排在自家樣式前面，index.scss 才蓋得過它 */
import "@material-design-icons/font/outlined.css";
import "@material-design-icons/font/filled.css";
import "@web/assets/styles/global.scss";
import "element-plus/theme-chalk/dark/css-vars.css";
import "./styles/index.scss";
import "./styles/workspace.scss";

function waitForInitialReply(path: string): Promise<void> {
  return new Promise(resolve => {
    const channel = `${path}:hook`;
    const finish = () => {
      clearTimeout(timeout);
      window.electronIpcRenderer.removeListener(channel, handleReply);
      resolve();
    };
    const handleReply = () => finish();
    const timeout = setTimeout(finish, 5000);
    window.electronIpcRenderer.on(channel, handleReply);
  });
}

const pinia = createPinia();

const app = createApp(App);
app.use(i18n).use(router).use(ElementPlus).use(pinia);

const appStore = useAppStore(pinia);

app.mount("#app").$nextTick(async () => {
  appStore.registerListeners();
  appStore.startUpdatePolling();
  const authReady = waitForInitialReply(ipcRouters.AUTH.getAuthState.path);
  const settingsReady = waitForInitialReply(
    ipcRouters.SETTINGS.getSettings.path
  );
  appStore.refreshAuth();
  appStore.refreshSettings();

  watch(
    () => appStore.language,
    lang => {
      if (lang) {
        document.documentElement.lang = lang === "zh-TW" ? "zh-Hant" : lang;
        (i18n.global.locale as any).value = lang;
      }
    },
    { immediate: true }
  );

  await Promise.all([authReady, settingsReady, router.isReady()]);
  postMessage({ payload: "removeLoading" }, "*");
});
