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
import { ipcRouters } from "../electron/core/IpcRouter";
import "./styles/index.scss";

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
app.component("IconifyIconOffline", IconifyIconOffline);
app.component("IconifyIconOnline", IconifyIconOnline);

app.use(i18n).use(router).use(ElementPlus).use(pinia);

const appStore = useAppStore(pinia);

app.mount("#app").$nextTick(async () => {
  appStore.registerListeners();
  const authReady = waitForInitialReply(ipcRouters.AUTH.getAuthState.path);
  const settingsReady = waitForInitialReply(ipcRouters.SETTINGS.getSettings.path);
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

  await Promise.all([authReady, settingsReady, router.isReady()]);
  postMessage({ payload: "removeLoading" }, "*");
});
