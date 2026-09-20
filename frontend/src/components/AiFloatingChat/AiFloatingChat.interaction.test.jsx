// @vitest-environment happy-dom
import { act, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
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
  const navigate = useNavigate();
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
  const surface = location.pathname === "/class-setup" ? {
    id: "class-setup", getState: () => ({ "classsetup.current_step": { value: "3. 教學環境" } }),
  } : null;
  return <LayoutContext.Provider value={{ requestForm: form, surface, requestSubmission }}>
    <span data-testid="path">{location.pathname}{location.search}</span>
    <button onClick={() => navigate("/class-setup?classId=42&step=3")}>模擬班級第3步</button>
    <button onClick={() => navigate("/templates")}>模擬切換到範本</button>
    <button onClick={() => navigate("/course-template-management/env-42?tab=basic&returnTo=%2Fclass-setup%3FclassId%3D9%26step%3D3")}>模擬既有環境草稿</button>
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

test("a side question is answered without consuming the pending intake answer", async () => {
  await send("我想放 Node.js 網站並上線");
  AiTemplateRecommendationApi.chat.mockResolvedValueOnce({ reply: "DNS 會把網域名稱對應到位址。" });
  await send("順便問什麼是 DNS？");
  expect(host.textContent).toContain("DNS 會把網域名稱對應到位址");
  expect(AiNavigationService.intake).toHaveBeenCalledTimes(1);
  await click("幾週");
  const [transcript, options] = AiNavigationService.intake.mock.calls.at(-1);
  expect(options.pendingKey).toBe("duration");
  expect(transcript.some((message) => message.content.includes("DNS"))).toBe(false);
});

test("multiple flows remain selectable after a relationship question", async () => {
  const flows = [
    { flow_id: "open_class", flow_title: "開班流程", steps: [{ title: "填課表", path: "/class-setup", detail: "先保存班級", status: "current" }] },
    { flow_id: "share_template", flow_title: "建立範本", steps: [{ title: "準備母機", path: "/my-resources", status: "current" }] },
  ];
  AiNavigationService.resolve.mockResolvedValueOnce({ action: "guide", flow_id: "open_class", steps: flows[0].steps, flows, answer: "班級可重用既有教學環境。" });
  await send("我要開班，也要建立範本");
  expect(host.textContent).toContain("班級可重用既有教學環境");
  expect(AiNavigationService.intake).not.toHaveBeenCalled();
  await click("建立範本");
  AiNavigationService.resolve.mockResolvedValueOnce({ action: "answer", answer: "範本只提供機器來源。" });
  await send("範本和班級的關係？");
  const options = AiNavigationService.resolve.mock.calls.at(-1)[1];
  expect(options.activeFlowId).toBe("share_template");
  expect(options.pendingFlowIds).toEqual(["open_class", "share_template"]);
  await click("開班流程");
  expect(host.textContent).toContain("接回「開班流程」");
  expect(AiTemplateRecommendationApi.recommend).not.toHaveBeenCalled();
});

test("class guidance includes the saved wizard step and preserves its resume URL", async () => {
  await click("模擬班級第3步");
  AiNavigationService.resolve.mockResolvedValueOnce({
    action: "guide", flow_id: "open_class", flow_title: "開班流程",
    steps: [{ title: "選用環境", path: "/class-setup", detail: "可重用已發布環境", status: "current" }],
  });
  await send("我要繼續建立班級");
  expect(AiNavigationService.resolve.mock.calls.at(-1)[1]).toMatchObject({
    currentPath: "/class-setup?classId=42&step=3", surfaceId: "class-setup",
    screenState: { "classsetup.current_step": { value: "3. 教學環境" } },
  });
  await click("模擬切換到範本");
  const step = [...host.querySelectorAll("button")].find((button) => button.querySelector("strong")?.textContent === "選用環境");
  await act(async () => step.click());
  expect(host.querySelector('[data-testid="path"]').textContent).toBe("/class-setup?classId=42&step=3");
});

test("an answer for a screen left during the request cannot replace current guidance", async () => {
  let finish;
  AiNavigationService.resolve.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  await send("我要建立班級");
  await click("模擬切換到範本");
  await act(async () => finish({ action: "answer", answer: "過期的畫面說明" }));
  expect(host.textContent).not.toContain("過期的畫面說明");
  expect(host.textContent).toContain("畫面或資料已變更");
});

test("environment steps open actual tabs without duplicating instructions or publishing", async () => {
  const steps = [
    { title: "填寫基本資料", detail: "填寫環境名稱", path: "/course-template-management/new", state: { environmentTab: "basic" }, status: "current" },
    { title: "加入機器並設定配置", detail: "加入一至三台機器", path: "/course-template-management/new", state: { environmentTab: "machines" }, status: "todo" },
    { title: "確認並發布環境", detail: "由使用者確認發布", path: "/course-template-management/new", state: { environmentTab: "machines" }, status: "todo" },
  ];
  AiNavigationService.resolve.mockResolvedValueOnce({ action: "guide", flow_id: "prepare_environment", flow_title: "建立教學環境", steps });
  await send("我要建立教學環境");
  const buttons = [...host.querySelectorAll("ol button")];
  expect(buttons[0].querySelector("strong").textContent).toBe("填寫基本資料");
  await act(async () => buttons[0].click());
  expect(host.querySelector('[data-testid="path"]').textContent).toBe("/course-template-management/new?tab=basic");
  await click("模擬既有環境草稿");
  await act(async () => buttons[1].click());
  const path = host.querySelector('[data-testid="path"]').textContent;
  expect(path.split("?")[0]).toBe("/course-template-management/env-42");
  expect(new URLSearchParams(path.split("?")[1]).get("tab")).toBe("machines");
  expect(new URLSearchParams(path.split("?")[1]).get("returnTo")).toBe("/class-setup?classId=9&step=3");
  await act(async () => buttons[2].click());
  expect(host.textContent.split("加入一至三台機器")).toHaveLength(2);
  expect(host.textContent.split("由使用者確認發布")).toHaveLength(2);
  expect(AiNavigationService.resolve).toHaveBeenCalledOnce();
  expect(AiTemplateRecommendationApi.recommend).not.toHaveBeenCalled();
});

test("建立課程 starts class guidance while preserving the old environment task", async () => {
  AiNavigationService.resolve.mockResolvedValueOnce({
    action: "guide", flow_id: "prepare_environment", flow_title: "建立教學環境",
    steps: [{ title: "環境基本資料", path: "/course-template-management/new", status: "current" }],
  });
  await send("建立教學環境");
  AiNavigationService.resolve.mockResolvedValueOnce({
    action: "guide", flow_id: "open_class", flow_title: "建立班級",
    steps: [{ title: "填寫課表", path: "/class-setup", detail: "填班級名稱與上課時間。", status: "current" }],
  });
  await send("建立課程");
  expect(AiNavigationService.resolve.mock.calls.at(-1)[0]).toBe("建立課程");
  expect(host.textContent).toContain("填班級名稱與上課時間");
  expect(AiTemplateRecommendationApi.chat).not.toHaveBeenCalled();
  expect(AiNavigationService.intake).not.toHaveBeenCalled();
  const selected = host.querySelector('button[aria-pressed="true"]');
  expect(selected.textContent).toBe("建立班級");
  expect(host.querySelectorAll('button[aria-pressed]').length).toBe(2);
});
