import { app } from "electron";
import Logger from "../core/Logger";
import SettingsRepository from "../repository/SettingsRepository";

class SettingsService {
  private readonly _repo: SettingsRepository;

  constructor(repo: SettingsRepository) {
    this._repo = repo;
  }

  async get(): Promise<SkyLabSettings> {
    return this._repo.get();
  }

  async save(patch: Partial<SkyLabSettings>): Promise<SkyLabSettings> {
    const next = await this._repo.save(patch);
    try {
      app.setLoginItemSettings({
        openAtLogin: !!next.launchAtStartup,
        openAsHidden: !!next.launchAtStartup
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

  async getBackendUrl(): Promise<string> {
    return (await this.get()).backendUrl;
  }
}

export default SettingsService;
