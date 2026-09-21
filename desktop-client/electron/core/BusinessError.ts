enum ResponseCode {
  SUCCESS = "A1000;successful.",
  INTERNAL_ERROR = "B1000;internal error.",
  NOT_LOGGED_IN = "B1001;Not logged in.",
  LOGIN_TIMEOUT = "B1002;Login timed out.",
  NO_TUNNELS = "B1003;No tunnels available.",
  BACKEND_ERROR = "B1005;Backend request failed.",
  WIREGUARD_NOT_INSTALLED = "B1006;WireGuard is not installed.",
  WIREGUARD_KEY_STORAGE = "B1007;Secure key storage is unavailable.",
  WIREGUARD_START_FAILED = "B1008;WireGuard tunnel failed to start.",
  WIREGUARD_INSTALL_FAILED = "B1009;WireGuard could not be installed."
}

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

export { BusinessError, ResponseCode };
