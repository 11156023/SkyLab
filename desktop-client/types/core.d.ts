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

interface Window {
  electronIpcRenderer: Electron.IpcRenderer;
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
  vmid: number | null;
  request_id?: string | null;
  teaching_class_id?: string | null;
  allocation_scope?: "personal" | "teaching_class";
  control_policy?: "owner" | "class_member";
  name: string;
  type: string;
  status: string;
  node?: string;
  ip_address?: string | null;
  environment_type?: string | null;
  os_info?: string | null;
  expiry_date?: string | null;
  is_placeholder?: boolean;
  can_control?: boolean;
  can_delete?: boolean;
  [key: string]: any;
}

interface SkyLabTunnelInfo {
  vmid?: number;
  name?: string;
  vm_name?: string;
  service?: string;
  host?: string;
  port?: number;
  [key: string]: any;
}

interface TunnelStatusInfo {
  running: boolean;
  lastStartTime: number;
  connectionError: string | null;
  tunnels: SkyLabTunnelInfo[];
  mode?: "wireguard";
  interfaceName?: string | null;
  latestHandshakeAt?: number | null;
}

interface SkyLabWireGuardConfig {
  mode: "wireguard";
  interface_name: string;
  interface_address: string;
  gateway_public_key: string;
  endpoint: string;
  allowed_ips: string[];
  persistent_keepalive: number;
  expires_in: number;
  connections: Array<{
    vmid: number;
    name: string;
    service: "ssh" | "rdp";
    host: string;
    port: number;
  }>;
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
