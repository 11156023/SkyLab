import fs from "fs";
import PathUtils from "../utils/PathUtils";
import SystemService from "./SystemService";

class LogService {
  private readonly _systemService: SystemService;
  private readonly _appPath: string = PathUtils.getAppLogFilePath();

  constructor(systemService: SystemService) {
    this._systemService = systemService;
  }

  async getAppLogContent() {
    return new Promise((resolve, reject) => {
      if (!fs.existsSync(this._appPath)) {
        resolve("");
        return;
      }
      try {
        const data = fs.readFileSync(this._appPath, "utf-8");
        resolve(data);
      } catch (error) {
        reject(error);
      }
    });
  }

  openAppLogFile(): Promise<boolean> {
    return new Promise<boolean>((resolve, reject) => {
      this._systemService
        .openLocalFile(this._appPath)
        .then(result => {
          resolve(result);
        })
        .catch(err => {
          reject(err);
        });
    });
  }
}

export default LogService;
