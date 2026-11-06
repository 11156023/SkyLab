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
  mocks.list.mockResolvedValue([]);
  mocks.listMySessions.mockResolvedValue([]);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("ResourcesPage 佔位列", () => {
  test("審核中的申請不寫「開通中」；已核准、機器還在建的才寫", async () => {
    mocks.listRequests.mockResolvedValue({
      data: [
        { id: "r1", status: "pending", hostname: "wait-vm", resource_type: "vm", vmid: null },
        { id: "r2", status: "approved", provisioning_status: "running", hostname: "prov-vm", resource_type: "vm", vmid: null },
      ],
    });

    await act(async () => { root.render(withQueryClient(<ResourcesPage />)); });
    await flush();

    const rowOf = (name) => [...host.querySelectorAll("tr")].find((tr) => tr.textContent.includes(name));
    /* 審核中：狀態徽章是「審核中」，環境欄只有「資源申請」，沒有那行開通中的小字 */
    expect(rowOf("wait-vm").textContent).toContain("CreatingRow.statusPendingReview");
    expect(rowOf("wait-vm").textContent).toContain("CreatingRow.resourceRequestLabel");
    expect(rowOf("wait-vm").textContent).not.toContain("CreatingRow.creating");
    /* 開通中：小字照舊 */
    expect(rowOf("prov-vm").textContent).toContain("CreatingRow.statusProvisioning");
    expect(rowOf("prov-vm").textContent).toContain("CreatingRow.creating");
  });
});
