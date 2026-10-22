// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import ResourcesPage from "./ResourcesPage";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  listMySessions: vi.fn(),
  listRequests: vi.fn(),
  t: (key) => key,
}));

vi.mock("../../../services/resources", () => ({ ResourcesService: { list: mocks.list } }));
vi.mock("../../../services/quickPractice", () => ({ QuickPracticeService: { listMySessions: mocks.listMySessions } }));
vi.mock("../../../services/vmRequests", () => ({ VmRequestsService: { list: mocks.listRequests } }));
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "student" } }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/QuotaUsageBar/QuotaUsageBar", () => ({ default: () => null }));
vi.mock("../../../components/MachineKindBadge/MachineKindBadge", () => ({ default: () => null }));

let root;
let host;

async function flush() {
  await act(async () => {
    /* TanStack Query 以 macrotask 批次通知，先等一個 timer tick */
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.listMySessions.mockResolvedValue([]);
  mocks.listRequests.mockResolvedValue({ data: [] });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("ResourcesPage expiry column", () => {
  test("a date-only expiry shows that calendar day plus the local cutoff time", async () => {
    const previousTz = process.env.TZ;
    process.env.TZ = "America/Los_Angeles";
    try {
      mocks.list.mockResolvedValue([{
        vmid: 101, name: "my-vm", type: "qemu", status: "stopped", request_id: 1, expiry_date: "2026-10-15",
      }]);
      await act(async () => { root.render(withQueryClient(<ResourcesPage />)); });
      await flush();

      const row = [...host.querySelectorAll("tr")].find((tr) => tr.textContent.includes("my-vm"));
      // 當成 UTC 午夜的話洛杉磯會顯示 10/14；實際截止是 10/14 17:00（PDT）
      expect(row.textContent).toContain("2026/10/15");
      expect(row.textContent).not.toContain("2026/10/14");
      /* 日期與時間間的空白依 ICU 版本可能是 U+202F，比對前統一成一般空白 */
      expect(row.textContent.replace(/\s/g, " ")).toContain("10/14 17:00 截止");
    } finally {
      if (previousTz === undefined) delete process.env.TZ;
      else process.env.TZ = previousTz;
    }
  });
});
