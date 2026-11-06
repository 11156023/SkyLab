// @vitest-environment happy-dom
/*
 * 重設登入密碼：系統產生／自己指定用分段切換；自己指定時規則清單打字中不標紅、按送出才標；
 * 重設後的密碼框依「平台有沒有保管」講對話（系統產生的總覽看得到，自己指定的看不回來），
 * 還沒生效（要重新開機）才多一句說明。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const { service, toastApi, i18n } = vi.hoisted(() => ({
  i18n: { t: (key) => key, i18n: { language: "zh-TW" } },
  service: {
    getCredentials: vi.fn(),
    resetPassword: vi.fn(),
  },
  toastApi: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => i18n }));
vi.mock("../../../../../services/resources", () => ({ ResourcesService: service }));
vi.mock("../../../../../hooks/useToast", () => ({ useToast: () => toastApi }));
vi.mock("../../../../../hooks/useDialogPresence", () => ({
  default: (value) => ({ open: Boolean(value), item: value || null, closing: false }),
}));
vi.mock("../../../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => async () => false }));
vi.mock("../../../../../components/Modal/Modal", () => ({
  default: ({ as: Tag = "div", onSubmit, title, children, actions }) => (
    <Tag role="dialog" onSubmit={onSubmit}>
      <h2>{title}</h2>
      {children}
      <div>{actions}</div>
    </Tag>
  ),
}));

import CredentialsCard from "./CredentialsCard";
import { withQueryClient } from "../../../../../queries/testing";

let host;
let root;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

const CREDENTIALS = {
  vmid: 7, resource_type: "qemu", running: true, username: "student",
  has_login_password: true, has_custom_login_password: false,
  supports_password_reset: true, supports_ssh_keys: true, is_windows: false,
  requires_running: false, platform_public_key: null, authorized_keys: [],
};

async function renderCard(overrides = {}) {
  service.getCredentials.mockResolvedValue({ ...CREDENTIALS, ...overrides });
  await act(async () => root.render(withQueryClient(<CredentialsCard vmid={7} />)));
  await flush();
}

const button = (text) => [...host.querySelectorAll("button")].find((b) => b.textContent.includes(text));
const dialog = () => host.querySelector('form[role="dialog"]');
const passwordInput = () => dialog()?.querySelector("#cred-pw");
const rulesList = () => dialog()?.querySelector('[role="list"]');

async function openDialog() {
  await act(async () => { button("CredentialsCard.resetPassword").click(); });
}
async function typePassword(value) {
  const input = passwordInput();
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setValue.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function submit() {
  await act(async () => { dialog().dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  await flush();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

test("system-generated: one click, and the card says the password can be seen again on the overview", async () => {
  service.resetPassword.mockResolvedValue({
    vmid: 7, password: "Tq7#mWz2", applied_immediately: false, rebooting: true, message: "rebooting to apply",
  });
  await renderCard();
  await openDialog();

  /* 預設系統產生：沒有密碼欄，送出鈕直接講要做什麼；執行中的 VM 有重開機警告 */
  expect(passwordInput()).toBeNull();
  expect(dialog().textContent).toContain("CredentialsCard.generateHint");
  expect(dialog().textContent).toContain("CredentialsCard.rebootWarning");
  expect(button("CredentialsCard.submitGenerate")).toBeTruthy();

  await submit();

  expect(service.resetPassword).toHaveBeenCalledWith(7, null);
  expect(dialog()).toBeNull();
  expect(host.textContent).toContain("Tq7#mWz2");
  expect(host.textContent).toContain("CredentialsCard.secretStoredNote");
  expect(host.textContent).not.toContain("CredentialsCard.secretOnceNote");
  /* 還沒生效：留一句什麼時候生效 */
  expect(host.textContent).toContain("rebooting to apply");

  await act(async () => { button("CredentialsCard.secretDone").click(); });
  expect(host.textContent).not.toContain("Tq7#mWz2");
});

test("own password: rules are not flagged while typing, only after submitting; then it is sent", async () => {
  service.resetPassword.mockResolvedValue({
    vmid: 7, password: "abcdefgh", applied_immediately: true, rebooting: false, message: "applied now",
  });
  await renderCard({ running: false });
  await openDialog();
  expect(dialog().textContent).not.toContain("CredentialsCard.rebootWarning");

  await act(async () => { button("CredentialsCard.modeCustom").click(); });
  expect(button("CredentialsCard.submitCustom")).toBeTruthy();
  await typePassword("abc 12");

  /* 打字途中：清單照常顯示，但欄位不標紅、送出鈕不停用 */
  expect(rulesList().textContent).toContain("PasswordRules.noWhitespace");
  expect(passwordInput().getAttribute("aria-invalid")).toBeNull();
  expect(button("CredentialsCard.submitCustom").disabled).toBe(false);

  await submit();
  expect(service.resetPassword).not.toHaveBeenCalled();
  expect(passwordInput().getAttribute("aria-invalid")).toBe("true");

  await typePassword("abcdefgh");
  await submit();

  expect(service.resetPassword).toHaveBeenCalledWith(7, "abcdefgh");
  /* 自己指定的平台不保存；馬上生效就不再重複 toast 的那句 */
  expect(host.textContent).toContain("CredentialsCard.secretOnceNote");
  expect(host.textContent).not.toContain("applied now");
});

test("Windows machines also need the complexity rules before the password can be sent", async () => {
  await renderCard({ is_windows: true, running: false });
  await openDialog();
  await act(async () => { button("CredentialsCard.modeCustom").click(); });
  await typePassword("abcdefg1");

  await submit();

  expect(service.resetPassword).not.toHaveBeenCalled();
  expect(rulesList().textContent).toContain("PasswordRules.windowsAddOneType");
});
