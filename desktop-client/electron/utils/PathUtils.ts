import { app } from "electron";
import path from "path";
import fs from "fs";
import FileUtils from "./FileUtils";

class PathUtils {
  public static getAppData() {
    return app.getPath("userData");
  }

  public static getConfigStoragePath() {
    const result = path.join(PathUtils.getAppData(), "config");
    FileUtils.mkdir(result);
    return result;
  }

  public static getDataBaseStoragePath() {
    const result = path.join(PathUtils.getAppData(), "db");
    FileUtils.mkdir(result);
    return result;
  }

  public static getAppLogFilePath() {
    return path.join(app.getPath("logs"), "main.log");
  }

  public static getWireGuardConfigFilePath() {
    return path.join(PathUtils.getConfigStoragePath(), "SkyLab.conf");
  }

  public static getWireGuardIdentityFilePath() {
    return path.join(
      PathUtils.getConfigStoragePath(),
      "wireguard-identity.json"
    );
  }

  public static getBundledWireGuardInstallerPath() {
    const filename = "wireguard-amd64-1.1.msi";
    const packed = path.join(
      process.resourcesPath || "",
      "wireguard",
      filename
    );
    if (app.isPackaged || fs.existsSync(packed)) return packed;
    return path.join(app.getAppPath(), "vendor", "wireguard", filename);
  }

  /** 第三方授權聲明：打包後在 resources 根目錄（electron-builder extraResources），開發時讀專案根目錄 */
  public static getThirdPartyNoticesPath() {
    const filename = "THIRD_PARTY_NOTICES.txt";
    const packed = path.join(process.resourcesPath || "", filename);
    if (app.isPackaged || fs.existsSync(packed)) return packed;
    return path.join(app.getAppPath(), filename);
  }
}

export default PathUtils;
