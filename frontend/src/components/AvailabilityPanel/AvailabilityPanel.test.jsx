// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const preview = vi.hoisted(() => vi.fn());

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));
vi.mock("../../services/vmRequestAvailability", () => ({
  VmRequestAvailabilityService: { preview },
}));

import AvailabilityPanel from "./AvailabilityPanel";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function dateStr(offsetDays) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

let host;
let root;

beforeEach(() => {
  preview.mockReset();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

test("asks for the spec first when the draft is incomplete", async () => {
  await act(async () => root.render(<AvailabilityPanel draft={{ resource_type: "vm" }} />));
  expect(host.textContent).toContain("AvailabilityPanel.hintFillSpecFirst");
  expect(preview).not.toHaveBeenCalled();
});

test("days without availability are disabled, open days are clickable, and data is reported to the parent", async () => {
  /* 月曆預設顯示本月，所以只拿「今天」當可選日 */
  const open = dateStr(0);
  const data = {
    days: [
      { date: open, slots: [{ status: "available" }, { status: "available" }] },
    ],
  };
  preview.mockResolvedValue(data);
  const onDataChange = vi.fn();
  const onChange = vi.fn();
  await act(async () =>
    root.render(
      <AvailabilityPanel
        draft={{ resource_type: "vm", cores: 2, memory: 2048, disk_size: 20, template_id: "f8-full" }}
        onChange={onChange}
        onDataChange={onDataChange}
      />,
    ),
  );
  await act(async () => {});
  expect(onDataChange).toHaveBeenLastCalledWith(data);

  const today = new Date().getDate();
  const dayButtons = [...host.querySelectorAll("button")].filter((b) => /^\d+$/.test(b.textContent));
  const openButton = dayButtons.find((b) => Number(b.textContent) === today);
  expect(openButton.disabled).toBe(false);
  /* 沒有資料的日子（等同額滿／不可選）一律 disabled */
  const others = dayButtons.filter((b) => b !== openButton);
  expect(others.every((b) => b.disabled)).toBe(true);

  await act(async () => openButton.click());
  expect(onChange).toHaveBeenCalledWith(
    expect.objectContaining({ start_at: expect.any(String), end_at: expect.any(String) }),
  );
});
