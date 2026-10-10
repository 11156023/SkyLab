import { spawn } from "child_process";
import { app, shell } from "electron";

class SystemService {
  /**
   * 只開允許的網址：固定的 GitHub／SkyLab 網域，加上設定頁填的後端網址
   * （「開啟 Web 平台」用它；可能是自架網域或本機測試的 http://localhost）
   */
  async openUrl(url: string, backendUrl?: string) {
    const target = new URL(url);
    const allowedHosts = new Set([
      "github.com",
      "objects.githubusercontent.com",
      "github-releases.githubusercontent.com",
      "skylab-tw.com"
    ]);
    const isBackend =
      !!backendUrl && target.origin === new URL(backendUrl).origin;
    if (
      !isBackend &&
      (target.protocol !== "https:" || !allowedHosts.has(target.hostname))
    ) {
      throw new Error(`URL is not allowed: ${target.origin}`);
    }
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
