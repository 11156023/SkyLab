// Manual page: manual/ai-api.md
import { D, H, MIN, NOW, ago, iso, later } from "../lib/mock.mjs";
import { aiApiMocks, myCredentials, myRequests } from "../mocks/ai.mjs";
import { users } from "../mocks/common.mjs";

const tab = (s, key) => s.page.locator(`[data-guide-tab="${key}"]`);
const row = (s, text) => s.page.locator("tr", { hasText: text }).first();
const dialog = (s) => s.page.locator('[role="dialog"],[role="alertdialog"]').last();
const keyName = (s, i) => myCredentials(s.m)[i].api_key_name;
// The page column right of the sidebar, from y to y + h.
const MAIN = (y = 0, h = 900) => ({ x: 336, y, width: 1068, height: h });
const badge = (r) => r.locator("td").nth(1).locator("span").first();
// The page column from the top of `from` (minus padTop) to the bottom of `to` (plus padBottom).
async function between(s, from, to, padTop, padBottom) {
  await s.page.mouse.move(200, 600);
  await s.page.waitForTimeout(150);
  const a = await from.boundingBox();
  const b = await to.boundingBox();
  const y = Math.max(0, Math.round(a.y + padTop));
  return MAIN(y, Math.min(900 - y, Math.round(b.y + b.height + padBottom - y)));
}

async function openMenu(s, i = 0) {
  const r = row(s, myCredentials(s.m)[i].api_key_prefix);
  await s.click(r.getByRole("button", { name: s.t("More actions", "更多操作") }), { wait: 400 });
  return r;
}

// Saved chat history for the API Chat tab (stored in localStorage by the page).
function chatHistory(m) {
  const conv = (id, title, model, q, a, t) => ({
    id, title, model,
    messages: [
      { id: `${id}-q`, role: "user", content: q, createdAt: iso(t) },
      { id: `${id}-a`, role: "assistant", content: a, model, createdAt: iso(t + 9000) },
    ],
  });
  const conversations = [
    conv("8b1d2c3e-0001-4000-8000-000000000001", m.L("Explain Flask blueprints", "Flask 的 Blueprint 是什麼"), "qwen3-32b",
      m.L("Explain Flask blueprints", "Flask 的 Blueprint 是什麼"),
      m.L("A **Blueprint** groups related routes, templates and static files so you can register them on the app as one unit.\n\n```python\nbp = Blueprint(\"posts\", __name__, url_prefix=\"/posts\")\n```",
        "**Blueprint** 可以把相關的路由、範本與靜態檔案包成一組，再一次註冊到應用程式上。\n\n```python\nbp = Blueprint(\"posts\", __name__, url_prefix=\"/posts\")\n```"),
      NOW - 2 * D),
    conv("8b1d2c3e-0002-4000-8000-000000000002", m.L("SQL JOIN vs subquery", "SQL JOIN 和子查詢差在哪"), "llama-3.3-70b",
      m.L("SQL JOIN vs subquery", "SQL JOIN 和子查詢差在哪"),
      m.L("Both can combine data from two tables. A JOIN is usually easier for the optimiser; a subquery reads better when you only need a filter.",
        "兩者都能合併兩張表的資料。JOIN 通常比較好讓資料庫最佳化；只需要當作篩選條件時，子查詢比較好讀。"),
      NOW - 5 * D),
  ];
  return { conversations, activeId: conversations[0].id };
}

const CHAT_Q = ["What is the difference between a VM and a container?", "虛擬機和容器有什麼不同？"];
const CHAT_A = [
  ["The main differences between a virtual machine and a container:\n\n",
    "- **Isolation**: a VM runs its own operating system kernel; containers share the host kernel.\n",
    "- **Start-up time**: containers start in seconds, VMs usually take tens of seconds.\n",
    "- **Resources**: containers are lighter, so one host can run many more of them.\n\n",
    "Pick a VM when you need a full OS or a desktop; a container is a good fit for web sites and APIs."],
  ["虛擬機和容器的主要差異：\n\n",
    "- **隔離層級**：虛擬機有自己的作業系統核心，容器則共用主機核心\n",
    "- **啟動速度**：容器幾秒就能啟動，虛擬機通常需要數十秒\n",
    "- **資源用量**：容器更輕量，同一台主機可以跑更多個\n\n",
    "需要完整作業系統或圖形桌面時選虛擬機；部署網站或 API 服務，用容器就很合適。"],
];

