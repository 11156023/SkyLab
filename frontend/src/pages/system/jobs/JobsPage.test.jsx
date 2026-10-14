// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import JobsPage from "./JobsPage";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/jobs", () => ({ JobsService: { list: mocks.list } }));
vi.mock("../../../components/Jobs/JobDetailDialog", () => ({ default: () => null }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/SegmentedControl/SegmentedControl", () => ({
  default: ({ options = [], onChange, ariaLabel }) => (
    <div data-group={ariaLabel}>
      {options.map((option) => (
        <button key={option.value} type="button" data-value={option.value} onClick={() => onChange(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  ),
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function job(id, kind) {
  return { id, kind, title: `job-${id}`, status: "completed", progress: 100 };
}

let host;
let root;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function flush() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  });
}

async function pickKind(kind) {
  await act(async () => host.querySelector(`[data-group="JobsPage.filterKind"] [data-value="${kind}"]`).click());
}

test("快速切換篩選時，較晚回來的舊條件結果不會蓋掉目前篩選的清單", async () => {
  const older = deferred();
  const newer = deferred();
  mocks.list
    .mockResolvedValueOnce({ items: [job("all", "vm_request")] })
    .mockReturnValueOnce(older.promise)
    .mockReturnValueOnce(newer.promise);

  await act(async () => root.render(<JobsPage />));
  await flush();

  await pickKind("vm_request");
  await pickKind("deletion");
  expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ kinds: ["deletion"] }));

  await act(async () => newer.resolve({ items: [job("del", "deletion")] }));
  await flush();
  await act(async () => older.resolve({ items: [job("vm", "vm_request")] }));
  await flush();

  expect(host.textContent).toContain("job-del");
  expect(host.textContent).not.toContain("job-vm");
  expect(host.querySelector('[data-testid="loading"]')).toBeNull();
});
