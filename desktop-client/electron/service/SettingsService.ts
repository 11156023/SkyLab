import { app } from "electron";
import { BusinessError, ResponseCode } from "../core/BusinessError";
import Logger from "../core/Logger";
import SettingsRepository, {
  type SettingsPatch
} from "../repository/SettingsRepository";

class SettingsService {
  private readonly _repo: SettingsRepository;

  constructor(repo: SettingsRepository) {
    this._repo = repo;
  }

  async get(): Promise<SkyLabSettings> {
    return this._repo.get();
  }

  /**
   * 存一般設定（語言、後端網址、開機啟動）。token 不經過這裡，一律走
   * setTokens／clearTokens：兩邊各自只改自己的欄位，同時進行也不會互相蓋掉。
   * 後端網址改變時由 repository 在同一次寫入中清掉 token。
   */
  async save(patch: Partial<SkyLabSettings>): Promise<SkyLabSettings> {
    const safePatch: SettingsPatch = {};
    if (typeof patch.language === "string") safePatch.language = patch.language;
    if (typeof patch.launchAtStartup === "boolean") {
      safePatch.launchAtStartup = patch.launchAtStartup;
    }
    if (typeof patch.backendUrl === "string") {
      safePatch.backendUrl = this.normalizeBackendUrl(patch.backendUrl);
    }
    const next = await this._repo.saveSettings(safePatch);
    try {
      // Electron 44 移除了 openAsHidden（原本就只有 macOS 認得）；Windows 的
      // 開機啟動只需要 openAtLogin。
      app.setLoginItemSettings({
        openAtLogin: !!next.launchAtStartup
      });
    } catch (e) {
      Logger.error("SettingsService.save", e as Error);
    }
    return next;
  }

  async getLanguage(): Promise<string> {
    const s = await this.get();
    return s.language;
  }

  async saveLanguage(language: string): Promise<void> {
    await this.save({ language });
  }

  async getToken(): Promise<string> {
    return (await this.get()).token;
  }

  async setToken(token: string): Promise<void> {
    await this._repo.saveTokens({ token });
  }

  async getRefreshToken(): Promise<string> {
    return (await this.get()).refreshToken || "";
  }

  async setTokens(token: string, refreshToken: string): Promise<void> {
    await this._repo.saveTokens({ token, refreshToken });
  }

  async clearTokens(): Promise<void> {
    await this.setTokens("", "");
  }

  /** 學校平台的網址；還沒填（第一次開啟）時丟 BACKEND_NOT_CONFIGURED。 */
  async getBackendUrl(): Promise<string> {
    const backendUrl = await this.getConfiguredBackendUrl();
    if (!backendUrl) {
      throw new BusinessError(ResponseCode.BACKEND_NOT_CONFIGURED);
    }
    return backendUrl;
  }

  /** 同 getBackendUrl，但還沒填時回空字串（給「是不是學校平台的網址」這類判斷用）。 */
  async getConfiguredBackendUrl(): Promise<string> {
    const settings = await this.get();
    if (!settings.backendUrl) return "";
    return this.normalizeBackendUrl(settings.backendUrl);
  }

  /**
   * 「ntub.skylab-tw.com」補成 https://；從瀏覽器網址列貼上的
   * 「https://ntub.skylab-tw.com/login」只留來源（平台一律架在網域根目錄）。
   */
  private normalizeBackendUrl(value: string): string {
    const trimmed = value.trim();
    let url: URL;
    try {
      url = new URL(
        /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed)
          ? trimmed
          : `https://${trimmed}`
      );
    } catch {
      throw new Error("Backend URL is invalid");
    }
    const isLocal =
      url.hostname === "localhost" || url.hostname === "127.0.0.1";
    if (url.protocol !== "https:" && !(isLocal && url.protocol === "http:")) {
      throw new Error("Backend URL must use HTTPS");
    }
    if (url.username || url.password) {
      throw new Error("Backend URL must not contain credentials");
    }
    return url.origin;
  }
}

export default SettingsService;
