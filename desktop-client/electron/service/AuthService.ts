import { shell } from "electron";
import { BusinessError, ResponseCode } from "../core/BusinessError";
import Logger from "../core/Logger";
import SkyLabService from "./SkyLabService";
import SettingsService from "./SettingsService";
import WireGuardTunnelService from "./WireGuardTunnelService";

class AuthService {
  private readonly _SkyLabService: SkyLabService;
  private readonly _settingsService: SettingsService;
  private readonly _tunnelService: WireGuardTunnelService;
  private _pollTimer: NodeJS.Timeout | null = null;
  private _loginInProgress = false;

  constructor(
    SkyLabService: SkyLabService,
    settingsService: SettingsService,
    tunnelService: WireGuardTunnelService
  ) {
    this._SkyLabService = SkyLabService;
    this._settingsService = settingsService;
    this._tunnelService = tunnelService;
  }

  private _tokenIsUsable(token: string): boolean {
    try {
      const parts = token.split(".");
      if (parts.length !== 3) return false;
      const payload = JSON.parse(
        Buffer.from(parts[1], "base64url").toString("utf-8")
      ) as { exp?: unknown };
      return (
        typeof payload.exp === "number" &&
        Number.isFinite(payload.exp) &&
        payload.exp > Math.floor(Date.now() / 1000) + 30
      );
    } catch {
      return false;
    }
  }

  async isLoggedIn(): Promise<boolean> {
    const token = await this._settingsService.getToken();
    if (!token) return false;
    if (!this._tokenIsUsable(token)) {
      await this._settingsService.setToken("");
      return false;
    }
    return true;
  }

  isLoginInProgress(): boolean {
    return this._loginInProgress;
  }

  /**
   * Start device-code login. Opens the login URL in the user's browser,
   * then polls the backend until approved. Calls onResult when the flow
   * finishes (success or failure).
   */
  async startLogin(
    onResult: (success: boolean, error?: string) => void
  ): Promise<void> {
    if (this._loginInProgress) {
      throw new BusinessError(
        ResponseCode.INTERNAL_ERROR,
        "Login already in progress"
      );
    }
    this._loginInProgress = true;

    try {
      const dc = await this._SkyLabService.requestDeviceCode();
      // 只允許在瀏覽器開啟 http(s) 連結；後端若被竄改回傳 file:// 或自訂 scheme，
      // shell.openExternal 會直接交給作業系統執行，必須先擋掉。
      let loginUrl: URL;
      try {
        loginUrl = new URL(dc.login_url);
      } catch {
        throw new BusinessError(
          ResponseCode.INTERNAL_ERROR,
          "Backend returned an invalid login URL"
        );
      }
      if (loginUrl.protocol !== "https:" && loginUrl.protocol !== "http:") {
        throw new BusinessError(
          ResponseCode.INTERNAL_ERROR,
          `Refusing to open login URL with scheme ${loginUrl.protocol}`
        );
      }
      await shell.openExternal(loginUrl.toString());
      const expiresAt = Date.now() + dc.expires_in * 1000;

      const poll = async () => {
        if (Date.now() > expiresAt) {
          this._loginInProgress = false;
          this._pollTimer = null;
          onResult(false, "login timed out");
          return;
        }
        try {
          const result = await this._SkyLabService.pollDeviceCode(
            dc.device_code
          );
          if (result.status === "approved" && result.accessToken) {
            await this._settingsService.setToken(result.accessToken);
            this._loginInProgress = false;
            this._pollTimer = null;
            onResult(true);
            return;
          }
          this._pollTimer = setTimeout(poll, 2000);
        } catch (err) {
          Logger.warn("AuthService.startLogin.poll", (err as Error).message);
          this._loginInProgress = false;
          this._pollTimer = null;
          onResult(false, (err as Error).message);
        }
      };

      this._pollTimer = setTimeout(poll, 2000);
    } catch (err) {
      this._loginInProgress = false;
      throw err;
    }
  }

  cancelLogin() {
    if (this._pollTimer) {
      clearTimeout(this._pollTimer);
      this._pollTimer = null;
    }
    this._loginInProgress = false;
  }

  async logout(): Promise<void> {
    this.cancelLogin();
    const token = await this._settingsService.getToken();
    if (token) {
      try {
        await this._tunnelService.stopTunnel();
      } catch (error) {
        Logger.error("AuthService.logout.stopTunnel", error as Error);
      }
      try {
        await this._SkyLabService.logout();
      } catch (error) {
        Logger.error("AuthService.logout.revokeToken", error as Error);
      }
    }
    await this._settingsService.setToken("");
  }
}

export default AuthService;
