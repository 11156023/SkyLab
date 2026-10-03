import { execFile, spawn } from "child_process";
import {
  createHash,
  createPrivateKey,
  createPublicKey,
  generateKeyPairSync,
  randomUUID
} from "crypto";
import { app, BrowserWindow, Notification, safeStorage } from "electron";
import fs from "fs";
import { isIP } from "net";
import path from "path";
import BeanFactory from "../core/BeanFactory";
import { BusinessError, ResponseCode } from "../core/BusinessError";
import GlobalConstant from "../core/GlobalConstant";
import Logger from "../core/Logger";
import PathUtils from "../utils/PathUtils";
import ResponseUtils from "../utils/ResponseUtils";
import SkyLabService from "./SkyLabService";

type WireGuardIdentity = {
  version: 1;
  deviceId: string;
  publicKey: string;
  encryptedPrivateKey: string;
};

type DecryptedIdentity = {
  deviceId: string;
  publicKey: string;
  privateKey: string;
};

type TunnelServiceState = "missing" | "stopped" | "running";

const TUNNEL_NAME = "SkyLab";
const SERVICE_NAME = `WireGuardTunnel$${TUNNEL_NAME}`;
const LEASE_REFRESH_INTERVAL_MS = 5 * 60 * 1000;
const LEASE_REFRESH_RETRY_MS = 60 * 1000;
const ORPHANED_TUNNEL_ERROR =
  "A tunnel from an earlier app session needs to be reconnected.";
const WIREGUARD_MSI_SHA256 =
  "6daa5d37a9e2950dfb8c48b95ab8e562cb2bad1c785d020f38f97bea4c6a5566";
const PRIVATE_KEY_DER_PREFIX = Buffer.from(
  "302e020100300506032b656e04220420",
  "hex"
);

class WireGuardTunnelService {
  private readonly _SkyLabService: SkyLabService;
  private _listener: NodeJS.Timeout | null = null;
  private _lastStartTime = -1;
  private _notifiedStartTime = -1;
  private _connectionError: string | null = null;
  private _connections: SkyLabTunnelInfo[] = [];
  private _latestHandshakeAt: number | null = null;
  private _expiresAt: number | null = null;
  private _polling = false;
  private _lastLeaseRefreshAt = -1;
  private _lastLeaseRefreshAttemptAt = -1;
  private _refreshPromise: Promise<void> | null = null;
  private _startPromise: Promise<void> | null = null;
  private _operationTail: Promise<void> = Promise.resolve();
  private _operationRevision = 0;
  private _leaseRefreshError: string | null = null;
  private _activeConfigFingerprint: string | null = null;

  constructor() {
    this._SkyLabService = BeanFactory.getBean("SkyLabService");
  }

  get lastStartTime(): number {
    return this._lastStartTime;
  }

  get tunnels(): SkyLabTunnelInfo[] {
    return this._connections;
  }

  get connectionError(): string | null {
    return this._connectionError;
  }

  private _wireGuardExecutable(): string {
    if (process.platform !== "win32") {
      throw new BusinessError(
        ResponseCode.WIREGUARD_NOT_INSTALLED,
        "This build currently supports the official WireGuard for Windows client."
      );
    }
    const candidates = [
      process.env.SKYLAB_WIREGUARD_EXECUTABLE,
      process.env.ProgramW6432 &&
        path.join(process.env.ProgramW6432, "WireGuard", "wireguard.exe"),
      process.env.ProgramFiles &&
        path.join(process.env.ProgramFiles, "WireGuard", "wireguard.exe"),
      "C:\\Program Files\\WireGuard\\wireguard.exe"
    ].filter((value): value is string => !!value);
    const executable = candidates.find(value => fs.existsSync(value));
    if (!executable) {
      throw new BusinessError(
        ResponseCode.WIREGUARD_NOT_INSTALLED,
        "Install the official WireGuard for Windows app, then try again."
      );
    }
    return executable;
  }

