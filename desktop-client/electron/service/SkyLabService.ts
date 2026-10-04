import { net } from "electron";
import { BusinessError, ResponseCode } from "../core/BusinessError";
import Logger from "../core/Logger";
import SettingsService from "./SettingsService";

type HttpResult = {
  status: number;
  body: string;
};

class SkyLabService {
  private readonly _settingsService: SettingsService;
  private _refreshPromise: Promise<boolean> | null = null;

  constructor(settingsService: SettingsService) {
    this._settingsService = settingsService;
  }

  private async requestOnce(
    method: string,
    pathname: string,
    options: { auth?: boolean; body?: any } = {}
  ): Promise<HttpResult> {
    const backendUrl = await this._settingsService.getBackendUrl();
    const url = backendUrl.replace(/\/$/, "") + pathname;
    return new Promise((resolve, reject) => {
      const req = net.request({ method, url });
      let timeout: NodeJS.Timeout | null = null;
      const clearRequestTimeout = () => {
        if (timeout) clearTimeout(timeout);
        timeout = null;
      };
      if (options.body !== undefined) {
        req.setHeader("Content-Type", "application/json");
      }
      this._settingsService
        .getToken()
        .then(token => {
          if (options.auth && token) {
            req.setHeader("Authorization", `Bearer ${token}`);
          }
          const chunks: Buffer[] = [];
          req.on("response", response => {
            response.on("data", chunk => chunks.push(chunk));
            response.on("end", () => {
              clearRequestTimeout();
              resolve({
                status: response.statusCode,
                body: Buffer.concat(chunks).toString("utf-8")
              });
            });
            response.on("error", (err: Error) => {
              clearRequestTimeout();
              reject(err);
            });
          });
          req.on("error", (err: Error) => {
            clearRequestTimeout();
            reject(new BusinessError(ResponseCode.BACKEND_ERROR, err.message));
          });
          if (options.body !== undefined) {
            req.write(JSON.stringify(options.body));
          }
          timeout = setTimeout(() => {
            reject(
              new BusinessError(
                ResponseCode.BACKEND_ERROR,
                "Request timed out."
              )
            );
            req.abort();
          }, 20_000);
          req.end();
        })
        .catch(reject);
    });
  }

  async refreshSession(): Promise<boolean> {
    if (this._refreshPromise) return this._refreshPromise;
    this._refreshPromise = (async () => {
      const refreshToken = await this._settingsService.getRefreshToken();
      if (!refreshToken) return false;
      const res = await this.requestOnce(
        "POST",
        "/api/v1/login/refresh-token",
        { body: { refresh_token: refreshToken } }
      );
      if (res.status === 400 || res.status === 401 || res.status === 403) {
        return false;
      }
      if (res.status !== 200) {
        throw new BusinessError(ResponseCode.BACKEND_ERROR, res.body);
      }
      const data = JSON.parse(res.body) as {
        access_token?: string;
        refresh_token?: string;
      };
      if (!data.access_token || !data.refresh_token) return false;
      await this._settingsService.setTokens(
        data.access_token,
        data.refresh_token
      );
      return true;
    })();
    try {
      return await this._refreshPromise;
    } finally {
      this._refreshPromise = null;
    }
  }

  private async request(
    method: string,
    pathname: string,
    options: { auth?: boolean; body?: any } = {}
  ): Promise<HttpResult> {
    let res = await this.requestOnce(method, pathname, options);
    if (!options.auth || res.status !== 401) return res;

    if (await this.refreshSession()) {
      res = await this.requestOnce(method, pathname, options);
      if (res.status !== 401) return res;
    }

    await this._settingsService.clearTokens();
    throw new BusinessError(ResponseCode.NOT_LOGGED_IN);
  }

  async requestDeviceCode(): Promise<DeviceCodeResponse> {
    const res = await this.request(
      "POST",
      "/api/v1/desktop-client/auth/device-code",
      { body: {} }
    );
    if (res.status !== 200) {
      Logger.warn(
        "SkyLabService.requestDeviceCode",
        `status=${res.status} body=${res.body}`
      );
      throw new BusinessError(ResponseCode.BACKEND_ERROR, res.body);
    }
    return JSON.parse(res.body) as DeviceCodeResponse;
  }

