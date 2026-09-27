// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import JobDetailDialog from "./JobDetailDialog";
import { JobsService } from "../../services/jobs";

vi.mock("../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "student" } }) }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal()),
  useTranslation: () => ({ t: (key) => key }),
}));
vi.mock("../../services/jobs", () => ({ JobsService: { detail: vi.fn() } }));

let host;
let root;

const job = (id, title, status) => ({
  item: { id, title, status, created_at: "2026-09-27T00:00:00Z" },
  extra: {},
});

async function render(jobId) {
  await act(async () => {
    root.render(<JobDetailDialog jobId={jobId} onClose={() => {}} />);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
});

test("keeps polling after a failed background refresh and shows the final status", async () => {
  JobsService.detail
    .mockResolvedValueOnce(job("a", "任務甲", "running"))
    .mockRejectedValueOnce(new Error("502 Bad Gateway"))
    .mockResolvedValueOnce(job("a", "任務甲", "completed"));

  await render("a");
  expect(document.body.textContent).toContain("JobRow.statusRunning");

  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(document.body.textContent).toContain("502 Bad Gateway");

  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(JobsService.detail).toHaveBeenCalledTimes(3);
  expect(document.body.textContent).not.toContain("502 Bad Gateway");
  expect(document.body.textContent).not.toContain("JobRow.statusRunning");
  expect(document.body.textContent).toContain("JobRow.statusCompleted");
});

test("switching to another job id never shows the previous job's details", async () => {
  JobsService.detail
    .mockResolvedValueOnce(job("a", "任務甲", "completed"))
    .mockReturnValueOnce(new Promise(() => {}));

  await render("a");
  expect(document.body.textContent).toContain("任務甲");

  await render("b");
  expect(document.body.textContent).not.toContain("任務甲");
});