  private _verifyBundledInstaller(installerPath: string): void {
    if (!fs.existsSync(installerPath)) {
      throw new BusinessError(
        ResponseCode.WIREGUARD_INSTALL_FAILED,
        "The bundled installer is missing."
      );
    }
    const digest = createHash("sha256")
      .update(fs.readFileSync(installerPath))
      .digest("hex");
    if (digest !== WIREGUARD_MSI_SHA256) {
      throw new BusinessError(
        ResponseCode.WIREGUARD_INSTALL_FAILED,
        "The bundled installer failed its integrity check."
      );
    }
  }

  private async _ensureWireGuardInstalled(): Promise<void> {
    try {
      this._wireGuardExecutable();
      return;
    } catch (error) {
      if (
        !(error instanceof BusinessError) ||
        error.bizCode !== ResponseCode.WIREGUARD_NOT_INSTALLED.split(";")[0]
      ) {
        throw error;
      }
    }

    const installerPath = PathUtils.getBundledWireGuardInstallerPath();
    this._verifyBundledInstaller(installerPath);
    Logger.info(
      "WireGuardTunnelService.ensureWireGuardInstalled",
      "Installing bundled, signed WireGuard for Windows prerequisite"
    );
    await this._runElevatedProcess("msiexec.exe", [
      "/i",
      installerPath,
      "/qn",
      "/norestart",
      "DO_NOT_LAUNCH=1"
    ]);
    this._wireGuardExecutable();
  }

  private _wgExecutable(): string | null {
    const manager = this._wireGuardExecutable();
    const candidate = path.join(path.dirname(manager), "wg.exe");
    return fs.existsSync(candidate) ? candidate : null;
  }

  private _derivePublicKey(privateKey: Buffer): string {
    const keyObject = createPrivateKey({
      key: Buffer.concat([PRIVATE_KEY_DER_PREFIX, privateKey]),
      format: "der",
      type: "pkcs8"
    });
    const publicDer = createPublicKey(keyObject).export({
      format: "der",
      type: "spki"
    });
    return Buffer.from(publicDer).subarray(-32).toString("base64");
  }

  private _createIdentity(): DecryptedIdentity {
    const pair = generateKeyPairSync("x25519");
    const privateDer = pair.privateKey.export({ format: "der", type: "pkcs8" });
    const publicDer = pair.publicKey.export({ format: "der", type: "spki" });
    const privateKey = Buffer.from(privateDer).subarray(-32).toString("base64");
    const publicKey = Buffer.from(publicDer).subarray(-32).toString("base64");
    return { deviceId: randomUUID(), privateKey, publicKey };
  }

  private _loadIdentity(): DecryptedIdentity {
    if (!safeStorage.isEncryptionAvailable()) {
      throw new BusinessError(ResponseCode.WIREGUARD_KEY_STORAGE);
    }
    const identityPath = PathUtils.getWireGuardIdentityFilePath();
    if (!fs.existsSync(identityPath)) {
      const identity = this._createIdentity();
      const stored: WireGuardIdentity = {
        version: 1,
        deviceId: identity.deviceId,
        publicKey: identity.publicKey,
        encryptedPrivateKey: safeStorage
          .encryptString(identity.privateKey)
          .toString("base64")
      };
      fs.writeFileSync(identityPath, JSON.stringify(stored), {
        encoding: "utf-8",
        mode: 0o600
      });
      return identity;
    }

    const stored = JSON.parse(
      fs.readFileSync(identityPath, "utf-8")
    ) as WireGuardIdentity;
    if (
      stored.version !== 1 ||
      !/^[A-Za-z0-9._:-]{8,128}$/.test(stored.deviceId) ||
      !this._validWireGuardKey(stored.publicKey)
    ) {
      throw new BusinessError(
        ResponseCode.WIREGUARD_KEY_STORAGE,
        "The saved WireGuard identity is invalid."
      );
    }
    const privateKey = safeStorage.decryptString(
      Buffer.from(stored.encryptedPrivateKey, "base64")
    );
    const rawPrivateKey = Buffer.from(privateKey, "base64");
    if (
      rawPrivateKey.length !== 32 ||
      this._derivePublicKey(rawPrivateKey) !== stored.publicKey
    ) {
      throw new BusinessError(
        ResponseCode.WIREGUARD_KEY_STORAGE,
        "The saved WireGuard key pair is invalid."
      );
    }
    return {
      deviceId: stored.deviceId,
      publicKey: stored.publicKey,
      privateKey
    };
  }

