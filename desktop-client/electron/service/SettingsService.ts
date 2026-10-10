import { app } from "electron";
import Logger from "../core/Logger";
import GlobalConstant from "../core/GlobalConstant";
import SettingsRepository from "../repository/SettingsRepository";
import BackendUrlUtils, { BackendUrlProblem } from "../utils/BackendUrlUtils";

/* 存檔被擋下時回給畫面的訊息（設定頁會先用同一套規則即時提示，正常不會走到這裡） */
const BACKEND_URL_ERRORS: Record<BackendUrlProblem, string> = {
  required: "Backend URL is invalid",
  invalid: "Backend URL is invalid",
  insecure: "Backend URL must use HTTPS",
  extra: "Backend URL must not contain credentials, query, or fragment"
};

class SettingsService {
  private readonly _repo: SettingsRepository;

  constructor(repo: SettingsRepository) {
    this._repo = repo;
  }

  async get(): Promise<SkyLabSettings> {
    return this._repo.get();
  }

  async save(patch: Partial<SkyLabSettings>): Promise<SkyLabSettings> {
    const current = await this._repo.get();
    const safePatch: Partial<SkyLabSettings> = {};
    if (typeof patch.language === "string") safePatch.language = patch.language;
    if (typeof patch.launchAtStartup === "boolean") {
      safePatch.launchAtStartup = patch.launchAtStartup;
    }
    if (typeof patch.token === "string") safePatch.token = patch.token;
    if (typeof patch.refreshToken === "string") {
      safePatch.refreshToken = patch.refreshToken;
    }
    if (typeof patch.backendUrl === "string") {
      const backendUrl = this.normalizeBackendUrl(patch.backendUrl);
      safePatch.backendUrl = backendUrl;
      if (backendUrl !== current.backendUrl?.replace(/\/$/, "")) {
        safePatch.token = "";
        safePatch.refreshToken = "";
      }
    }
    const next = await this._repo.save(safePatch);
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
    await this.save({ token });
  }

  async getRefreshToken(): Promise<string> {
    return (await this.get()).refreshToken || "";
  }

  async setTokens(token: string, refreshToken: string): Promise<void> {
    await this.save({ token, refreshToken });
  }

  async clearTokens(): Promise<void> {
    await this.setTokens("", "");
  }

  async getBackendUrl(): Promise<string> {
    const settings = await this.get();
    try {
      return this.normalizeBackendUrl(settings.backendUrl);
    } catch {
      const migrated = await this.save({
        backendUrl: GlobalConstant.DEFAULT_BACKEND_URL
      });
      return migrated.backendUrl;
    }
  }

  private normalizeBackendUrl(value: string): string {
    const result = BackendUrlUtils.parse(value);
    if (result.ok === false)
      throw new Error(BACKEND_URL_ERRORS[result.problem]);
    return result.url;
  }
}

export default SettingsService;