// Replaces the chat completion request with a stream that sends the first part of
// the reply and then waits until window.__chatRelease() is called.
const streamScript = (parts, firstN) => `(() => {
  const parts = ${JSON.stringify(parts)};
  const orig = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : input.url;
    if (!url.includes("/ai-proxy/chat/completions")) return orig.apply(this, arguments);
    const enc = new TextEncoder();
    const ev = (text) => enc.encode("data: " + JSON.stringify({ choices: [{ index: 0, delta: { content: text } }] }) + "\\n\\n");
    let ctrl;
    const body = new ReadableStream({ start(c) { ctrl = c; parts.slice(0, ${firstN}).forEach((p) => c.enqueue(ev(p))); } });
    window.__chatRelease = () => { parts.slice(${firstN}).forEach((p) => ctrl.enqueue(ev(p))); ctrl.enqueue(enc.encode("data: [DONE]\\n\\n")); ctrl.close(); };
    return Promise.resolve(new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } }));
  };
})();`;

export default {
  page: "ai-api",
  role: "student",
  mocks(m) {
    aiApiMocks(m);
  },
  sections: [
    // s01 Request a key
    async (s) => {
      await s.go("/ai-api");
      const add = s.page.locator('[data-guide="ai-add-key"]').first();
      await s.shot(1, add, { crop: MAIN(0, 400) });
      await s.click(add, { wait: 600 });
      const name = s.page.locator("#ai-key-name");
      await s.fill(name, s.t("LLM Course Lab", "大型語言模型實作"));
      await s.shot(2, name);
      const purpose = s.page.locator("#ai-purpose");
      await s.fill(purpose, s.t(
        "Generative AI course, week 6 lab: build a RAG demo that answers questions about our course slides with the campus models.",
        "生成式 AI 課程第六週實作：用校內模型做 RAG 示範，回答課程投影片內容相關的問題。",
      ));
      await s.shot(3, [purpose, s.page.locator("#ai-purpose-hint")]);
      const duration = s.page.locator("#ai-duration");
      await duration.selectOption("90d");
      await s.settle(150);
      await s.shot(4, duration);
      await s.click(s.page.locator('[data-guide="ai-submit"]'), { wait: 900 });
      const r = row(s, s.t("LLM Course Lab", "大型語言模型實作"));
      await s.shot(5, [tab(s, "records"), r.locator("td").nth(2)], { crop: MAIN(0, 680) });
    },
    // s02 Wait for approval
    {
      mocks(m) {
        const pending = {
          ...myRequests(m)[1], id: "r0a1f3e2-0009-4b00-8000-000000000009",
          api_key_name: m.L("LLM Course Lab", "大型語言模型實作"),
          purpose: m.L("Generative AI course, week 6 lab: build a RAG demo with the campus models.", "生成式 AI 課程第六週實作：用校內模型做 RAG 示範。"),
          status: "pending", reviewer_id: null, reviewer_email: null, review_comment: null, reviewed_at: null, created_at: ago(40 * MIN), duration: "90d",
        };
        let calls = 0;
        let credCalls = 0;
        const fresh = {
          id: "c0a1f3e2-0009-4b00-8000-000000000009", request_id: pending.id, base_url: "https://skylab.ntub.edu.tw",
          api_key_prefix: "ccai_Hn5wQe", api_key_name: pending.api_key_name, rate_limit: 20,
          created_at: iso(NOW), expires_at: later(90 * D), revoked_at: null,
        };
        aiApiMocks(m, {
          credsHandler: (st) => (++credCalls >= 2 ? [fresh, ...st.creds] : st.creds),
          requestsHandler: (st) => {
            calls += 1;
            const list = [calls >= 2 ? { ...pending, status: "approved", reviewed_at: iso(NOW), reviewer_id: users(m).admin.id, reviewer_email: users(m).admin.email } : pending, ...st.requests];
            return { data: list, count: list.length };
          },
        });
      },
      run: async (s) => {
        await s.go("/ai-api");
        await s.click(tab(s, "records"), { wait: 500 });
        // The page polls every 30 s while a request is pending.
        await s.page.locator("[data-sonner-toast]").first().waitFor({ timeout: 40000 });
        await s.settle(600);
        const name = s.t("LLM Course Lab", "大型語言模型實作");
        await s.shot(1, [s.page.locator("[data-sonner-toast]").first(), row(s, name).locator("td").nth(2)], { crop: MAIN(0, 620) });
        await s.page.evaluate(() => document.querySelectorAll("[data-sonner-toast]").forEach((t) => t.remove()));
        await s.click(tab(s, "keys"), { wait: 500 });
        await s.shot(2, [tab(s, "keys"), row(s, name).locator("td").nth(0), badge(row(s, name))], { crop: MAIN(0, 450) });
        await s.click(tab(s, "records"), { wait: 500 });
        const rej = row(s, myRequests(s.m)[0].api_key_name);
        await s.shot(3, [rej.locator("td").nth(2), rej.locator("td").nth(5)], { crop: MAIN(0, 620) });
      },
    },
    // s03 View and copy your key
    async (s) => {
      await s.go("/ai-api");
      await s.shot(1, [tab(s, "keys"), s.page.getByText(/^ccai_7Hk2Qm/), s.page.getByText(/^ccai_Rw9pXe/)], { crop: MAIN(0, 400) });
      const nameBtn = row(s, keyName(s, 0)).getByRole("button", { name: new RegExp(keyName(s, 0)) });
      await s.shot(2, nameBtn, { crop: MAIN(0, 400) });
      await s.click(nameBtn, { wait: 700 });
      const d = dialog(s);
      await s.shot(3, [
        d.getByRole("button", { name: s.t("Copy API Key", "複製 API Key") }),
        d.getByRole("button", { name: s.t("Copy key name", "複製金鑰名稱") }),
        d.getByRole("button", { name: s.t("Copy Base URL", "複製 Base URL") }),
      ]);
      await s.shot(4, d.getByRole("button", { name: s.t("Copy cURL command", "複製 cURL 指令") }));
    },
    // s04 Make your first call with API Quick Start
    async (s) => {
      await s.go("/ai-api");
      const qs = s.page.locator('[data-guide="ai-quick-start"]');
      await s.shot(1, qs, { crop: MAIN(0, 400) });
      await s.click(qs, { wait: 1200 });
      const d = dialog(s);
      const baseField = d.getByRole("button", { name: s.t("Copy Base URL", "複製 Base URL") }).locator("xpath=../..");
      const keyField = d.locator("select").locator("xpath=..");
      await s.shot(2, [baseField, keyField]);
      const cmd = d.getByRole("button", { name: s.t("Copy", "複製"), exact: true }).locator("xpath=..");
      await s.shot(3, cmd);
      const groups = d.getByRole("group");
      await s.click(groups.nth(1).getByRole("button", { name: "Python" }), { wait: 1500 });
      await s.shot(4, [groups.nth(0), groups.nth(1), d.getByRole("button", { name: s.t("Copy code", "複製程式碼") })]);
      const det = d.locator("details");
      await s.click(det.locator("summary"), { wait: 500 });
      await s.shot(5, det);
    },
    // s05 Chat with a model in the browser
    async (s) => {
      const key = `campus:api-chat:v1:${users(s.m).student.id}`;
      await s.page.addInitScript(({ k, v }) => { if (!localStorage.getItem(k)) localStorage.setItem(k, v); }, { k: key, v: JSON.stringify(chatHistory(s.m)) });
      const parts = CHAT_A[s.lang === "en" ? 0 : 1];
      await s.page.addInitScript({ content: streamScript(parts, 3) });
      await s.go("/ai-api");
      await s.click(tab(s, "chat"), { wait: 1200 });
      await s.shot(1, tab(s, "chat"), { crop: MAIN(0, 420) });
      await s.shot(2, [s.page.locator("#api-chat-model"), s.page.locator("#api-chat-credential")], { crop: MAIN(0, 420) });
      await s.click(s.page.getByRole("button", { name: s.t("New conversation", "新對話") }), { wait: 400 });
      const input = s.page.locator("#api-chat-input");
      await s.fill(input, CHAT_Q[s.lang === "en" ? 0 : 1]);
      await s.press("Enter", { wait: 800 });
      await s.page.getByRole("button", { name: s.t("Stop waiting", "停止等待") }).waitFor();
      await s.shot(3, [s.page.locator("article").last(), s.page.getByRole("button", { name: s.t("Stop waiting", "停止等待") })], { crop: MAIN(0, 828), newFrame: true });
      await s.page.evaluate(() => window.__chatRelease());
      await s.page.getByRole("button", { name: s.t("Send", "送出"), exact: true }).waitFor();
      await s.settle(500);
      await s.shot(4, [s.page.getByRole("button", { name: s.t("New conversation", "新對話") }), s.page.locator("aside ul").first()], { crop: MAIN(0, 560), newFrame: true });
    },
    // s06 Rename a key
    async (s) => {
      await s.go("/ai-api");
      const r = await openMenu(s, 0);
      await s.shot(1, s.page.getByRole("menuitem", { name: s.t("Rename", "重新命名") }), { crop: MAIN(0, 420) });
      await s.click(s.page.getByRole("menuitem", { name: s.t("Rename", "重新命名") }), { wait: 400 });
      const input = r.locator("input");
      await s.fill(input, s.t("Web Final (Team 3)", "網頁期末第三組"));
      await s.shot(2, [input, r.getByRole("button", { name: s.t("Rename", "重新命名") })], { crop: MAIN(0, 420), newFrame: true });
    },
    // s07 Regenerate a key
    async (s) => {
      await s.go("/ai-api");
      await openMenu(s, 1);
      const item = s.page.getByRole("menuitem", { name: s.t("Regenerate key", "重新產生金鑰") });
      await s.shot(1, item, { crop: MAIN(0, 450) });
      await s.click(item, { wait: 500 });
      const confirm = s.page.locator('[role="alertdialog"]').getByRole("button", { name: s.t("Regenerate", "重新產生"), exact: true });
      await s.shot(2, confirm);
      await s.click(confirm, { wait: 900 });
      await s.page.evaluate(() => document.querySelectorAll("[data-sonner-toast]").forEach((t) => t.remove()));
      const d = dialog(s);
      await s.shot(3, d.locator("dd").first());
    },
    // s08 Delete a key
    async (s) => {
      await s.go("/ai-api");
      await openMenu(s, 0);
      const item = s.page.getByRole("menuitem", { name: s.t("Delete", "刪除") });
      await s.shot(1, item, { crop: MAIN(0, 420) });
      await s.click(item, { wait: 500 });
      await s.shot(2, s.page.locator('[role="alertdialog"]').getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
    },
    // s09 See inactive keys
    async (s) => {
      await s.go("/ai-api");
      const show = s.page.getByRole("button", { name: s.t("Show inactive keys (2)", "顯示已失效的金鑰（2）") });
      await s.click(show, { wait: 500 });
      await s.shot(0, [
        s.page.getByRole("button", { name: s.t("Hide inactive keys", "收起已失效的金鑰") }),
        badge(row(s, myCredentials(s.m)[2].api_key_prefix)),
        badge(row(s, myCredentials(s.m)[3].api_key_prefix)),
      ], { crop: MAIN(0, 520) });
    },
    // s10 Check your usage
    async (s) => {
      await s.go("/ai-api");
      await s.click(tab(s, "usage"), { wait: 900 });
      await s.shot(1, tab(s, "usage"), { crop: MAIN(0, 820) });
      const range = s.page.locator('[data-guide="ai-usage-panel"]');
      await s.shot(2, range, { crop: MAIN(0, 820) });
      await s.shot(3, s.page.locator('[data-guide="ai-route-usage"]'), { crop: MAIN(0, 820) });
      const rows = s.page.locator('button[aria-controls^="usage-record-"]');
      await s.click(rows.nth(2), { wait: 400 });
      const open = rows.nth(2).locator("xpath=..");
      await open.evaluate((el) => el.scrollIntoView({ block: "center" }));
      await s.shot(4, open, { crop: await between(s, s.page.getByText(s.t("Call Details", "細項紀錄"), { exact: true }), rows.nth(4).locator("xpath=.."), -16, 0), scroll: false });
      const more = s.page.getByRole("button", { name: s.t("Load More", "載入更多") });
      await more.scrollIntoViewIfNeeded();
      // The floating AI assistant button would sit in the corner of this crop.
      await s.page.getByRole("button", { name: s.t("Open AI assistant", "開啟 AI 助手") }).evaluate((el) => { el.style.visibility = "hidden"; });
      await s.shot(5, more, { crop: await between(s, rows.nth(15).locator("xpath=.."), more, -1, 20), scroll: false });
    },
  ],
};
