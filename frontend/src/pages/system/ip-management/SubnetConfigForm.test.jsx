// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import SubnetConfigForm from "./SubnetConfigForm";

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: (key) => key }),
}));
vi.mock("../../../components/Modal/Modal", () => ({
  default: ({ title, children, actions, onSubmit }) => (
    <form role="dialog" onSubmit={onSubmit}>
      <h2>{title}</h2>
      {children}
      <div>{actions}</div>
    </form>
  ),
}));

const CONFIG = {
  cidr: "10.10.0.0/24",
  gateway: "10.10.0.1",
  bridge_name: "vmbr1",
  vlan_tag: null,
  gateway_vm_ip: "10.10.0.2",
  platform_ip: "10.10.0.3",
  dns_servers: null,
  extra_blocked_subnets: [],
  forward_port_start: 30000,
  forward_port_end: 39999,
  forward_public_host: null,
};

let container;
let root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(props) {
  act(() => {
    root.render(<SubnetConfigForm onCancel={() => {}} onDelete={() => {}} {...props} />);
  });
}

function platformInput() {
  return container.querySelector('input[placeholder="SubnetConfigForm.platformIpPlaceholder"]');
}

function typeInto(input, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function submit() {
  act(() => {
    container.querySelector("form").dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
  });
}

describe("SubnetConfigForm 主系統 IP", () => {
  test("編輯時帶入既有的主系統 IP，並隨 payload 送出", () => {
    const onSubmit = vi.fn();
    render({ config: CONFIG, onSubmit });

    expect(container.textContent).toContain("SubnetConfigForm.platformIp");
    expect(platformInput().value).toBe("10.10.0.3");
    expect(platformInput().required).toBe(false);

    typeInto(platformInput(), " 10.10.0.9 ");
    submit();

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ platform_ip: "10.10.0.9" }));
  });

  test("留空時送 null", () => {
    const onSubmit = vi.fn();
    render({ config: CONFIG, onSubmit });

    typeInto(platformInput(), "  ");
    submit();

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ platform_ip: null }));
  });

  test("新建表單預設為空", () => {
    render({ config: null, onSubmit: vi.fn() });

    expect(platformInput().value).toBe("");
  });
});
