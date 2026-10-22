// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
  getServiceStatus: vi.fn(),
  readServiceConfig: vi.fn(),
  writeServiceConfig: vi.fn(),
  getServiceLogs: vi.fn(),
  getWireGuardOverview: vi.fn(),
  controlService: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/gateway", () => ({
  GatewayService: {
    getConfig: mocks.getConfig,
    getServiceStatus: mocks.getServiceStatus,
    readServiceConfig: mocks.readServiceConfig,
    writeServiceConfig: mocks.writeServiceConfig,
    getServiceLogs: mocks.getServiceLogs,
    getWireGuardOverview: mocks.getWireGuardOverview,
    controlService: mocks.controlService,
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: ({ children }) => <div>{children}</div> }));
/* Monaco 很重：換成只顯示 props 的替身 */
vi.mock("./ConfigCodeEditor", () => ({
  default: ({ value, onChange, dirty, loadFailed, onReload, onSave }) => (
    <div data-testid="editor" data-dirty={String(dirty)} data-failed={String(loadFailed)}>
      <textarea value={value} onChange={(e) => onChange(e.target.value)} />
      <button type="button" onClick={onReload}>editor-reload</button>
      <button type="button" onClick={onSave}>editor-save</button>
    </div>
  ),
}));

const { default: GatewayPage } = await import("./GatewayPage");
const { withQueryClient } = await import("../../../queries/testing");

let root;
let host;

/* TanStack Query 以 macrotask 批次通知；lazy 載入的編輯器替身也要多等幾輪 */
async function flush() {
  for (let round = 0; round < 3; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      for (let i = 0; i < 5; i += 1) await Promise.resolve();
    });
  }
}

function buttonByText(text) {
  return [...host.querySelectorAll("button")].find((b) => b.textContent === text);
}

function editor() {
  return host.querySelector('[data-testid="editor"]');
}

async function typeInto(textarea, value) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function openTab(label) {
  await act(async () => { buttonByText(label).click(); });
  await flush();
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.getConfig.mockResolvedValue({ is_configured: true, host: "gw.example.edu", ssh_port: 22, ssh_user: "root" });
  mocks.getServiceStatus.mockResolvedValue({ active: true, status_text: "active (running)" });
  mocks.getServiceLogs.mockResolvedValue("log line");
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("GatewayPage nginx tab", () => {
  test("a config read failure locks the editor until a reload succeeds", async () => {
    mocks.readServiceConfig
      .mockRejectedValueOnce(new Error("ssh down"))
      .mockResolvedValueOnce({ content: "worker_processes 1;" });

    await act(async () => { root.render(withQueryClient(<GatewayPage />)); });
    await flush();
    await openTab("nginx");

    expect(editor().dataset.failed).toBe("true");
    expect(host.querySelector("textarea").value).toBe("");
    expect(mocks.toast.error).toHaveBeenCalledWith("GatewayPage.toastConfigReadFailed");

    await act(async () => { buttonByText("editor-reload").click(); });
    await flush();

    expect(mocks.readServiceConfig).toHaveBeenCalledTimes(2);
    expect(editor().dataset.failed).toBe("false");
    expect(host.querySelector("textarea").value).toBe("worker_processes 1;");
    expect(editor().dataset.dirty).toBe("false");
    expect(host.textContent).toContain("log line");
  });

  test("writing the config marks it saved without re-reading the file", async () => {
    mocks.readServiceConfig.mockResolvedValue({ content: "a" });
    mocks.writeServiceConfig.mockResolvedValue({});

    await act(async () => { root.render(withQueryClient(<GatewayPage />)); });
    await flush();
    await openTab("nginx");

    await typeInto(host.querySelector("textarea"), "b");
    expect(editor().dataset.dirty).toBe("true");

    await act(async () => { buttonByText("editor-save").click(); });
    await flush();

    expect(mocks.writeServiceConfig).toHaveBeenCalledWith("nginx", "b");
    expect(editor().dataset.dirty).toBe("false");
    expect(host.querySelector("textarea").value).toBe("b");
    expect(mocks.readServiceConfig).toHaveBeenCalledTimes(1);
  });
});

describe("GatewayPage WireGuard tab", () => {
  test("an overview failure shows the retry state; retrying loads the metrics", async () => {
    mocks.getWireGuardOverview
      .mockRejectedValueOnce(new Error("503"))
      .mockResolvedValueOnce({
        mode: "kernel", interface: "wg0", authorized_sessions: 3, live_peers: 2, recent_handshakes: 1,
        transfer_rx_bytes: 0, transfer_tx_bytes: 0, expired_sessions: 0, inspection_available: true,
        session_ttl_seconds: 3600, reconcile_enabled: true,
      });

    await act(async () => { root.render(withQueryClient(<GatewayPage />)); });
    await flush();
    await openTab("GatewayPage.tabWireGuard");

    expect(mocks.toast.error).toHaveBeenCalledWith("Error.generic");
    const retry = [...host.querySelectorAll("button")].find((b) => b.textContent.includes("Error.retry"));
    await act(async () => { retry.click(); });
    await flush();

    expect(mocks.getWireGuardOverview).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("GatewayPage.wireGuardAuthorizedSessions");
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
  });
});
