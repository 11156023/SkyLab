import { ipcRenderer } from "electron";
import pkg from "../../package.json";

Object.defineProperty(window, "electronIpcRenderer", {
  value: ipcRenderer,
  configurable: false,
  enumerable: false,
  writable: false
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
.app-loading-cubes {
  position: relative;
  width: 112px;
  height: 92px;
  margin-bottom: 12px;
  perspective: 240px;
}
.app-loading-cube {
  position: absolute;
  width: 28px;
  height: 28px;
  background: #5471bf;
  box-shadow: 0 7px 14px rgba(43, 77, 152, .13);
  transform: rotateX(-25deg) rotateY(-35deg);
  animation: app-cube-rise 2.4s ease-in-out infinite;
}
.app-loading-cube::before,
.app-loading-cube::after {
  position: absolute;
  content: "";
  background: #7794d3;
}
.app-loading-cube::before {
  top: -10px;
  left: 0;
  width: 28px;
  height: 10px;
  transform: skewX(45deg);
  transform-origin: bottom left;
}
.app-loading-cube::after {
  top: 0;
  right: -10px;
  width: 10px;
  height: 28px;
  background: #34539b;
  transform: skewY(45deg);
  transform-origin: top left;
}
.app-loading-cube:nth-child(2) { top: 42px; left: 20px; animation-delay: 0s; }
.app-loading-cube:nth-child(3) { top: 26px; left: 54px; animation-delay: .2s; }
.app-loading-cube:nth-child(4) { top: 50px; left: 76px; animation-delay: .4s; }
.app-loading-ground {
  position: absolute;
  right: 4px;
  bottom: 1px;
  left: 10px;
  height: 12px;
  border-radius: 50%;
  background: rgba(84, 113, 191, .15);
  filter: blur(7px);
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
@keyframes app-cube-rise {
  0%, 16%, 100% { transform: translateY(8px) rotateX(-25deg) rotateY(-35deg); opacity: .55; }
  45%, 64% { transform: translateY(-9px) rotateX(-25deg) rotateY(-35deg); opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .app-loading-cube { animation: none; opacity: 1; }
  .app-loading-wrap { transition: none; }
}
    `;
  const oStyle = document.createElement("style");
  const oDiv = document.createElement("div");
  let removed = false;

  oStyle.id = "app-loading-style";
  oStyle.textContent = styleContent;
  oDiv.className = "app-loading-wrap";
  oDiv.setAttribute("role", "status");
  oDiv.setAttribute("aria-label", "正在啟動 SkyLab Connect");
  oDiv.innerHTML = `
    <div class="app-loading-card">
      <div class="app-loading-cubes" aria-hidden="true">
        <span class="app-loading-ground"></span>
        <span class="app-loading-cube"></span>
        <span class="app-loading-cube"></span>
        <span class="app-loading-cube"></span>
      </div>
      <h1 class="app-loading-title">SkyLab Connect</h1>
      <p class="app-loading-description">正在準備您的安全連線…</p>
      <span class="app-loading-version">v${pkg.version}</span>
    </div>`;

  return {
    appendLoading() {
      if (removed) return;
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
