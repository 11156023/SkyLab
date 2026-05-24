interface ApiResponse<T> {
  bizCode: string;
  data: T;
  message: string;
}

interface ControllerParam {
  channel: string;
  event: Electron.IpcMainEvent;
  args: any;
}

interface ListenerParam {
  channel: string;
  args: any[];
}

type IpcRouter = {
  path: string;
  controller: string;
};

type Listener = {
  channel: string;
  listenerMethod: any;
};

enum IpcRouterKeys {
  AUTH = "AUTH",
  RESOURCE = "RESOURCE",
  SESSION = "SESSION",
  TUNNEL = "TUNNEL",
  SETTINGS = "SETTINGS",
  LOG = "LOG",
  SYSTEM = "SYSTEM"
}

type IpcRouters = Record<
  IpcRouterKeys,
  {
    [method: string]: IpcRouter;
  }
>;

type Listeners = Record<string, Listener>;

// ─── SkyLab domain types ───────────────────────────────────────────────

interface SkyLabSettings {
  _id?: string;
  language?: string;
  backendUrl?: string;
  token?: string;
  launchAtStartup?: boolean;
}

interface DeviceCodeResponse {
  device_code: string;
  login_url: string;
  expires_in: number;
}

interface SkyLabResource {
  vmid: number;
  name: string;
  type: string;
  status: string;
  node?: string;
  ip_address?: string;
  environment_type?: string;
  [key: string]: any;
}

interface SkyLabTunnelInfo {
  vmid?: number;
  name?: string;
  service?: string;
  visitor_port?: number;
  [key: string]: any;
}

interface SkyLabTunnelConfig {
  frpc_config: string;
  tunnels: SkyLabTunnelInfo[];
}

interface TunnelStatusInfo {
  running: boolean;
  lastStartTime: number;
  connectionError: string | null;
  tunnels: SkyLabTunnelInfo[];
}

interface SkyLabSessionStatus {
  vmid: number;
  running: boolean;
  auto_stop_at: string | null;
  auto_stop_reason: "window_grace" | "practice_quota" | null;
  minutes_until_stop: number | null;
  expiry_at: string | null;
  hours_until_expiry: number | null;
  should_warn: boolean;
  warn_reason: "auto_stop" | "expiry" | null;
  can_extend: boolean;
}

interface SkyLabExtendResult {
  vmid: number;
  auto_stop_at: string;
  extended_minutes: number;
}
