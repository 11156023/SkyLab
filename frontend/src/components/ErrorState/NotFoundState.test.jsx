// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));

import NotFoundState from "./NotFoundState";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;

afterEach(async () => {
  await act(async () => root?.unmount());
  document.body.innerHTML = "";
});

test("renders the not-found empty state without an action button", async () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => root.render(<NotFoundState />));
  expect(host.textContent).toContain("Error.notFoundTitle");
  expect(host.textContent).toContain("Error.notFoundDesc");
  expect(host.querySelector("button")).toBeNull();
});
