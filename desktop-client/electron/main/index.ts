import {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  MenuItem,
  MenuItemConstructorOptions,
  shell,
  Tray
} from "electron";
import { release, totalmem, cpus } from "node:os";
import node_path, { join } from "node:path";
import AuthController from "../controller/AuthController";
import LogController from "../controller/LogController";
import ResourceController from "../controller/ResourceController";
import SettingsController from "../controller/SettingsController";
import SystemController from "../controller/SystemController";
import TunnelController from "../controller/TunnelController";
import BeanFactory from "../core/BeanFactory";
import { ipcRouters, listeners } from "../core/IpcRouter";
import Logger from "../core/Logger";
import SettingsRepository from "../repository/SettingsRepository";
import AuthService from "../service/AuthService";
import SkyLabService from "../service/SkyLabService";
import FrpcProcessService from "../service/FrpcProcessService";
import LogService from "../service/LogService";
import SettingsService from "../service/SettingsService";
import SystemService from "../service/SystemService";

process.env.DIST_ELECTRON = join(__dirname, "..");
process.env.DIST = join(process.env.DIST_ELECTRON, "../dist");
process.env.VITE_PUBLIC = process.env.VITE_DEV_SERVER_URL
  ? join(process.env.DIST_ELECTRON, "../public")
  : process.env.DIST;

const preload = join(__dirname, "../preload/index.js");
const url = process.env.VITE_DEV_SERVER_URL;
const indexHtml = join(process.env.DIST, "index.html");

class SkyLabApp {
  private _win: BrowserWindow | null = null;
  private _quitting = false;

  constructor() {
    this.initializeBeans();
    this.initializeListeners();
    this.initializeRouters();
    this.initializeElectronApp();
  }

  async initializeWindow() {
    if (this._win) return;

    Logger.info(
      "SkyLabApp.initializeWindow",
      [
        "=== Application Started ===",
        `App       : ${app.getName()} v${app.getVersion()}`,
        `Platform  : ${process.platform} / ${process.arch}`,
        `OS Release: ${release()}`,
        `Node.js   : ${process.versions.node}`,
        `Electron  : ${process.versions.electron}`,
        `Chrome    : ${process.versions.chrome}`,
        `CPU       : ${cpus()[0]?.model ?? "unknown"} (${cpus().length} cores)`,
        `Memory    : ${(totalmem() / 1024 / 1024 / 1024).toFixed(1)} GB`
      ].join("\n")
    );

    this._win = new BrowserWindow({
      title: `${app.getName()} v${app.getVersion()}`,
      icon: join(process.env.VITE_PUBLIC, "logo/only/16x16.png"),
      width: 960,
      height: 640,
      minWidth: 900,
      minHeight: 600,
      webPreferences: {
        preload,
        nodeIntegration: true,
        contextIsolation: false
      },
      show: !process.argv.includes("--hidden")
    });
    BeanFactory.setBean("win", this._win);

    if (process.env.VITE_DEV_SERVER_URL) {
      await this._win.loadURL(url);
    } else {
      await this._win.loadFile(indexHtml);
    }

    this._win.webContents.on("did-finish-load", () => {
      this._win?.webContents.send(
        "main-process-message",
        new Date().toLocaleString()
      );
    });
    this._win.webContents.setWindowOpenHandler(({ url }) => {
      if (url.startsWith("https:") || url.startsWith("http:")) {
        shell.openExternal(url);
      }
      return { action: "deny" };
    });

    Menu.setApplicationMenu(null);

    const that = this;
    (this._win as any).on("minimize", (event: any) => {
      event.preventDefault();
      that._win?.hide();
    });

    this._win.on("close", event => {
      if (!that._quitting) {
        event.preventDefault();
        that._win?.hide();
        if (process.platform === "darwin") {
          app.dock.hide();
        }
      }
      return false;
    });

    Logger.info("SkyLabApp.initializeWindow", "Window initialized.");
  }

  initializeTray() {
    const that = this;
    const menu: Array<MenuItemConstructorOptions | MenuItem> = [
      {
        label: "Show",
        click: () => {
          that._win?.show();
          if (process.platform === "darwin") {
            app.dock.show();
          }
        }
      },
      {
        label: "Quit",
        click: () => {
          that._quitting = true;
          const frpcProcessService: FrpcProcessService =
            BeanFactory.getBean("frpcProcessService");
          frpcProcessService.stopTunnel().finally(() => app.quit());
        }
      }
    ];
    const tray = new Tray(
      node_path.join(process.env.VITE_PUBLIC, "logo/only/16x16.png")
    );
    tray.setToolTip(app.getName());
    tray.setContextMenu(Menu.buildFromTemplate(menu));
    tray.on("double-click", () => this._win?.show());
    Logger.info("SkyLabApp.initializeTray", "Tray initialized.");
  }

