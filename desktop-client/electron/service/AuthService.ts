import { shell } from "electron";
import { BusinessError, ResponseCode } from "../core/BusinessError";
import Logger from "../core/Logger";
import SkyLabService from "./SkyLabService";
import SettingsService from "./SettingsService";

class AuthService {
  private readonly _SkyLabService: SkyLabService;
  private readonly _settingsService: SettingsService;
  private _pollTimer: NodeJS.Timeout | null = null;
  private _loginInProgress = false;

  constructor(
    SkyLabService: SkyLabService,
    settingsService: SettingsService
  ) {
    this._SkyLabService = SkyLabService;
    this._settingsService = settingsService;
  }

  async isLoggedIn(): Promise<boolean> {
    const token = await this._settingsService.getToken();
    return !!token;
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
      await shell.openExternal(dc.login_url);
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
          Logger.warn(
            "AuthService.startLogin.poll",
            (err as Error).message
          );
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
    await this._settingsService.setToken("");
  }
}

export default AuthService;
