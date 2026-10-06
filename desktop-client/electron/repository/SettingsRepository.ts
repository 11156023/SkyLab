import BaseRepository from "./BaseRepository";
import GlobalConstant from "../core/GlobalConstant";

import { safeStorage } from "electron";

const ENCRYPTED_PREFIX = "safe:v1:";

class SettingsRepository extends BaseRepository<SkyLabSettings> {
  private readonly _id = "1";
  private _sessionToken = "";
  private _sessionRefreshToken = "";

  private decodeSecret(value: string | undefined, refresh: boolean): string {
    const cached = refresh ? this._sessionRefreshToken : this._sessionToken;
    if (!value) return cached;
    if (!value.startsWith(ENCRYPTED_PREFIX)) {
      if (refresh) this._sessionRefreshToken = value;
      else this._sessionToken = value;
      return value;
    }
    try {
      const decoded = safeStorage.decryptString(
        Buffer.from(value.slice(ENCRYPTED_PREFIX.length), "base64")
      );
      if (refresh) this._sessionRefreshToken = decoded;
      else this._sessionToken = decoded;
      return decoded;
    } catch {
      return "";
    }
  }

  private encodeSecret(value: string | undefined, refresh: boolean): string {
    const normalized = value || "";
    if (refresh) this._sessionRefreshToken = normalized;
    else this._sessionToken = normalized;
    if (!normalized || !safeStorage.isEncryptionAvailable()) return "";
    return (
      ENCRYPTED_PREFIX +
      safeStorage.encryptString(normalized).toString("base64")
    );
  }

  constructor() {
    super("settings");
  }

  async get(): Promise<SkyLabSettings> {
    const existing = await this.findById(this._id);
    if (existing) {
      let languageMigrated = false;
      if (existing.language === "zh-CN") {
        existing.language = "zh-TW";
        languageMigrated = true;
      }
      // Move only retired built-in defaults to the current production site;
      // keep user-configured servers intact.
      if (
        !existing.backendUrl ||
        [
          GlobalConstant.LEGACY_BACKEND_URL,
          GlobalConstant.LEGACY_PUBLIC_BACKEND_URL
        ].includes(existing.backendUrl.replace(/\/$/, ""))
      ) {
        return this.updateById(this._id, {
          ...existing,
          backendUrl: GlobalConstant.DEFAULT_BACKEND_URL,
          token: "",
          refreshToken: ""
        });
      }
      const token = this.decodeSecret(existing.token, false);
      const refreshToken = this.decodeSecret(existing.refreshToken, true);
      if (
        languageMigrated ||
        (existing.token && !existing.token.startsWith(ENCRYPTED_PREFIX)) ||
        (existing.refreshToken &&
          !existing.refreshToken.startsWith(ENCRYPTED_PREFIX))
      ) {
        await this.updateById(this._id, {
          ...existing,
          token: this.encodeSecret(token, false),
          refreshToken: this.encodeSecret(refreshToken, true)
        });
      }
      return { ...existing, token, refreshToken };
    }
    const defaults: SkyLabSettings = {
      _id: this._id,
      backendUrl: GlobalConstant.DEFAULT_BACKEND_URL,
      token: "",
      refreshToken: "",
      language: GlobalConstant.DEFAULT_LANGUAGE,
      launchAtStartup: false
    };
    await this.updateById(this._id, defaults);
    return defaults;
  }

  async save(patch: Partial<SkyLabSettings>): Promise<SkyLabSettings> {
    const current = await this.get();
    const merged = { ...current, ...patch, _id: this._id };
    await this.updateById(this._id, {
      ...merged,
      token: this.encodeSecret(merged.token, false),
      refreshToken: this.encodeSecret(merged.refreshToken, true)
    });
    return merged;
  }
}

export default SettingsRepository;
