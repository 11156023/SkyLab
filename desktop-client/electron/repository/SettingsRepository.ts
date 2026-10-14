import BaseRepository from "./BaseRepository";
import GlobalConstant from "../core/GlobalConstant";

import { safeStorage } from "electron";

const ENCRYPTED_PREFIX = "safe:v1:";

/** 一般設定欄位；token 只能經由 saveTokens 更新。 */
export type SettingsPatch = Pick<
  SkyLabSettings,
  "language" | "backendUrl" | "launchAtStartup"
>;
export type TokenPatch = Pick<SkyLabSettings, "token" | "refreshToken">;

class SettingsRepository extends BaseRepository<SkyLabSettings> {
  private readonly _id = "1";
  private _sessionToken = "";
  private _sessionRefreshToken = "";
  // 所有讀取與寫入都排進同一條佇列、一次只跑一個：「讀出 → 合併 → 寫回」之間
  // 不會有別的寫入插隊（例如存設定時 token 剛好輪替，舊的 refresh token 被寫回）
  private _queue: Promise<unknown> = Promise.resolve();

  private serialize<R>(task: () => Promise<R>): Promise<R> {
    const run = this._queue.then(task, task);
    this._queue = run.catch(() => undefined);
    return run;
  }

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

  get(): Promise<SkyLabSettings> {
    return this.serialize(() => this.load());
  }

  /**
   * 只更新給定的一般設定欄位，token 欄位保持資料庫裡的現值；後端網址改變時
   * 一併清掉 token（舊伺服器的 token 對新伺服器沒有意義）。
   */
  saveSettings(patch: SettingsPatch): Promise<SkyLabSettings> {
    return this.serialize(async () => {
      const current = await this.load();
      const fields: Partial<SkyLabSettings> = {};
      if (patch.language !== undefined) fields.language = patch.language;
      if (patch.launchAtStartup !== undefined) {
        fields.launchAtStartup = patch.launchAtStartup;
      }
      if (patch.backendUrl !== undefined) {
        fields.backendUrl = patch.backendUrl;
        if (patch.backendUrl !== current.backendUrl?.replace(/\/$/, "")) {
          fields.token = this.encodeSecret("", false);
          fields.refreshToken = this.encodeSecret("", true);
        }
      }
      if (Object.keys(fields).length) await this.patchById(this._id, fields);
      return this.load();
    });
  }

  /** 只更新 token 欄位，一般設定保持資料庫裡的現值。 */
  saveTokens(tokens: TokenPatch): Promise<void> {
    return this.serialize(async () => {
      // 確保紀錄存在、舊格式已遷移，再只改 token 欄位
      await this.load();
      const fields: Partial<SkyLabSettings> = {};
      if (tokens.token !== undefined) {
        fields.token = this.encodeSecret(tokens.token, false);
      }
      if (tokens.refreshToken !== undefined) {
        fields.refreshToken = this.encodeSecret(tokens.refreshToken, true);
      }
      if (Object.keys(fields).length) await this.patchById(this._id, fields);
    });
  }

  /** 讀取設定（必要時做格式遷移）；只能在 serialize 佇列內呼叫。 */
  private async load(): Promise<SkyLabSettings> {
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
}

export default SettingsRepository;