  private _validWireGuardKey(value: string): boolean {
    try {
      if (!/^[A-Za-z0-9+/]{43}=$/.test(value)) return false;
      const decoded = Buffer.from(value, "base64");
      return (
        decoded.length === 32 &&
        decoded.some(byte => byte !== 0) &&
        decoded.toString("base64") === value
      );
    } catch {
      return false;
    }
  }

  private _singleLine(value: string, label: string): string {
    const normalized = String(value || "").trim();
    if (!normalized || /[\r\n]/.test(normalized)) {
      throw new BusinessError(
        ResponseCode.WIREGUARD_START_FAILED,
        `Invalid ${label}.`
      );
    }
    return normalized;
  }

  private _buildConfig(
    identity: DecryptedIdentity,
    config: SkyLabWireGuardConfig
  ): string {
    if (!this._validWireGuardKey(config.gateway_public_key)) {
      throw new BusinessError(
        ResponseCode.WIREGUARD_START_FAILED,
        "Invalid Gateway public key."
      );
    }
    const interfaceAddress = this._singleLine(
      config.interface_address,
      "interface address"
    );
    const [address] = interfaceAddress.split("/");
    if (isIP(address) !== 4) {
      throw new BusinessError(
        ResponseCode.WIREGUARD_START_FAILED,
        "Invalid interface address."
      );
    }
    const endpoint = this._singleLine(config.endpoint, "Gateway endpoint");
    const allowedIps = config.allowed_ips.map(value =>
      this._singleLine(value, "allowed IP range")
    );
    if (!allowedIps.length) {
      throw new BusinessError(
        ResponseCode.WIREGUARD_START_FAILED,
        "No allowed IP ranges were returned."
      );
    }
    const keepalive = Math.max(
      0,
      Math.min(120, Number(config.persistent_keepalive) || 0)
    );
    return [
      "[Interface]",
      `PrivateKey = ${identity.privateKey}`,
      `Address = ${interfaceAddress}`,
      "",
      "[Peer]",
      `PublicKey = ${config.gateway_public_key}`,
      `Endpoint = ${endpoint}`,
      `AllowedIPs = ${allowedIps.join(", ")}`,
      `PersistentKeepalive = ${keepalive}`,
      ""
    ].join("\n");
  }

  private _powershellQuote(value: string): string {
    return `'${value.replace(/'/g, "''")}'`;
  }

  private _runElevatedProcess(
    executable: string,
    args: string[]
  ): Promise<void> {
    const argumentList = args
      .map(value => this._powershellQuote(value))
      .join(",");
    const command = [
      `$p = Start-Process -FilePath ${this._powershellQuote(executable)}`,
      `-ArgumentList @(${argumentList}) -Verb RunAs -Wait -PassThru -WindowStyle Hidden`,
      "; if ($p.ExitCode -eq 0 -or $p.ExitCode -eq 3010) { exit 0 } else { exit 1 }"
    ].join(" ");
    return new Promise((resolve, reject) => {
      const child = spawn(
        "powershell.exe",
        ["-NoProfile", "-NonInteractive", "-Command", command],
        { windowsHide: true, stdio: "ignore" }
      );
      child.once("error", reject);
      child.once("exit", code => {
        if (code === 0) resolve();
        else reject(new Error(`Administrator action exited ${code}`));
      });
    });
  }

  private _runElevatedWireGuard(args: string[]): Promise<void> {
    return this._runElevatedProcess(this._wireGuardExecutable(), args);
  }

  private _execFile(command: string, args: string[]): Promise<string> {
    return new Promise((resolve, reject) => {
      execFile(command, args, { windowsHide: true }, (error, stdout) => {
        if (error) reject(error);
        else resolve(stdout);
      });
    });
  }