  async pollDeviceCode(code: string): Promise<DevicePollResult> {
    const res = await this.request(
      "GET",
      `/api/v1/desktop-client/auth/poll?code=${encodeURIComponent(code)}`
    );
    Logger.info("SkyLabService.pollDeviceCode", `status=${res.status}`);
    if (res.status === 404) {
      throw new BusinessError(
        ResponseCode.LOGIN_TIMEOUT,
        "device code expired"
      );
    }
    if (res.status !== 200) {
      throw new BusinessError(ResponseCode.BACKEND_ERROR, res.body);
    }
    const data = JSON.parse(res.body);
    return {
      status: data.status,
      accessToken: data.access_token || null,
      refreshToken: data.refresh_token || null
    };
  }

  async logout(): Promise<void> {
    try {
      await this.refreshSession();
    } catch (error) {
      Logger.warn("SkyLabService.logout.refresh", (error as Error).message);
    }
    const refreshToken = await this._settingsService.getRefreshToken();
    const res = await this.requestOnce("POST", "/api/v1/login/logout", {
      auth: true,
      body: refreshToken ? { refresh_token: refreshToken } : {}
    });
    if (res.status !== 200 && res.status !== 401) {
      throw new BusinessError(ResponseCode.BACKEND_ERROR, res.body);
    }
  }

  async listResources(): Promise<SkyLabResource[]> {
    const res = await this.request("GET", "/api/v1/resources/my", {
      auth: true
    });
    if (res.status === 401) {
      throw new BusinessError(ResponseCode.NOT_LOGGED_IN);
    }
    if (res.status !== 200) {
      throw new BusinessError(ResponseCode.BACKEND_ERROR, res.body);
    }
    return JSON.parse(res.body) as SkyLabResource[];
  }

  async getSessionStatus(vmid: number): Promise<SkyLabSessionStatus> {
    const res = await this.request(
      "GET",
      `/api/v1/resources/${vmid}/session-status`,
      { auth: true }
    );
    if (res.status === 401) {
      throw new BusinessError(ResponseCode.NOT_LOGGED_IN);
    }
    if (res.status !== 200) {
      throw new BusinessError(ResponseCode.BACKEND_ERROR, res.body);
    }
    return JSON.parse(res.body) as SkyLabSessionStatus;
  }

  async listSessionStatuses(): Promise<SkyLabSessionStatus[]> {
    const res = await this.request(
      "GET",
      "/api/v1/resources/my/session-status",
      { auth: true }
    );
    if (res.status !== 200) {
      throw new BusinessError(ResponseCode.BACKEND_ERROR, res.body);
    }
    return JSON.parse(res.body) as SkyLabSessionStatus[];
  }

  async extendSession(vmid: number): Promise<SkyLabExtendResult> {
    const res = await this.request(
      "POST",
      `/api/v1/resources/${vmid}/extend-session`,
      { auth: true, body: {} }
    );
    if (res.status === 401) {
      throw new BusinessError(ResponseCode.NOT_LOGGED_IN);
    }
    if (res.status !== 200) {
      throw new BusinessError(ResponseCode.BACKEND_ERROR, res.body);
    }
    return JSON.parse(res.body) as SkyLabExtendResult;
  }

  async connectWireGuard(
    deviceId: string,
    publicKey: string
  ): Promise<SkyLabWireGuardConfig> {
    const res = await this.request(
      "POST",
      "/api/v1/desktop-client/wireguard/connect",
      {
        auth: true,
        body: { device_id: deviceId, public_key: publicKey }
      }
    );
    if (res.status === 401) {
      throw new BusinessError(ResponseCode.NOT_LOGGED_IN);
    }
    if (res.status !== 200) {
      throw new BusinessError(ResponseCode.BACKEND_ERROR, res.body);
    }
    return JSON.parse(res.body) as SkyLabWireGuardConfig;
  }

  async refreshWireGuard(deviceId: string): Promise<SkyLabWireGuardConfig> {
    const res = await this.request(
      "POST",
      "/api/v1/desktop-client/wireguard/refresh",
      { auth: true, body: { device_id: deviceId } }
    );
    if (res.status === 401) {
      throw new BusinessError(ResponseCode.NOT_LOGGED_IN);
    }
    if (res.status !== 200) {
      throw new BusinessError(ResponseCode.BACKEND_ERROR, res.body);
    }
    return JSON.parse(res.body) as SkyLabWireGuardConfig;
  }

  async disconnectWireGuard(deviceId: string): Promise<void> {
    const res = await this.request(
      "POST",
      "/api/v1/desktop-client/wireguard/disconnect",
      { auth: true, body: { device_id: deviceId } }
    );
    if (res.status === 401) {
      throw new BusinessError(ResponseCode.NOT_LOGGED_IN);
    }
    if (res.status !== 200) {
      throw new BusinessError(ResponseCode.BACKEND_ERROR, res.body);
    }
  }
}

export default SkyLabService;
