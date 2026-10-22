// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("./useToast", () => ({ useToast: () => toast }));

import useRouteMessageToast from "./useRouteMessageToast";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;
let seenState;

function Page() {
  useRouteMessageToast();
  seenState = useLocation().state;
  return null;
}

beforeEach(() => {
  toast.success.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

test("shows the message once and drops it from the history state, keeping the rest", async () => {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[{ pathname: "/class-management/c1", state: { message: "已送出建機", createdTemplateId: "t9" } }]}>
        <Page />
      </MemoryRouter>,
    );
  });
  expect(toast.success).toHaveBeenCalledTimes(1);
  expect(toast.success).toHaveBeenCalledWith("已送出建機");
  expect(seenState).toEqual({ createdTemplateId: "t9" });
});

test("does nothing without a message", async () => {
  await act(async () => {
    root.render(<MemoryRouter initialEntries={["/class-management/c1"]}><Page /></MemoryRouter>);
  });
  expect(toast.success).not.toHaveBeenCalled();
});
