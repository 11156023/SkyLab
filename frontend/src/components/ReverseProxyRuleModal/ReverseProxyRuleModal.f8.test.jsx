// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));
vi.mock("../../hooks/useToast", () => ({ useToast: () => toast }));
vi.mock("../../services/resources", () => ({
  ResourcesService: { list: vi.fn(async () => [{ vmid: 101, name: "vm" }]), listAll: vi.fn(async () => []) },
}));

import ReverseProxyRuleModal from "./ReverseProxyRuleModal";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

beforeEach(() => {
  toast.error.mockReset();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

test("switching an edited custom-port rule back to common ports submits the port the select shows", async () => {
  const onSubmit = vi.fn();
  const zone = { id: "z1", name: "example.com" };
  await act(async () =>
    root.render(
      <ReverseProxyRuleModal
        rule={{ vmid: 101, zone_id: "z1", domain: "app.example.com", internal_port: 9000, enable_https: true }}
        setupContext={{ zones: [zone] }}
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    ),
  );

  const toggle = [...document.querySelectorAll("button")].find(
    (b) => b.textContent === "ReverseProxyRuleModal.backToCommonPorts",
  );
  await act(async () => toggle.click());

  const portSelect = document.querySelector("[data-guide='proxy-rule-port'] select");
  expect(portSelect.value).toBe("80");

  await act(async () => document.querySelector("form").requestSubmit());
  expect(toast.error).not.toHaveBeenCalled();
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ internal_port: 80, vmid: 101 }));
});
