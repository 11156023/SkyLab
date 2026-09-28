// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));

import PowerMenu from "./PowerMenu";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

function findButton(label) {
  return [...document.querySelectorAll("button")].find((b) => b.textContent.includes(label));
}

async function renderMenu(props) {
  const anchor = document.createElement("button");
  document.body.appendChild(anchor);
  await act(async () =>
    root.render(
      <PowerMenu
        resource={{ status: "running" }}
        onControl={() => {}}
        anchorRef={{ current: anchor }}
        {...props}
      />,
    ),
  );
}

test("power actions close the menu before running the action", async () => {
  const onClose = vi.fn();
  const onControl = vi.fn();
  await renderMenu({ onClose, onControl });
  await act(async () => findButton("PowerMenu.reboot").click());
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onControl).toHaveBeenCalledWith("reboot");
});

test("convert-to-template and delete leave closing to the caller (no double close)", async () => {
  const onClose = vi.fn();
  const onConvertTemplate = vi.fn();
  const onDeleteClick = vi.fn();
  await renderMenu({ onClose, onConvertTemplate, onDeleteClick });

  await act(async () => findButton("PowerMenu.convertTemplate").click());
  expect(onConvertTemplate).toHaveBeenCalledTimes(1);

  await act(async () => findButton("PowerMenu.delete").click());
  expect(onDeleteClick).toHaveBeenCalledTimes(1);

  expect(onClose).not.toHaveBeenCalled();
});

test("extra actions are hidden without their callbacks", async () => {
  await renderMenu({ onClose: () => {} });
  expect(findButton("PowerMenu.convertTemplate")).toBeUndefined();
  expect(findButton("PowerMenu.delete")).toBeUndefined();
});
