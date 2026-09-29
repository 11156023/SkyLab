// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import ResourceMgmtPage from "./ResourceMgmtPage";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  batchActionInChunks: vi.fn(),
  listAllSessions: vi.fn(),
  buildEnvironmentGroups: vi.fn(),
  navigate: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/resources", () => ({
  ResourcesService: { listAll: mocks.listAll, batchActionInChunks: mocks.batchActionInChunks },
}));
vi.mock("../../../services/quickPractice", () => ({
  QuickPracticeService: { listAllSessions: mocks.listAllSessions },
}));
vi.mock("../../../utils/environmentGroups", () => ({
  buildEnvironmentGroups: mocks.buildEnvironmentGroups,
  groupedResourceKeys: (groups = []) => ({
    requestIds: new Set(groups.flatMap((g) => g.machines.map((m) => String(m.requestId)))),
    vmids: new Set(groups.flatMap((g) => g.machines.map((m) => m.vmid))),
  }),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/MachineKindBadge/MachineKindBadge", () => ({ default: () => null }));
vi.mock("../../../components/PowerMenu/PowerMenu", () => ({
  default: ({ items = [], onControl }) => (
    <div>
      {items.map((item) => (
        <button key={item.action} type="button" data-action={item.action} onClick={() => onControl(item.action)}>
          {item.label}
        </button>
      ))}
    </div>
  ),
}));

let root;
let host;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

function buttonByText(text) {
  return [...host.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.listAllSessions.mockResolvedValue([]);
  mocks.buildEnvironmentGroups.mockReturnValue([]);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("ResourceMgmtPage", () => {
  test("Retry after a failed first load clears the error state", async () => {
    mocks.listAll
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce([]);

    await act(async () => { root.render(<ResourceMgmtPage />); });
    await flush();
    const retry = buttonByText("Error.retry");
    expect(retry).toBeTruthy();

    await act(async () => { retry.click(); });
    await flush();

    expect(mocks.listAll).toHaveBeenCalledTimes(2);
    expect(buttonByText("Error.retry")).toBeUndefined();
  });

  test("group 'start all' reports partial failure instead of success", async () => {
    mocks.listAll.mockResolvedValue([]);
    mocks.buildEnvironmentGroups.mockReturnValue([{
      id: "course:1",
      kind: "course",
      title: "課程 A",
      timingLabel: "—",
      nodeLabel: "pve1",
      machines: [
        { id: "m1", vmid: 101, requestId: 1, status: "stopped", resource: { vmid: 101, status: "stopped" } },
        { id: "m2", vmid: 102, requestId: 2, status: "stopped", resource: { vmid: 102, status: "stopped" } },
      ],
    }]);
    mocks.batchActionInChunks.mockResolvedValue({ total: 2, succeeded: 1, failed: 1, results: [] });

    await act(async () => { root.render(<ResourceMgmtPage />); });
    await flush();

    const menuBtn = host.querySelector('button[aria-label="ResourceMgmtPage.groupPowerTitle"]');
    await act(async () => { menuBtn.click(); });
    const startAll = host.querySelector('button[data-action="start"]');
    await act(async () => { startAll.click(); });
    await flush();

    expect(mocks.batchActionInChunks).toHaveBeenCalledWith([101, 102], "start");
    expect(mocks.toast.success).not.toHaveBeenCalled();
    expect(mocks.toast.error).toHaveBeenCalledWith("ResourceMgmtPage.batchPartialFailToast");
  });
});
