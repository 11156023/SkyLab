// @vitest-environment happy-dom
import { act, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import AiFloatingChat from "./AiFloatingChat";
import { LayoutContext } from "../../layout/layoutContext";
import { AiNavigationService } from "../../services/aiNavigation";
import { AiTemplateRecommendationApi } from "../../services/aiTemplateRecommendation";
import { ResourcesService } from "../../services/resources";
import { VmRequestsService } from "../../services/vmRequests";
import translations from "../../locales/zh-TW/components.json";

vi.mock("../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "student" } }) }));
vi.mock("react-i18next", async (importOriginal) => ({ ...(await importOriginal()), useTranslation: () => ({ t: translate }) }));
vi.mock("../../services/aiNavigation", () => ({ AiNavigationService: { resolve: vi.fn(), intake: vi.fn() } }));
vi.mock("../../services/aiTemplateRecommendation", () => ({ AiTemplateRecommendationApi: { chat: vi.fn(), recommend: vi.fn() } }));
vi.mock("../../services/aiContextualHelp", () => ({
  AiContextualHelpService: { surfaces: vi.fn().mockResolvedValue([]), explain: vi.fn() }, matchSurface: () => null,
}));
vi.mock("../../services/resources", () => ({ ResourcesService: { list: vi.fn() } }));
vi.mock("../../services/vmRequests", () => ({ VmRequestsService: { list: vi.fn(), create: vi.fn() } }));

function translate(key, vars = {}) {
  return Object.entries(vars).reduce((text, [name, value]) => text.replaceAll(`{{${name}}}`, value), translations[key] ?? key);
}

const requestSteps = [
  { title: "打開申請單", path: "/my-requests", status: "done" },
  { title: "填寫並確認", path: "/my-requests", status: "current", action: "recommend" },
  { title: "等待審核", path: "/my-requests", status: "todo" },
  { title: "開始使用", path: "/my-resources", status: "todo" },
];
const facts = { purpose: "Node.js 網站上線", gpu: "不需要 GPU", display: "Linux 指令列就好", inferred: ["gpu", "display"] };
const prefill = { resource_type: "lxc", hostname: "nodejs-web", cores: 2, memory_mb: 2048, disk_gb: 20 };
let host, root;
const applyPrefill = vi.fn();

function Harness() {
  const location = useLocation();
  const [form, setForm] = useState(null);
  const [filled, setFilled] = useState(null);
  const [requestSubmission, reportRequestSubmission] = useState(null);
  useEffect(() => {
    if (location.pathname === "/my-requests" && location.state?.create) {
      setForm({
        getContext: () => ({ hostname: "", resource_type: "lxc" }),
        applyPrefill: (value) => { applyPrefill(value); setFilled(value); },
      });
    } else setForm(null);
  }, [location.pathname, location.state]);
  return <LayoutContext.Provider value={{ requestForm: form, surface: null, requestSubmission }}>
    <span data-testid="path">{location.pathname}</span>
    {filled && <input aria-label="hostname" readOnly value={filled.hostname} />}
    <button onClick={() => reportRequestSubmission({ id: "request-1" })}>模擬申請送出成功</button>
    <AiFloatingChat open />
  </LayoutContext.Provider>;
}

beforeEach(async () => {
  vi.clearAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  AiNavigationService.resolve.mockResolvedValue({
    action: "guide", flow_id: "publish_service", flow_title: "發布網站",
    steps: [{ title: "啟動網站", detail: "先連進機器啟動網站", path: "/my-resources", status: "current" }],
  });
  ResourcesService.list.mockResolvedValue([]);
  AiNavigationService.intake.mockResolvedValue({
    ready: false, answered: 3, total: 4, facts, assumptions: ["不需要 GPU", "Linux 指令列就好"],
    question: { key: "duration", text: "大概要用多久？", options: ["幾週"] },
    flow_id: "request_machine", flow_title: "申請機器", steps: requestSteps,
  });
  AiTemplateRecommendationApi.recommend.mockResolvedValue({ final_plan: { summary: "已準備 Node.js 配置", form_prefill: prefill } });
  await act(async () => root.render(<MemoryRouter initialEntries={["/my-resources"]}><Harness /></MemoryRouter>));
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function click(text) {
  const buttons = [...host.querySelectorAll("button")].filter((button) => button.textContent.trim() === text);
  expect(buttons.length).toBeGreaterThan(0);
  await act(async () => buttons.at(-1).click());
}

async function send(text) {
  const input = host.querySelector("textarea");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => host.querySelector(`button[aria-label="${translate("AiFloatingChat.sendMessageAriaLabel")}"]`).click());
}

async function fillWebsite() {
  await send("我想放 Node.js 網站並上線");
  AiNavigationService.intake.mockResolvedValue({
    ready: true, answered: 4, total: 4, facts: { ...facts, duration: "幾週" },
    flow_id: "request_machine", flow_title: "申請機器", steps: requestSteps,
  });
  await click("幾週");
}

test("website without machines opens the form, asks one relevant question and fills it", async () => {
  await send("我想放 Node.js 網站並上線");
  expect(host.querySelector('[data-testid="path"]').textContent).toBe("/my-requests");
  expect(host.textContent).toContain("大概要用多久？");
  expect(AiTemplateRecommendationApi.chat).not.toHaveBeenCalled();
  expect(AiTemplateRecommendationApi.recommend).not.toHaveBeenCalled();
  AiNavigationService.intake.mockResolvedValue({
    ready: true, facts: { ...facts, duration: "幾週" }, steps: requestSteps, flow_id: "request_machine",
  });
  await click("幾週");
  expect(applyPrefill).toHaveBeenCalledWith(prefill);
  expect(host.querySelector('input[aria-label="hostname"]').value).toBe("nodejs-web");
  expect(AiNavigationService.intake.mock.calls.at(-1)[1]).toMatchObject({ facts, pendingKey: "duration" });
  expect(AiTemplateRecommendationApi.recommend.mock.calls[0][0].messages.at(-1).content).toContain("Node.js");
  await send("好");
  expect(AiTemplateRecommendationApi.recommend).toHaveBeenCalledTimes(1);
  expect(VmRequestsService.create).not.toHaveBeenCalled();
  expect(host.textContent).toContain("目前尚未送出申請");
});

test("only the matching approved and provisioned request resumes website publishing", async () => {
  await fillWebsite();
  await click("模擬申請送出成功");
  VmRequestsService.list.mockResolvedValue({ data: [{ id: "request-1", status: "pending", provisioning_status: "idle" }] });
  await click("查看申請進度");
  expect(host.textContent).toContain("這張申請還不能開始使用");
  expect(host.querySelector('[data-testid="path"]').textContent).toBe("/my-requests");
  VmRequestsService.list.mockResolvedValue({ data: [{ id: "request-1", status: "approved", provisioning_status: "completed", vmid: 101 }] });
  ResourcesService.list.mockResolvedValue([{ vmid: 101 }]);
  await send("好");
  expect(host.querySelector('[data-testid="path"]').textContent).toBe("/my-resources/101");
  expect(host.textContent).toContain("接著繼續原本的目標");
  expect(AiTemplateRecommendationApi.recommend).toHaveBeenCalledTimes(1);
});

test("planner failure keeps facts and never pretends to fill the form", async () => {
  await send("我想放 Node.js 網站並上線");
  AiTemplateRecommendationApi.recommend.mockRejectedValueOnce(new Error("offline"));
  await click(translate("AiFloatingChat.choiceSkipButton"));
  expect(host.textContent).toContain("已保留你的需求");
  expect(applyPrefill).not.toHaveBeenCalled();
  expect(AiTemplateRecommendationApi.chat).not.toHaveBeenCalled();
  await click(translate("AiFloatingChat.choiceSkipButton"));
  expect(applyPrefill).toHaveBeenCalledWith(prefill);
});

test("a failed resource lookup asks about prerequisites without claiming the user has no machines", async () => {
  ResourcesService.list.mockRejectedValueOnce(new Error("offline"));
  await send("我想放 Node.js 網站並上線");
  expect(host.textContent).toContain("目前無法讀取你的機器清單");
  expect(AiNavigationService.intake).not.toHaveBeenCalled();
  await click("我沒有機器");
  expect(AiNavigationService.intake).toHaveBeenCalledOnce();
  expect(host.querySelector('[data-testid="path"]').textContent).toBe("/my-requests");
});

test("fill command starts planning directly without asking the navigation model", async () => {
  await send("幫我填");
  expect(AiNavigationService.resolve).not.toHaveBeenCalled();
  expect(AiNavigationService.intake).toHaveBeenCalledOnce();
  expect(AiTemplateRecommendationApi.chat).not.toHaveBeenCalled();
});

test("an existing machine is not replaced with an unnecessary application", async () => {
  ResourcesService.list.mockResolvedValue([{ vmid: 101 }]);
  await send("我想放 Node.js 網站並上線");
  expect(host.textContent).toContain("啟動網站");
  expect(AiNavigationService.intake).not.toHaveBeenCalled();
  expect(host.querySelector('[data-testid="path"]').textContent).toBe("/my-resources");
});