  initializeElectronApp() {
    if (release().startsWith("6.1")) app.disableHardwareAcceleration();
    if (process.platform === "win32") app.setAppUserModelId(app.getName());

    if (!app.requestSingleInstanceLock()) {
      app.quit();
      process.exit(0);
    }

    app.whenReady().then(() => {
      this.initializeWindow();
      this.initializeTray();
    });

    app.on("window-all-closed", () => {
      this._win = null;
      if (process.platform !== "darwin") {
        const frpcProcessService: FrpcProcessService =
          BeanFactory.getBean("frpcProcessService");
        frpcProcessService.stopTunnel().finally(() => app.quit());
      }
    });

    app.on("second-instance", () => {
      if (this._win) {
        if (this._win.isMinimized()) this._win.show();
        if (!this._win.isVisible()) this._win.show();
        this._win.focus();
      }
    });

    app.on("activate", () => {
      const wins = BrowserWindow.getAllWindows();
      if (wins.length) wins[0].focus();
      else this.initializeWindow();
    });

    app.on("before-quit", () => {
      this._quitting = true;
      const frpcProcessService: FrpcProcessService =
        BeanFactory.getBean("frpcProcessService");
      frpcProcessService.stopTunnel().finally(() => {});
    });

    Logger.info(
      "SkyLabApp.initializeElectronApp",
      "ElectronApp initialized."
    );
  }

  initializeBeans() {
    BeanFactory.setBean("settingsRepository", new SettingsRepository());
    BeanFactory.setBean(
      "settingsService",
      new SettingsService(BeanFactory.getBean("settingsRepository"))
    );
    BeanFactory.setBean("systemService", new SystemService());
    BeanFactory.setBean(
      "SkyLabService",
      new SkyLabService(BeanFactory.getBean("settingsService"))
    );
    BeanFactory.setBean(
      "authService",
      new AuthService(
        BeanFactory.getBean("SkyLabService"),
        BeanFactory.getBean("settingsService")
      )
    );
    BeanFactory.setBean(
      "logService",
      new LogService(BeanFactory.getBean("systemService"))
    );
    BeanFactory.setBean("frpcProcessService", new FrpcProcessService());

    BeanFactory.setBean(
      "authController",
      new AuthController(BeanFactory.getBean("authService"))
    );
    BeanFactory.setBean(
      "resourceController",
      new ResourceController(BeanFactory.getBean("SkyLabService"))
    );
    BeanFactory.setBean(
      "tunnelController",
      new TunnelController(BeanFactory.getBean("frpcProcessService"))
    );
    BeanFactory.setBean(
      "settingsController",
      new SettingsController(BeanFactory.getBean("settingsService"))
    );
    BeanFactory.setBean(
      "logController",
      new LogController(BeanFactory.getBean("logService"))
    );
    BeanFactory.setBean("systemController", new SystemController());

    Logger.info("SkyLabApp.initializeBeans", "Beans initialized.");
  }

  private initializeListeners() {
    Object.keys(listeners).forEach(listenerKey => {
      const { listenerMethod, channel } = listeners[listenerKey];
      const [beanName, method] = listenerMethod.split(".");
      const bean = BeanFactory.getBean<any>(beanName);
      const listenerParam: ListenerParam = { channel, args: [] };
      bean[method].call(bean, listenerParam);
    });
    Logger.info("SkyLabApp.initializeListeners", "Listeners initialized.");
  }

  private initializeRouters() {
    Object.keys(ipcRouters).forEach(routerKey => {
      const routerGroup = ipcRouters[routerKey as IpcRouterKeys];
      Object.keys(routerGroup).forEach(method => {
        const router = routerGroup[method];
        ipcMain.on(router.path, (event, args) => {
          const req: ControllerParam = {
            channel: `${router.path}:hook`,
            event,
            args
          };
          const [beanName, fn] = router.controller.split(".");
          const bean = BeanFactory.getBean<any>(beanName);
          bean[fn].call(bean, req);
          Logger.debug(
            "ipcRouter",
            `path=${router.path} => ${beanName}.${fn}`
          );
        });
      });
    });
    Logger.info("SkyLabApp.initializeRouters", "Routers initialized.");
  }
}

new SkyLabApp();