  private async _serviceState(): Promise<TunnelServiceState> {
    if (process.platform !== "win32") return "missing";
    try {
      const output = await this._execFile("sc.exe", ["query", SERVICE_NAME]);
      return /STATE\s*:\s*4\s+RUNNING/i.test(output) ? "running" : "stopped";
    } catch (error) {
      const queryError = error as Error & {
        code?: number | string;
        stdout?: string | Buffer;
        stderr?: string | Buffer;
      };
      const output = [
        queryError.message,
        String(queryError.stdout || ""),
        String(queryError.stderr || "")
      ].join("\n");
      if (
        queryError.code === 1060 ||
        /(?:FAILED\s+1060|does not exist as an installed service)/i.test(output)
      ) {
        return "missing";
      }
      throw error;
    }
  }

  async isRunning(): Promise<boolean> {
    try {
      return (await this._serviceState()) === "running";
    } catch (error) {
      Logger.error("WireGuardTunnelService.isRunning", error as Error);
      return false;
    }
  }

  private async _waitUntilRemoved(timeoutMs = 15_000): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if ((await this._serviceState()) === "missing") return true;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    return (await this._serviceState()) === "missing";
  }

  private async _removeLocalTunnelService(): Promise<void> {
    if ((await this._serviceState()) === "missing") return;
    await this._runElevatedWireGuard(["/uninstalltunnelservice", TUNNEL_NAME]);
    if (!(await this._waitUntilRemoved())) {
      throw new Error("WireGuard tunnel service could not be removed");
    }
  }

  private async _waitUntilRunning(timeoutMs = 15_000): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await this.isRunning()) return true;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    return this.isRunning();
  }

  private async _readLatestHandshake(): Promise<number | null> {
    const executable = this._wgExecutable();
    if (!executable || !(await this.isRunning())) return null;
    try {
      const output = await this._execFile(executable, [
        "show",
        TUNNEL_NAME,
        "latest-handshakes"
      ]);
      const values = output
        .trim()
        .split(/\s+/)
        .map(value => Number(value))
        .filter(value => Number.isFinite(value) && value > 0);
      return values.length ? Math.max(...values) * 1000 : null;
    } catch {
      return null;
    }
  }

  private _normalizeConnections(
    config: SkyLabWireGuardConfig
  ): SkyLabTunnelInfo[] {
    return config.connections
      .filter(
        target =>
          Number.isInteger(target.vmid) &&
          target.vmid > 0 &&
          isIP(target.host) === 4 &&
          Number.isInteger(target.port) &&
          target.port > 0 &&
          target.port <= 65535 &&
          ["ssh", "rdp"].includes(target.service)
      )
      .map(target => ({
        vmid: target.vmid,
        vm_name: target.name,
        service: target.service,
        host: target.host,
        port: target.port
      }));
  }

  private _configFingerprint(config: SkyLabWireGuardConfig): string {
    return JSON.stringify({
      interface_name: config.interface_name,
      interface_address: config.interface_address,
      gateway_public_key: config.gateway_public_key,
      endpoint: config.endpoint,
      allowed_ips: config.allowed_ips,
      persistent_keepalive: config.persistent_keepalive
    });
  }

  private _applyLease(config: SkyLabWireGuardConfig): void {
    const now = Date.now();
    this._connections = this._normalizeConnections(config);
    this._expiresAt = now + Math.max(60, Number(config.expires_in) || 0) * 1000;
    this._lastLeaseRefreshAt = now;
    this._lastLeaseRefreshAttemptAt = now;
    this._connectionError = null;
    this._leaseRefreshError = null;
  }

  private _serializeOperation(action: () => Promise<void>): Promise<void> {
    this._operationRevision++;
    const operation = this._operationTail.then(action);
    this._operationTail = operation.then(
      () => {
        this._operationRevision++;
      },
      () => {
        this._operationRevision++;
      }
    );
    return operation;
  }

  async startTunnel(): Promise<void> {
    if (this._startPromise) return this._startPromise;
    const operation = this._serializeOperation(() => this._startTunnel());
    this._startPromise = operation;
    try {
      await operation;
    } finally {
      if (this._startPromise === operation) this._startPromise = null;
    }
  }

  private async _startTunnel(): Promise<void> {
    if (
      this._lastStartTime !== -1 &&
      this._expiresAt !== null &&
      Date.now() < this._expiresAt &&
      !this._connectionError &&
      (await this.isRunning())
    )
      return;
    this._connectionError = null;
    await this._ensureWireGuardInstalled();
    const identity = this._loadIdentity();
    const config = await this._SkyLabService.connectWireGuard(
      identity.deviceId,
      identity.publicKey
    );
    const configPath = PathUtils.getWireGuardConfigFilePath();
    try {
      const connections = this._normalizeConnections(config);
      await this._removeLocalTunnelService();
      fs.writeFileSync(configPath, this._buildConfig(identity, config), {
        encoding: "utf-8",
        mode: 0o600
      });
      await this._runElevatedWireGuard(["/installtunnelservice", configPath]);
      if (!(await this._waitUntilRunning())) {
        throw new Error("WireGuard tunnel service is not running");
      }
      this._connections = connections;
      this._lastStartTime = Date.now();
      this._activeConfigFingerprint = this._configFingerprint(config);
      this._applyLease(config);
      this._notifiedStartTime = -1;
      this._latestHandshakeAt = await this._readLatestHandshake();
      Logger.info(
        "WireGuardTunnelService.startTunnel",
        `Started ${TUNNEL_NAME}; targets=${this._connections.length}`
      );
    } catch (error) {
      this._connectionError = (error as Error).message;
      try {
        await this._SkyLabService.disconnectWireGuard(identity.deviceId);
      } catch (cleanupError) {
        Logger.error(
          "WireGuardTunnelService.startTunnel.cleanup",
          cleanupError
        );
      }
      if (error instanceof BusinessError) throw error;
      throw new BusinessError(
        ResponseCode.WIREGUARD_START_FAILED,
        error.message
      );
    } finally {
      fs.rmSync(configPath, { force: true });
    }
  }

  async refreshTunnel(): Promise<void> {
    if (this._refreshPromise) return this._refreshPromise;
    this._refreshPromise = this._serializeOperation(async () => {
      try {
        await this._refreshTunnelLease();
      } catch (error) {
        this._leaseRefreshError = (error as Error).message;
        throw error;
      }
    }).finally(() => {
      this._refreshPromise = null;
    });
    return this._refreshPromise;
  }

  private async _refreshTunnelLease(): Promise<void> {
    this._lastLeaseRefreshAttemptAt = Date.now();
    if (!(await this.isRunning()) || this._lastStartTime === -1) {
      throw new BusinessError(
        ResponseCode.WIREGUARD_START_FAILED,
        "The WireGuard tunnel is not connected."
      );
    }
    const identity = this._loadIdentity();
    const config = await this._SkyLabService.refreshWireGuard(
      identity.deviceId
    );
    if (
      this._activeConfigFingerprint !== null &&
      this._configFingerprint(config) !== this._activeConfigFingerprint
    ) {
      this._connectionError =
        "The WireGuard network configuration changed. Reconnect to apply it.";
      throw new BusinessError(
        ResponseCode.WIREGUARD_START_FAILED,
        this._connectionError
      );
    }
    this._applyLease(config);
    Logger.info(
      "WireGuardTunnelService.refreshTunnel",
      `Renewed lease; targets=${this._connections.length}`
    );
  }

  async refreshIfRunning(reason: string): Promise<void> {
    if (!(await this.isRunning()) || this._lastStartTime === -1) return;
    try {
      await this.refreshTunnel();
    } catch (error) {
      Logger.error(`WireGuardTunnelService.refreshIfRunning.${reason}`, error);
    }
  }

  private async _refreshLeaseWhenDue(): Promise<void> {
    const now = Date.now();
    if (
      this._lastLeaseRefreshAt !== -1 &&
      now - this._lastLeaseRefreshAt < LEASE_REFRESH_INTERVAL_MS
    ) {
      return;
    }
    if (
      this._lastLeaseRefreshAttemptAt !== -1 &&
      now - this._lastLeaseRefreshAttemptAt < LEASE_REFRESH_RETRY_MS
    ) {
      return;
    }
    await this.refreshIfRunning("timer");
  }

  async stopTunnel(): Promise<void> {
    return this._serializeOperation(() => this._stopTunnel());
  }

  private async _stopTunnel(): Promise<void> {
    let localError: Error | null = null;
    let backendError: Error | null = null;
    try {
      await this._removeLocalTunnelService();
    } catch (error) {
      localError = error as Error;
    }
    const identityPath = PathUtils.getWireGuardIdentityFilePath();
    if (fs.existsSync(identityPath)) {
      try {
        const identity = this._loadIdentity();
        await this._SkyLabService.disconnectWireGuard(identity.deviceId);
      } catch (error) {
        backendError = error as Error;
      }
    }
    this._lastStartTime = -1;
    this._notifiedStartTime = -1;
    this._connections = [];
    this._latestHandshakeAt = null;
    this._expiresAt = null;
    this._lastLeaseRefreshAt = -1;
    this._lastLeaseRefreshAttemptAt = -1;
    this._activeConfigFingerprint = null;
    this._leaseRefreshError = null;
    this._connectionError = (localError || backendError)?.message || null;
    if (localError || backendError) {
      throw localError || backendError;
    }
  }

  async cleanupOrphanedTunnel(): Promise<void> {
    let state: TunnelServiceState;
    try {
      state = await this._serviceState();
    } catch (error) {
      Logger.error(
        "WireGuardTunnelService.cleanupOrphanedTunnel.query",
        error as Error
      );
      return;
    }
    if (state === "missing" || this._lastStartTime !== -1) return;
    Logger.warn(
      "WireGuardTunnelService.cleanupOrphanedTunnel",
      `Removing orphaned ${TUNNEL_NAME} tunnel service (${state})`
    );
    await this.stopTunnel();
  }

  async getStatus(): Promise<TunnelStatusInfo> {
    // A service query/handshake read can straddle start, stop or refresh. Retry
    // instead of publishing a snapshot from a different operation generation.
    const revision = this._operationRevision;
    await this._operationTail;
    const localRunning = await this.isRunning();
    const handshake = localRunning ? await this._readLatestHandshake() : null;
    if (revision !== this._operationRevision) return this.getStatus();
    const expired = this._expiresAt !== null && Date.now() >= this._expiresAt;
    const starting = this._startPromise !== null;
    const orphaned = localRunning && this._lastStartTime === -1 && !starting;
    if (localRunning && !expired) {
      this._latestHandshakeAt = handshake;
    }
    if (expired) {
      this._connectionError =
        "The secure session expired. Disconnect and sign in again.";
    } else if (orphaned) {
      this._connectionError = ORPHANED_TUNNEL_ERROR;
    } else if (this._connectionError === ORPHANED_TUNNEL_ERROR) {
      this._connectionError = null;
    }
    const running =
      localRunning && this._lastStartTime !== -1 && !expired && !orphaned;
    return {
      running,
      lastStartTime: this._lastStartTime,
      connectionError: this._connectionError,
      leaseRefreshError: this._leaseRefreshError,
      tunnels: this._connections,
      mode: "wireguard",
      interfaceName: running ? TUNNEL_NAME : null,
      latestHandshakeAt: this._latestHandshakeAt
    };
  }

  watchTunnel(listenerParam: ListenerParam) {
    if (this._listener) clearInterval(this._listener);
    this._listener = setInterval(async () => {
      if (this._polling) return;
      this._polling = true;
      try {
        let status = await this.getStatus();
        if (status.running) {
          await this._refreshLeaseWhenDue();
          status = await this.getStatus();
        }
        if (
          !status.running &&
          this._lastStartTime !== -1 &&
          this._notifiedStartTime !== this._lastStartTime
        ) {
          new Notification({
            title: app.getName(),
            body: "WireGuard connection was lost."
          }).show();
          this._notifiedStartTime = this._lastStartTime;
        }
        const win: BrowserWindow = BeanFactory.getBean("win");
        if (win && !win.isDestroyed()) {
          win.webContents.send(
            listenerParam.channel,
            ResponseUtils.success(status)
          );
        }
      } catch (error) {
        Logger.error("WireGuardTunnelService.watchTunnel", error);
      } finally {
        this._polling = false;
      }
    }, GlobalConstant.TUNNEL_STATUS_POLL_INTERVAL_MS);
  }
}

export default WireGuardTunnelService;
