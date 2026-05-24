import { spawn, ChildProcess } from "child_process";
import { app, BrowserWindow, Notification } from "electron";
import fs from "fs";
import treeKill from "tree-kill";
import BeanFactory from "../core/BeanFactory";
import { BusinessError, ResponseCode } from "../core/BusinessError";
import GlobalConstant from "../core/GlobalConstant";
import Logger from "../core/Logger";
import PathUtils from "../utils/PathUtils";
import ResponseUtils from "../utils/ResponseUtils";
import SkyLabService from "./SkyLabService";

const FRPC_ERROR_PATTERNS = [
  "connect to server error",
  "login to server failed"
];
const FRPC_SUCCESS_PATTERNS = [
  "login to server success",
  "start proxy success",
  "proxy added success"
];

class FrpcProcessService {
  private readonly _SkyLabService: SkyLabService;
  private _frpcProcess: ChildProcess | null = null;
  private _frpcProcessListener: NodeJS.Timeout | null = null;
  private _frpcLastStartTime: number = -1;
  private _notifiedStartTime: number = -1;
  private _tunnels: SkyLabTunnelInfo[] = [];

  constructor() {
    this._SkyLabService = BeanFactory.getBean("SkyLabService");
  }

  isRunning(): boolean {
    if (!this._frpcProcess || typeof this._frpcProcess.pid !== "number") {
      return false;
    }
    try {
      process.kill(this._frpcProcess.pid, 0);
      return true;
    } catch (err: any) {
      if (err.code === "EPERM") return true;
      return false;
    }
  }

  get frpcLastStartTime(): number {
    return this._frpcLastStartTime;
  }

  get tunnels(): SkyLabTunnelInfo[] {
    return this._tunnels;
  }

  readFrpcConnectionError(): string | null {
    const logPath = PathUtils.getFrpcLogFilePath();
    if (!fs.existsSync(logPath) || this._frpcLastStartTime === -1) {
      return null;
    }
    try {
      const stat = fs.statSync(logPath);
      if (stat.size === 0) return null;
      const readSize = Math.min(stat.size, 8192);
      const buf = Buffer.alloc(readSize);
      const fd = fs.openSync(logPath, "r");
      fs.readSync(fd, buf, 0, readSize, stat.size - readSize);
      fs.closeSync(fd);
      const lines = buf.toString("utf-8").split("\n").filter(l => l.trim());
      for (let i = lines.length - 1; i >= 0; i--) {
        const line = lines[i];
        if (FRPC_SUCCESS_PATTERNS.some(p => line.includes(p))) {
          return null;
        }
        const pattern = FRPC_ERROR_PATTERNS.find(p => line.includes(p));
        if (pattern) {
          const match = line.match(new RegExp(`${pattern}.*`));
          return match ? match[0].trim() : line.trim();
        }
      }
      return null;
    } catch {
      return null;
    }
  }

  async startTunnel(): Promise<void> {
    if (this.isRunning()) {
      Logger.info(
        "FrpcProcessService.startTunnel",
        `Already running, pid=${this._frpcProcess?.pid}`
      );
      return;
    }

    const frpcBinary = PathUtils.getBundledFrpcPath();
    if (!fs.existsSync(frpcBinary)) {
      throw new BusinessError(
        ResponseCode.FRPC_BINARY_MISSING,
        `path=${frpcBinary}`
      );
    }

    const config = await this._SkyLabService.getTunnelConfig();
    if (!config.tunnels || config.tunnels.length === 0) {
      throw new BusinessError(ResponseCode.NO_TUNNELS);
    }

    const configPath = PathUtils.getTomlConfigFilePath();
    fs.writeFileSync(configPath, config.frpc_config, { encoding: "utf-8" });
    this._tunnels = config.tunnels;

    Logger.info(
      "FrpcProcessService.startTunnel",
      `Starting frpc, binary=${frpcBinary}, config=${configPath}, tunnels=${config.tunnels.length}`
    );

    this._frpcProcess = spawn(frpcBinary, ["-c", configPath], {
      windowsHide: true
    });
    this._frpcLastStartTime = Date.now();

    this._frpcProcess.stdout?.on("data", data => {
      Logger.debug("FrpcProcessService.startTunnel", `stdout: ${data}`);
    });
    this._frpcProcess.stderr?.on("data", data => {
      Logger.debug("FrpcProcessService.startTunnel", `stderr: ${data}`);
    });
    this._frpcProcess.on("exit", (code, signal) => {
      Logger.info(
        "FrpcProcessService.startTunnel",
        `frpc exited code=${code} signal=${signal}`
      );
    });

    Logger.info(
      "FrpcProcessService.startTunnel",
      `frpc started pid=${this._frpcProcess.pid}`
    );
  }

  async stopTunnel(): Promise<void> {
    if (!this._frpcProcess || !this.isRunning()) {
      this._resetState();
      return;
    }
    const pid = this._frpcProcess.pid as number;
    Logger.info("FrpcProcessService.stopTunnel", `Stopping frpc, pid=${pid}`);
    await new Promise<void>(resolve => {
      treeKill(pid, err => {
        if (err) {
          Logger.error("FrpcProcessService.stopTunnel", err);
        }
        resolve();
      });
    });
    this._resetState();
  }

  private _resetState() {
    this._frpcProcess = null;
    this._frpcLastStartTime = -1;
    this._notifiedStartTime = -1;
    this._tunnels = [];
  }

  watchTunnel(listenerParam: ListenerParam) {
    if (this._frpcProcessListener) {
      clearInterval(this._frpcProcessListener);
    }
    this._frpcProcessListener = setInterval(() => {
      const running = this.isRunning();
      if (
        !running &&
        this._frpcLastStartTime !== -1 &&
        this._notifiedStartTime !== this._frpcLastStartTime
      ) {
        Logger.warn(
          "FrpcProcessService.watchTunnel",
          `frpc exited unexpectedly (lastStartTime=${this._frpcLastStartTime})`
        );
        new Notification({
          title: app.getName(),
          body: "Connection lost, please check the logs for details."
        }).show();
        this._notifiedStartTime = this._frpcLastStartTime;
      }
      const connectionError = running ? this.readFrpcConnectionError() : null;
      const win: BrowserWindow = BeanFactory.getBean("win");
      if (win && !win.isDestroyed()) {
        const status: TunnelStatusInfo = {
          running,
          lastStartTime: this._frpcLastStartTime,
          connectionError,
          tunnels: this._tunnels
        };
        win.webContents.send(
          listenerParam.channel,
          ResponseUtils.success(status)
        );
      }
    }, GlobalConstant.TUNNEL_STATUS_POLL_INTERVAL_MS);
  }
}

export default FrpcProcessService;
