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

/* PowerMenu 是 portal，但 React 事件仍沿元件樹冒泡到外層 <tr onClick> */
async function renderInRow({ onRowClick, onControl, onClose }) {
  const anchor = document.createElement("button");
  document.body.appendChild(anchor);
  await act(async () =>
    root.render(
      <table>
        <tbody>
          <tr onClick={onRowClick}>
            <td>
              <PowerMenu
                resource={{ status: "running" }}
                onControl={onControl}
                onClose={onClose}
                anchorRef={{ current: anchor }}
              />
            </td>
          </tr>
        </tbody>
      </table>,
    ),
  );
}

test("clicking the menu title does not trigger the parent row click", async () => {
  const onRowClick = vi.fn();
  await renderInRow({ onRowClick, onControl: vi.fn(), onClose: vi.fn() });
  const title = [...document.querySelectorAll("div")].find((d) => d.textContent === "PowerMenu.title");
  await act(async () => title.click());
  expect(onRowClick).not.toHaveBeenCalled();
});

test("menu buttons still run their own action without triggering the row", async () => {
  const onRowClick = vi.fn();
  const onControl = vi.fn();
  const onClose = vi.fn();
  await renderInRow({ onRowClick, onControl, onClose });
  const reboot = [...document.querySelectorAll("button")].find((b) => b.textContent.includes("PowerMenu.reboot"));
  await act(async () => reboot.click());
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(onControl).toHaveBeenCalledWith("reboot");
  expect(onRowClick).not.toHaveBeenCalled();
});
