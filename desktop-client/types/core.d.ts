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
  electronIpcRenderer: SkyLabIpcRenderer;
}

interface SkyLabIpcRenderer {
  send(channel: string, args?: unknown): void;
  on(channel: string, listener: (...args: any[]) => void): void;
  removeListener(channel: string, listener: (...args: any[]) => void): void;
  removeAllListeners(channel: string): void;
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
  UPDATE = "UPDATE",
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
  refreshToken?: string;
  launchAtStartup?: boolean;
}

interface DeviceCodeResponse {
  device_code: string;
  login_url: string;
  expires_in: number;
}

interface DevicePollResult {
  status: string;
  accessToken: string | null;
  refreshToken: string | null;
}

interface SkyLabResource {
  vmid: number | null;
  request_id?: string | null;
  teaching_class_id?: string | null;
  allocation_scope?: "personal" | "teaching_class";
  control_policy?: "owner" | "class_member";
  name: string;
  type: string;
  status:
    | "scheduled"
    | "provisioning"
    | "starting"
    | "running"
    | "stopped"
    | "paused"
    | "deleting"
    | "failed"
    | "deleted"
    | "unknown";
  node?: string;
  ip_address?: string | null;
  environment_type?: string | null;
  os_info?: string | null;
  guest_os?: Record<string, unknown> | null;
  expiry_date?: string | null;
  is_placeholder?: boolean;
  can_control?: boolean;
  can_delete?: boolean;
  can_request_spec_change?: boolean;
  can_extend?: boolean;
  access_role?: "owner" | "shared" | "class_member" | "class_teacher" | "admin";
  can_manage?: boolean;
  owner_email?: string | null;
  owner_name?: string | null;
  machine_kind?:
    "personal" | "shared" | "teaching_class" | "quick_practice" | "course";
  start_blocked_reason?: "window_not_started" | "window_ended" | null;
  window_start_at?: string | null;
  window_end_at?: string | null;
  class_relation?: "student" | "teacher" | null;
  teaching_class_name?: string | null;
  course_environment_name?: string | null;
  public_urls?: string[];
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
  leaseRefreshError?: string | null;
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

interface SkyLabUpdateInfo {
  currentVersion: string;
  latestVersion: string;
  updateAvailable: boolean;
  downloadUrl: string;
}
