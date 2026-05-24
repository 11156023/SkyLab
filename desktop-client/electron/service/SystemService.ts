import { spawn } from "child_process";
import { app, shell } from "electron";
import fs from "fs";
import os from "os";
import path from "path";

class SystemService {
  async openUrl(url: string) {
    if (url) {
      await shell.openExternal(url);
    }
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

  async openSsh(port: number, user = "root", host = "127.0.0.1"): Promise<void> {
    const sshCmd = `ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p ${port} ${user}@${host}`;
    if (process.platform === "win32") {
      const batPath = path.join(
        os.tmpdir(),
        `SkyLab-ssh-${port}-${Date.now()}.bat`
      );
      fs.writeFileSync(
        batPath,
        `@echo off\r\ntitle SkyLab SSH - ${host}:${port}\r\n${sshCmd}\r\npause\r\n`,
        { encoding: "utf-8" }
      );
      spawn("cmd.exe", ["/c", "start", "", batPath], {
        detached: true,
        stdio: "ignore",
        windowsHide: false
      }).unref();
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
}

export default SystemService;
