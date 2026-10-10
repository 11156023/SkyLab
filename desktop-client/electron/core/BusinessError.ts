/* 錯誤碼;英文訊息。英文訊息與 detail 只進記錄檔，畫面一律依錯誤碼顯示
   src/lang 的 errors.Bxxxx 在地化文案；新增錯誤碼時三個語系要一起補 */
enum ResponseCode {
  SUCCESS = "A1000;successful.",
  INTERNAL_ERROR = "B1000;internal error.",
  NOT_LOGGED_IN = "B1001;Not logged in.",
  LOGIN_TIMEOUT = "B1002;Login timed out.",
  BACKEND_ERROR = "B1005;Backend request failed.",
  WIREGUARD_NOT_INSTALLED = "B1006;WireGuard is not installed.",
  WIREGUARD_KEY_STORAGE = "B1007;Secure key storage is unavailable.",
  WIREGUARD_START_FAILED = "B1008;WireGuard tunnel failed to start.",
  WIREGUARD_INSTALL_FAILED = "B1009;WireGuard could not be installed.",
  UPDATE_INSTALL_FAILED = "B1010;Update installation failed.",
  ADMIN_CANCELLED = "B1011;Administrator permission was declined.",
  ADMIN_FAILED = "B1012;Administrator action failed.",
  TUNNEL_EXPIRED = "B1013;The secure session expired. Reconnect to continue.",
  TUNNEL_ORPHANED = "B1014;A tunnel from an earlier app session needs to be reconnected.",
  TUNNEL_CONFIG_CHANGED = "B1015;The WireGuard network configuration changed. Reconnect to apply it.",
  TUNNEL_STOP_FAILED = "B1016;WireGuard tunnel service could not be removed.",
  BACKEND_UNREACHABLE = "B1017;Could not reach the server.",
  OPEN_FAILED = "B1018;Could not open the requested item."
}

/** ResponseCode 的錯誤碼部分，例如 "B1011" */
const bizCodeOf = (code: ResponseCode): string => code.split(";")[0];

class BusinessError extends Error {
  private readonly _bizCode: string;

  constructor(bizErrorEnum: ResponseCode, detail?: string) {
    const [bizCode, message] = bizErrorEnum.split(";");
    super(detail ? `${message} ${detail}` : message);
    this._bizCode = bizCode;
    this.name = "BusinessError";
  }

  get bizCode(): string {
    return this._bizCode;
  }
}

export { BusinessError, ResponseCode, bizCodeOf };
