import { contextBridge, ipcRenderer } from "electron";
import pkg from "../../package.json";
import { ipcRouters, listeners } from "../core/IpcRouter";

const requestChannels = new Set(
  Object.values(ipcRouters).flatMap(group =>
    Object.values(group).map(router => router.path)
  )
);
const responseChannels = new Set([
  ...[...requestChannels].map(channel => `${channel}:hook`),
  ...Object.values(listeners).map(listener => listener.channel),
  "auth:event"
]);
type RendererListener = (...args: any[]) => void;
const wrappedListeners = new Map<
  string,
  Map<RendererListener, RendererListener>
>();

contextBridge.exposeInMainWorld("electronIpcRenderer", {
  send(channel: string, args?: unknown) {
    if (!requestChannels.has(channel)) throw new Error("IPC channel denied");
    ipcRenderer.send(channel, args);
  },
  on(channel: string, listener: RendererListener) {
    if (!responseChannels.has(channel)) throw new Error("IPC channel denied");
    const wrapped = (_event: unknown, ...args: any[]) => listener({}, ...args);
    const channelListeners = wrappedListeners.get(channel) || new Map();
    channelListeners.set(listener, wrapped);
    wrappedListeners.set(channel, channelListeners);
    ipcRenderer.on(channel, wrapped);
  },
  removeListener(channel: string, listener: RendererListener) {
    const wrapped = wrappedListeners.get(channel)?.get(listener);
    if (!wrapped) return;
    ipcRenderer.removeListener(channel, wrapped);
    wrappedListeners.get(channel)?.delete(listener);
  },
  removeAllListeners(channel: string) {
    if (!responseChannels.has(channel)) throw new Error("IPC channel denied");
    ipcRenderer.removeAllListeners(channel);
    wrappedListeners.delete(channel);
  }
});

function domReady(
  condition: DocumentReadyState[] = ["complete", "interactive"]
) {
  return new Promise(resolve => {
    if (condition.includes(document.readyState)) {
      resolve(true);
    } else {
      document.addEventListener("readystatechange", () => {
        if (condition.includes(document.readyState)) {
          resolve(true);
        }
      });
    }
  });
}

const safeDOM = {
  append(parent: HTMLElement, child: HTMLElement) {
    if (!Array.from(parent.children).find(e => e === child)) {
      return parent.appendChild(child);
    }
  },
  remove(parent: HTMLElement, child: HTMLElement) {
    if (Array.from(parent.children).find(e => e === child)) {
      return parent.removeChild(child);
    }
  }
};

function useLoading() {
  const styleContent = `
.app-loading-wrap {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: grid;
  padding: 24px;
  place-items: center;
  background:
    radial-gradient(ellipse 100% 60% at 100% 0%, #c1daff 0%, transparent 100%),
    radial-gradient(ellipse 100% 80% at 0% 90%, #feffed 0%, transparent 100%),
    radial-gradient(ellipse 150% 70% at 100% 100%, #edfff6 0%, transparent 100%),
    #e8f0fd;
  font-family: "Helvetica Neue", Helvetica, "PingFang SC", "Microsoft YaHei", sans-serif;
  transition: opacity 220ms ease, visibility 220ms ease;
}
.app-loading-wrap.is-leaving {
  opacity: 0;
  visibility: hidden;
}
.app-loading-card {
  display: flex;
  width: min(100%, 440px);
  min-height: 280px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 24px;
  border: 1px solid rgba(255, 255, 255, .78);
  border-radius: 18px;
  background: rgba(255, 255, 255, .82);
  box-shadow: 0 18px 48px rgba(67, 90, 149, .14), inset 0 1px 0 #fff;
  backdrop-filter: blur(14px) saturate(1.2);
  text-align: center;
}
.app-loading-octo {
  width: 80px;
  height: 80px;
  margin-bottom: 16px;
  object-fit: contain;
  image-rendering: pixelated;
  animation: app-octo-bob 2.4s ease-in-out infinite;
}
.app-loading-title {
  margin: 0;
  color: #3a549d;
  font-size: 23px;
  font-weight: 700;
  letter-spacing: .01em;
}
.app-loading-description {
  margin: 10px 0 0;
  color: #617dc8;
  font-size: 14px;
  line-height: 1.65;
}
.app-loading-version {
  margin-top: 20px;
  color: #8291aa;
  font-size: 12px;
  letter-spacing: .04em;
}
html:not([data-theme="light"]) .app-loading-wrap { background: #101722; }
html:not([data-theme="light"]) .app-loading-card {
  background: #1a2434;
  border-color: #ffffff14;
  box-shadow: none;
}
html:not([data-theme="light"]) .app-loading-title { color: #edf2fa; }
html:not([data-theme="light"]) .app-loading-description,
html:not([data-theme="light"]) .app-loading-version { color: #a9b7ce; }
@keyframes app-octo-bob {
  0%, 100% { transform: translateY(3px); }
  50% { transform: translateY(-5px); }
}
@media (prefers-reduced-motion: reduce) {
  .app-loading-octo { animation: none; }
  .app-loading-wrap { transition: none; }
}
    `;
  const oStyle = document.createElement("style");
  const oDiv = document.createElement("div");
  let removed = false;
  const locale = navigator.language.toLowerCase();
  const loadingText = locale.startsWith("ja")
    ? "安全な接続を準備しています…"
    : locale.startsWith("zh")
      ? "正在準備您的安全連線…"
      : "Preparing your secure connection…";
  const loadingLabel = locale.startsWith("ja")
    ? "SkyLab Connect を起動中"
    : locale.startsWith("zh")
      ? "正在啟動 SkyLab Connect"
      : "Starting SkyLab Connect";

  oStyle.id = "app-loading-style";
  oStyle.textContent = styleContent;
  oDiv.className = "app-loading-wrap";
  oDiv.setAttribute("role", "status");
  oDiv.setAttribute("aria-label", loadingLabel);
  oDiv.innerHTML = `
    <div class="app-loading-card">
      <img class="app-loading-octo" src="./logo/pixel-octo/64x64.png" alt="" />
      <h1 class="app-loading-title">SkyLab Connect</h1>
      <p class="app-loading-description">${loadingText}</p>
      <span class="app-loading-version">v${pkg.version}</span>
    </div>`;

  return {
    appendLoading() {
      if (removed) return;
      try {
        document.documentElement.dataset.theme =
          localStorage.getItem("skylab.theme") === "light" ? "light" : "dark";
      } catch {
        document.documentElement.dataset.theme = "dark";
      }
      safeDOM.append(document.head, oStyle);
      safeDOM.append(document.body, oDiv);
    },
    removeLoading() {
      if (removed) return;
      removed = true;
      oDiv.classList.add("is-leaving");
      setTimeout(() => {
        safeDOM.remove(document.head, oStyle);
        safeDOM.remove(document.body, oDiv);
      }, 250);
    }
  };
}

// ----------------------------------------------------------------------

const { appendLoading, removeLoading } = useLoading();
domReady().then(appendLoading);

window.addEventListener("message", ev => {
  if (ev.data?.payload === "removeLoading") removeLoading();
});

setTimeout(removeLoading, 8000);
