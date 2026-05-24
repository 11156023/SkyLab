import BaseRepository from "./BaseRepository";
import GlobalConstant from "../core/GlobalConstant";

class SettingsRepository extends BaseRepository<SkyLabSettings> {
  private readonly _id = "1";

  constructor() {
    super("settings");
  }

  async get(): Promise<SkyLabSettings> {
    const existing = await this.findById(this._id);
    if (existing) return existing;
    const defaults: SkyLabSettings = {
      _id: this._id,
      backendUrl: GlobalConstant.DEFAULT_BACKEND_URL,
      token: "",
      language: GlobalConstant.DEFAULT_LANGUAGE,
      launchAtStartup: false
    };
    await this.updateById(this._id, defaults);
    return defaults;
  }

  async save(patch: Partial<SkyLabSettings>): Promise<SkyLabSettings> {
    const current = await this.get();
    const merged = { ...current, ...patch, _id: this._id };
    return this.updateById(this._id, merged);
  }
}

export default SettingsRepository;
