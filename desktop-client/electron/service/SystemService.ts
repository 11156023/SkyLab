import { spawn } from "child_process";
import { app, shell } from "electron";
import BeanFactory from "../core/BeanFactory";
import GlobalConstant from "../core/GlobalConstant";
import type SettingsService from "./SettingsService";

class SystemService {
  async openUrl(url: string) {
    const target = new URL(url);
    // 「開啟 Web 平台」開的是設定頁填的後端（SettingsService 已驗證過 HTTPS／本機
    // http），不只預設網址；只放行同一個 origin。
    const settingsService: SettingsService =
      BeanFactory.getBean("settingsService");
    const backendUrl = await settingsService.getBackendUrl();
    if (target.origin === new URL(backendUrl).origin) {
      await shell.openExternal(target.toString());
      return;
    }
    if (target.protocol !== "https:") throw new Error("URL must use HTTPS");
    const allowedHosts = new Set([
      "github.com",
      "objects.githubusercontent.com",
      "github-releases.githubusercontent.com",
      new URL(GlobalConstant.DEFAULT_BACKEND_URL).hostname
    ]);
    if (!allowedHosts.has(target.hostname))
      throw new Error("URL host is not allowed");
    await shell.openExternal(target.toString());
  }

  async relaunch() {
    app.relaunch();
    app.quit();
  }

  openLocalFile(filePath: string): Promise<boolean> {
    return new Promise<boolean>((resolve, reject) => {
      shell
        .openPath(filePath)
        .then(errorMessage => {
          resolve(!errorMessage);
        })
        .catch(reject);
    });
  }

  openLocalPath(localPath: string): Promise<boolean> {
    return new Promise<boolean>(resolve => {
      shell.openPath(localPath).then(errorMessage => {
        resolve(!errorMessage);
      });
    });
  }

  async openSsh(
    port: number,
    user = "root",
    host = "127.0.0.1"
  ): Promise<void> {
    const sshCmd = `ssh -o StrictHostKeyChecking=accept-new -p ${port} ${user}@${host}`;
    if (process.platform === "win32") {
      await this._spawnDetached("cmd.exe", [
        "/d",
        "/k",
        `title SkyLab SSH - ${host}:${port} & ${sshCmd}`
      ]);
    } else if (process.platform === "darwin") {
      const script = `tell application "Terminal" to do script "${sshCmd}"`;
      spawn("osascript", ["-e", script], {
        detached: true,
        stdio: "ignore"
      }).unref();
    } else {
      spawn("x-terminal-emulator", ["-e", "sh", "-c", sshCmd], {
        detached: true,
        stdio: "ignore"
      }).unref();
    }
  }

  async openRdp(port: number, host = "127.0.0.1"): Promise<void> {
    const target = `${host}:${port}`;
    if (process.platform === "win32") {
      await this._spawnDetached("mstsc.exe", [`/v:${target}`]);
      return;
    }
    if (process.platform === "darwin") {
      await shell.openExternal(`rdp://full%20address=s:${target}`);
      return;
    }
    await this._spawnDetached("xfreerdp", [`/v:${target}`]);
  }

  private _spawnDetached(command: string, args: string[]): Promise<void> {
    return new Promise((resolve, reject) => {
      const child = spawn(command, args, {
        detached: true,
        stdio: "ignore",
        windowsHide: false
      });
      child.once("error", reject);
      child.once("spawn", () => {
        child.unref();
        resolve();
      });
    });
  }
}

export default SystemService;
