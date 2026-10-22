// Shared mock data for the AI pages: ai-api, ai-api-keys, ai-api-review, ai-monitoring.
import { D, H, MIN, NOW, ago, iso, later } from "../lib/mock.mjs";
import { people, users } from "./common.mjs";

export const PUBLIC_BASE = "https://skylab.ntub.edu.tw";
export const MODELS = ["qwen3-32b", "llama-3.3-70b", "gemma-3-27b"];

// ------------------------------------------------------------------ student side

// The signed-in student's keys: two usable, one expired, one replaced by a regeneration.
export function myCredentials(m) {
  const cred = (id, request_id, name, prefix, created, expires, extra = {}) => ({
    id, request_id, base_url: PUBLIC_BASE, api_key_prefix: prefix, api_key_name: name,
    rate_limit: 20, created_at: created, expires_at: expires, revoked_at: null, ...extra,
  });
  return [
    cred("c0a1f3e2-0001-4b00-8000-000000000001", "r0a1f3e2-0001-4b00-8000-000000000001",
      m.L("Web Final Project", "網頁程式期末專題"), "ccai_7Hk2Qm", ago(12 * D), later(18 * D)),
    cred("c0a1f3e2-0002-4b00-8000-000000000002", "r0a1f3e2-0002-4b00-8000-000000000002",
      m.L("Thesis Chatbot", "畢業專題聊天機器人"), "ccai_Rw9pXe", ago(6 * D), later(84 * D)),
    cred("c0a1f3e2-0003-4b00-8000-000000000003", "r0a1f3e2-0003-4b00-8000-000000000003",
      m.L("Data Mining HW", "資料探勘作業"), "ccai_Lp4sVd", ago(40 * D), ago(10 * D)),
    // Old key of the thesis request, revoked when it was regenerated.
    cred("c0a1f3e2-0004-4b00-8000-000000000004", "r0a1f3e2-0002-4b00-8000-000000000002",
      m.L("Thesis Chatbot", "畢業專題聊天機器人"), "ccai_Zt3nBy", ago(20 * D), later(70 * D), { revoked_at: ago(6 * D) }),
  ];
}

export const fullKey = (prefix) => `${prefix}9fK3vQ2mT8xLr6Wn4pZs1Yb7Hc5Jd0Ge`;

export function myRequests(m) {
  const req = (n, name, purpose, status, created, reviewed, comment = null, duration = "30d") => ({
    id: `r0a1f3e2-000${n}-4b00-8000-00000000000${n}`, user_id: users(m).student.id,
    user_email: users(m).student.email, user_full_name: users(m).student.full_name,
    purpose, api_key_name: name, duration, rate_limit: 20, status,
    reviewer_id: status === "pending" ? null : users(m).admin.id,
    reviewer_email: status === "pending" ? null : users(m).admin.email,
    review_comment: comment, reviewed_at: reviewed, created_at: created,
  });
  return [
    req(5, m.L("Course Q&A Bot", "課程問答機器人"),
      m.L("Personal side project: a Discord bot that answers questions about the web programming course.", "個人專題：做一個回答網頁程式課程問題的 Discord 機器人。"),
      "rejected", ago(3 * D), ago(2 * D),
      m.L("Please describe which course this is for and how many students will use the bot.", "請說明是哪門課使用，以及預計有多少同學會用這個機器人。")),
    req(2, m.L("Thesis Chatbot", "畢業專題聊天機器人"),
      m.L("Graduation project: a customer-service chatbot that classifies intents with the campus models.", "畢業專題的客服聊天機器人，串接校內模型做意圖分類。"),
      "approved", ago(21 * D), ago(20 * D), null, "90d"),
    req(1, m.L("Web Final Project", "網頁程式期末專題"),
      m.L("Web programming final project: summarise forum posts with an LLM in a Flask app.", "網頁程式設計期末專題：在 Flask 網站裡用 LLM 摘要討論區文章。"),
      "approved", ago(13 * D), ago(12 * D), m.L("Approved. Remember to keep the key out of your GitHub repo.", "已核准，記得不要把金鑰放進 GitHub。")),
    req(3, m.L("Data Mining HW", "資料探勘作業"),
      m.L("Data mining homework 3: label 500 product reviews with a model and compare with my classifier.", "資料探勘第三次作業：用模型標註 500 則商品評論，和自己的分類器比較。"),
      "approved", ago(41 * D), ago(40 * D)),
  ];
}

export function myUsage(m, preset = "30d") {
  const days = preset === "7d" ? 7 : preset === "90d" ? 90 : 30;
  const daily = Array.from({ length: days + 1 }, (_, i) => {
    const d = new Date(NOW - (days - i) * D);
    const wave = Math.max(0, Math.round(6 + 5 * Math.sin(i / 2.1) + (i % 7 === 5 ? -6 : 0)));
    return {
      date: d.toISOString().slice(0, 10), requests: wave,
      input_tokens: wave * 410 + (i % 3) * 120, output_tokens: wave * 690 + (i % 4) * 90,
    };
  });
  const sum = (k) => daily.reduce((s, x) => s + x[k], 0);
  const total = sum("requests");
  return {
    total_requests: total, total_input_tokens: sum("input_tokens"), total_output_tokens: sum("output_tokens"),
    by_model: {
      "qwen3-32b": { requests: Math.round(total * 0.62), input_tokens: Math.round(sum("input_tokens") * 0.6), output_tokens: Math.round(sum("output_tokens") * 0.64) },
      "llama-3.3-70b": { requests: Math.round(total * 0.28), input_tokens: Math.round(sum("input_tokens") * 0.3), output_tokens: Math.round(sum("output_tokens") * 0.27) },
      "gemma-3-27b": { requests: total - Math.round(total * 0.62) - Math.round(total * 0.28), input_tokens: Math.round(sum("input_tokens") * 0.1), output_tokens: Math.round(sum("output_tokens") * 0.09) },
    },
    daily, start_date: iso(NOW - days * D), end_date: iso(NOW),
  };
}

export function myUsageRecords(m, total = 47) {
  const creds = myCredentials(m);
  return Array.from({ length: total }, (_, i) => {
    const c = creds[i % 3 === 2 ? 1 : 0];
    const failed = i === 2 || i === 17;
    const model = MODELS[i % 5 === 3 ? 1 : i % 7 === 4 ? 2 : 0];
    const input = failed ? 0 : 180 + ((i * 97) % 900);
    const output = failed ? 0 : 260 + ((i * 131) % 1400);
    return {
      id: `5e1c${String(1000 + i).padStart(4, "0")}-7a2b-4c3d-9e8f-${String(400000000000 + i * 7919)}`,
      route: "model", credential_id: c.id, api_key_name: c.api_key_name, api_key_prefix: c.api_key_prefix,
      model_name: model, call_type: i % 4 === 1 ? "response" : "chat_completion", preset: null,
      request_id: `req_${(83421 + i * 37).toString(16)}`, upstream_request_id: null,
      input_tokens: input, output_tokens: output, total_tokens: input + output,
      request_duration_ms: failed ? 30000 : 900 + ((i * 337) % 4200), first_token_ms: failed ? null : 180 + (i % 9) * 30,
      stream: true, usage_reported: !failed, response_model: model, e2e_output_tokens_per_second: null,
      status: failed ? "error" : "success",
      error_message: failed ? m.L("Upstream model timed out after 30 seconds (502).", "上游模型 30 秒內沒有回應（502）。") : null,
      started_at: iso(NOW - (i * 7 + 2) * H - 17 * MIN), completed_at: iso(NOW - (i * 7 + 2) * H - 16 * MIN),
      created_at: iso(NOW - (i * 7 + 2) * H - 17 * MIN),
    };
  });
}

export const modelList = () => ({
  object: "list",
  data: MODELS.map((id) => ({ id, object: "model", owned_by: "skylab", model_info: { mode: "chat" } })),
});

// Endpoints the AI API page needs, with a mutable state in m.state.ai.
export function aiApiMocks(m, opts = {}) {
  const st = (m.state.ai = {
    creds: opts.creds ?? myCredentials(m),
    requests: opts.requests ?? myRequests(m),
  });
  m.use([
    ["GET", /^\/api\/v1\/ai-api\/credentials\/my$/, () => {
      const data = opts.credsHandler ? opts.credsHandler(st) : st.creds;
      return { data, count: data.length, public_base_url: PUBLIC_BASE };
    }],
    ["GET", /^\/api\/v1\/ai-api\/requests\/my$/, () => (opts.requestsHandler ? opts.requestsHandler(st) : { data: st.requests, count: st.requests.length })],
    ["POST", /^\/api\/v1\/ai-api\/requests$/, ({ body }) => {
      const r = {
        ...myRequests(m)[0], id: "r0a1f3e2-0009-4b00-8000-000000000009", purpose: body.purpose,
        api_key_name: body.api_key_name, duration: body.duration, status: "pending",
        reviewer_id: null, reviewer_email: null, review_comment: null, reviewed_at: null, created_at: iso(NOW),
      };
      st.requests = [r, ...st.requests];
      return r;
    }],
    ["GET", /^\/api\/v1\/ai-api\/credentials\/(?!my$)([^/]+)$/, ({ mt }) => {
      const c = st.creds.find((x) => x.id === mt[1]);
      if (!c) return { __status: 404, __body: { detail: "Not found" } };
      return { ...c, api_key: fullKey(c.api_key_prefix) };
    }],
    ["PATCH", /^\/api\/v1\/ai-api\/credentials\/([^/]+)$/, ({ mt, body }) => {
      st.creds = st.creds.map((x) => (x.id === mt[1] ? { ...x, ...body } : x));
      return st.creds.find((x) => x.id === mt[1]);
    }],
    ["POST", /^\/api\/v1\/ai-api\/credentials\/([^/]+)\/rotate$/, ({ mt }) => {
      const old = st.creds.find((x) => x.id === mt[1]);
      const fresh = { ...old, id: "c0a1f3e2-0009-4b00-8000-000000000009", api_key_prefix: "ccai_Xq8vNc", created_at: iso(NOW), revoked_at: null };
      st.creds = [fresh, ...st.creds.map((x) => (x.id === old.id ? { ...x, revoked_at: iso(NOW) } : x))];
      return { ...fresh, api_key: fullKey(fresh.api_key_prefix) };
    }],
    ["DELETE", /^\/api\/v1\/ai-api\/credentials\/([^/]+)$/, ({ mt }) => {
      st.creds = st.creds.filter((x) => x.id !== mt[1]);
      return { message: "ok" };
    }],
    ["GET", /^\/api\/v1\/ai-api\/usage\/my$/, ({ query }) => {
      const days = Math.round((Date.parse(query.end_date) - Date.parse(query.start_date)) / D);
      return myUsage(m, days <= 7 ? "7d" : days <= 31 ? "30d" : "90d");
    }],
    ["GET", /^\/api\/v1\/ai-api\/usage\/records\/my$/, ({ query }) => {
      const all = myUsageRecords(m);
      const skip = Number(query.skip || 0);
      const limit = Number(query.limit || 20);
      return { data: all.slice(skip, skip + limit), count: all.length };
    }],
    ["GET", /^\/api\/v1\/ai-proxy\/models$/, () => modelList()],
  ]);
  return st;
}

// ------------------------------------------------------------------ admin: review

export function reviewRequests(m) {
  const P = people(m);
  const U = users(m);
  const row = (n, person, name, purpose, status, created, reviewed = null, comment = null, duration = "30d") => ({
    id: `a7e2c9d0-00${String(n).padStart(2, "0")}-4c00-8000-0000000000${String(n).padStart(2, "0")}`,
    user_id: person.id, user_email: person.email, user_full_name: person.full_name,
    purpose, api_key_name: name, duration, rate_limit: 20, status,
    reviewer_id: status === "pending" ? null : U.admin.id, reviewer_email: status === "pending" ? null : U.admin.email,
    review_comment: comment, reviewed_at: reviewed, created_at: created,
  });
  return [
    row(1, P[0], m.L("Thesis Chatbot", "專題聊天機器人"),
      m.L("Graduation project: a customer-service chatbot for the library that classifies questions by intent with the campus models and hands hard cases to staff.",
        "畢業專題：替圖書館做客服聊天機器人，用校內模型判斷讀者問題的意圖，難以回答的再轉給館員處理，預計明年一月展示。"),
      "pending", ago(35 * MIN)),
    row(2, P[3], m.L("NLP Homework 4", "自然語言處理作業四"),
      m.L("NLP course homework 4: compare zero-shot sentiment labels from an LLM with my fine-tuned BERT model.", "自然語言處理課程作業四：比較 LLM 零樣本情緒標註與自己微調的 BERT 模型。"),
      "pending", ago(3 * H)),
    row(3, P[5], m.L("Teaching Assistant", "課程助教機器人"),
      m.L("Cloud computing course: an assistant that answers students' Docker questions during lab hours.", "雲端運算實務課程：實作課時回答同學 Docker 問題的助教機器人。"),
      "pending", ago(22 * H), null, null, "never"),
    row(4, users(m).student, m.L("Web Final Project", "網頁程式期末專題"),
      m.L("Web programming final project: summarise forum posts with an LLM in a Flask app.", "網頁程式設計期末專題：在 Flask 網站裡用 LLM 摘要討論區文章。"),
      "approved", ago(13 * D), ago(12 * D), m.L("Approved. Keep the key out of your GitHub repo.", "已核准，記得不要把金鑰放進 GitHub。")),
    row(5, P[1], m.L("Resume Helper", "履歷產生器"),
      m.L("Generate my resume automatically.", "幫我自動產生履歷。"),
      "rejected", ago(5 * D), ago(4 * D), m.L("Please explain which course or project this is for.", "請說明是哪門課或哪個專題要用。")),
    row(6, P[2], m.L("Data Mining HW", "資料探勘作業"),
      m.L("Data mining homework 3: label 500 product reviews with a model and compare with my classifier.", "資料探勘第三次作業：用模型標註 500 則商品評論，和自己的分類器比較。"),
      "approved", ago(9 * D), ago(8 * D)),
    row(7, users(m).teacher, m.L("Course Materials", "教材產生"),
      m.L("Draft practice questions for the operating systems course and check them before class.", "替作業系統課程草擬練習題，上課前再人工檢查。"),
      "approved", ago(20 * D), ago(20 * D), null, "never"),
    row(8, P[4], m.L("Game NPC", "遊戲 NPC"),
      m.L("Personal game mod with chatting NPCs.", "個人遊戲模組，讓 NPC 可以聊天。"),
      "rejected", ago(16 * D), ago(15 * D), m.L("Not related to a course; please use a personal account for hobby projects.", "與課程無關，個人興趣專案請使用自己的帳號。")),
  ];
}

export function reviewMocks(m) {
  const st = (m.state.review = { rows: reviewRequests(m) });
  m.use([
    ["GET", /^\/api\/v1\/ai-api\/requests$/, ({ query }) => {
      const data = query.status ? st.rows.filter((r) => r.status === query.status) : st.rows;
      return { data: data.slice(0, Number(query.limit || 100)), count: data.length };
    }],
    ["POST", /^\/api\/v1\/ai-api\/requests\/([^/]+)\/review$/, ({ mt, body }) => {
      st.rows = st.rows.map((r) => (r.id === mt[1] ? { ...r, status: body.status, review_comment: body.review_comment, reviewed_at: iso(NOW) } : r));
      return st.rows.find((r) => r.id === mt[1]);
    }],
  ]);
  return st;
}

// ------------------------------------------------------------------ admin: key management

export function adminCredentials(m) {
  const P = people(m);
  const U = users(m);
  const k = (n, person, name, prefix, purpose, status, created, lastUsed, extra = {}) => ({
    id: `b3d4e5f6-00${String(n).padStart(2, "0")}-4d00-8000-0000000000${String(n).padStart(2, "0")}`,
    user_id: person.id, user_email: person.email, user_full_name: person.full_name, user_role: person.role,
    request_id: `a7e2c9d0-00${String(n).padStart(2, "0")}-4c00-8000-0000000000${String(n).padStart(2, "0")}`,
    base_url: PUBLIC_BASE, api_key_prefix: prefix, api_key_name: name, rate_limit: 20, status,
    inactive_reason: null, expires_at: later(30 * D), revoked_at: null, deleted_at: null, created_at: created,
    request_purpose: purpose, reviewer_email: U.admin.email, reviewer_full_name: U.admin.full_name,
    reviewed_at: created, last_used_at: lastUsed, ...extra,
  });
  return [
    k(1, U.student, m.L("Web Final Project", "網頁程式期末專題"), "ccai_7Hk2Qm", m.L("Web programming final project: summarise forum posts with an LLM.", "網頁程式設計期末專題：用 LLM 摘要討論區文章。"), "active", ago(12 * D), ago(25 * MIN)),
    k(2, P[0], m.L("Thesis Chatbot", "專題聊天機器人"), "ccai_Mf2kTw", m.L("Library customer-service chatbot for my graduation project.", "畢業專題：圖書館客服聊天機器人。"), "active", ago(9 * D), ago(2 * H)),
    k(3, U.teacher, m.L("Course Materials", "教材產生"), "ccai_Qa8rLc", m.L("Draft practice questions for the operating systems course.", "替作業系統課程草擬練習題。"), "active", ago(20 * D), ago(1 * D), { expires_at: null }),
    k(4, P[2], m.L("Data Mining HW", "資料探勘作業"), "ccai_Vb6yHn", m.L("Label 500 product reviews and compare with my classifier.", "標註 500 則商品評論並與自己的分類器比較。"), "active", ago(8 * D), ago(3 * D)),
    k(5, P[5], m.L("Teaching Assistant", "課程助教機器人"), "ccai_Gt3pWe", m.L("Answer students' Docker questions during lab hours.", "實作課時回答同學的 Docker 問題。"), "active", ago(5 * D), null, { expires_at: null }),
    k(6, P[3], m.L("NLP Homework 3", "自然語言處理作業三"), "ccai_Ns5dQz", m.L("Compare zero-shot sentiment labels with a fine-tuned BERT.", "比較零樣本情緒標註與微調後的 BERT。"), "active", ago(26 * D), ago(19 * D)),
    k(7, U.admin, m.L("Ops Scripts", "維運腳本"), "ccai_Ad1mXk", m.L("Summarise nightly backup logs for the operations team.", "替維運團隊摘要每晚的備份日誌。"), "active", ago(48 * D), ago(40 * MIN), { expires_at: null }),
    k(8, P[1], m.L("Old Chatbot", "舊版聊天機器人"), "ccai_Ke7uRb", m.L("Chatbot prototype for the cloud computing course.", "雲端運算課程的聊天機器人雛型。"), "inactive", ago(70 * D), ago(41 * D), { inactive_reason: "expired", expires_at: ago(40 * D) }),
    k(9, P[0], m.L("Thesis Chatbot", "專題聊天機器人"), "ccai_Pz9cJa", m.L("Library customer-service chatbot for my graduation project.", "畢業專題：圖書館客服聊天機器人。"), "inactive", ago(30 * D), ago(9 * D), { inactive_reason: "revoked", revoked_at: ago(9 * D) }),
    k(10, P[4], m.L("Game NPC", "遊戲 NPC"), "ccai_Wr4tFs", m.L("Chatting NPCs for a personal game mod.", "個人遊戲模組裡會聊天的 NPC。"), "inactive", ago(60 * D), ago(33 * D), { inactive_reason: "deleted", deleted_at: ago(30 * D) }),
  ];
}

export function adminKeyMocks(m, { total = null, fill = 0 } = {}) {
  let rows = adminCredentials(m);
  if (fill) {
    // A long list: course projects of many owners, each with its own id, prefix and dates.
    const chars = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    const base = rows[0];
    const owners = [users(m).student, ...people(m), users(m).teacher];
    const projects = [
      ["Web Final Project", "網頁程式期末專題", "Summarise forum posts with an LLM in a Flask app.", "在 Flask 網站裡用 LLM 摘要討論區文章。"],
      ["NLP Homework 4", "自然語言處理作業四", "Compare zero-shot sentiment labels with a fine-tuned BERT.", "比較零樣本情緒標註與微調後的 BERT。"],
      ["Library Chatbot", "圖書館客服機器人", "Answer readers' questions about borrowing rules.", "回答讀者借還書規則的問題。"],
      ["Data Mining HW", "資料探勘作業", "Label 500 product reviews and compare with my classifier.", "標註 500 則商品評論並與自己的分類器比較。"],
      ["Docker Lab Helper", "Docker 實作小幫手", "Explain Docker error messages during lab hours.", "實作課時解釋 Docker 錯誤訊息。"],
      ["Resume Reviewer", "履歷健檢", "Career course: give feedback on resume drafts.", "職涯課程：替履歷草稿提供修改建議。"],
      ["SQL Tutor", "SQL 家教", "Database course: explain query plans to classmates.", "資料庫課程：替同學解釋查詢計畫。"],
      ["Quiz Generator", "測驗題產生器", "Draft weekly quiz questions for the networking course.", "替網路概論課草擬每週小考題目。"],
      ["Meeting Notes", "會議紀錄摘要", "Project course: summarise weekly team meeting notes.", "專題課：摘要每週小組會議紀錄。"],
      ["Code Review Bot", "程式碼審查機器人", "Software engineering course: comment on pull requests.", "軟體工程課：替 Pull Request 留審查意見。"],
      ["IoT Dashboard", "物聯網儀表板", "Explain sensor anomalies in the IoT capstone project.", "物聯網畢業專題：解釋感測器異常數據。"],
    ];
    rows = Array.from({ length: fill }, (_, i) => {
      const who = owners[i % owners.length];
      const [en, zh, pen, pzh] = projects[(i * 4) % projects.length];
      const p = Array.from({ length: 6 }, (_, j) => chars[(i * 7 + j * 13 + 3) % chars.length]).join("");
      return {
        ...base, id: `${base.id.slice(0, 24)}${String(100000000000 + i)}`, api_key_prefix: `ccai_${p}`,
        user_id: who.id, user_email: who.email, user_full_name: who.full_name, user_role: who.role,
        api_key_name: m.L(en, zh), request_purpose: m.L(pen, pzh),
        created_at: iso(NOW - (i * 9 + 2) * H), reviewed_at: iso(NOW - (i * 9 + 2) * H),
        last_used_at: i % 6 === 4 ? null : iso(NOW - (i * 5 + 1) * H - (i % 4) * 13 * MIN),
      };
    });
  }
  const st = (m.state.keys = { rows });
  m.use([
    ["GET", /^\/api\/v1\/ai-api\/credentials$/, ({ url }) => {
      const q = url.searchParams;
      const roles = q.getAll("user_role");
      const text = (q.get("query") || "").toLowerCase();
      const after = q.get("created_after");
      const base = st.rows.filter((r) =>
        (!roles.length || roles.includes(r.user_role))
        && (!after || Date.parse(r.created_at) >= Date.parse(after))
        && (!text || [r.api_key_name, r.user_full_name, r.user_email, r.api_key_prefix].some((v) => (v || "").toLowerCase().includes(text))));
      const status = q.get("status");
      const data = status ? base.filter((r) => r.status === status) : base;
      const active = base.filter((r) => r.status === "active").length;
      const count = total ?? data.length;
      return {
        data: data.slice(0, Number(q.get("limit") || 50)), count,
        total_count: total ?? base.length, active_count: total ?? active, inactive_count: base.length - active,
      };
    }],
    ["DELETE", /^\/api\/v1\/ai-api\/credentials\/([^/]+)$/, ({ mt }) => {
      st.rows = st.rows.map((r) => (r.id === mt[1] ? { ...r, status: "inactive", inactive_reason: "deleted", deleted_at: iso(NOW) } : r));
      return { message: "ok" };
    }],
  ]);
  return st;
}

// ------------------------------------------------------------------ admin: monitoring

// A request ID like the 32 hex digits nginx puts in X-Request-ID.
const hexId = (seed) => { let x = (seed * 2654435761) >>> 0; return Array.from({ length: 32 }, () => { x = (Math.imul(x, 1664525) + 1013904223) >>> 0; return (x >>> 28).toString(16); }).join(""); };

export function monitoringMocks(m, opts = {}) {
  const P = people(m);
  const U = users(m);
  const who = [U.student, P[0], U.teacher, P[3], P[2], P[5]];
  m.use([
    ["GET", /^\/api\/v1\/ai-api\/monitoring\/overview$/, ({ query }) => {
      if (opts.overviewError) return { __status: 500, __body: { detail: "error" } };
      const hourly = query.bucket === "hour";
      const days = Math.round((Date.parse(query.end_date) - Date.parse(query.start_date)) / D);
      const n = hourly ? days * 24 : days;
      const step = hourly ? H : D;
      const series = Array.from({ length: n }, (_, i) => {
        const t = NOW - (n - 1 - i) * step;
        const hour = new Date(t + 8 * H).getUTCHours();
        const day = new Date(t + 8 * H).getUTCDay();
        const scale = hourly ? (hour >= 8 && hour <= 22 ? 1 : 0.15) * (day === 0 || day === 6 ? 0.45 : 1) : (day === 0 || day === 6 ? 0.5 : 1);
        const base = hourly ? 64 : 1050;
        const calls = Math.round(base * scale * (0.85 + 0.3 * Math.abs(Math.sin(i / 3.7))));
        const errBoost = opts.errorSpike && i > n - 8 ? 0.09 : 0;
        const failed = Math.round(calls * (0.011 + 0.006 * Math.abs(Math.cos(i / 2.3)) + errBoost));
        return {
          bucket_start: iso(t), total_calls: calls, successful_calls: calls - failed, failed_calls: failed,
          total_tokens: calls * (520 + (i % 5) * 30), error_rate: calls ? Math.round((failed / calls) * 1000) / 10 : null,
          avg_latency_ms: calls ? 1650 + Math.round(400 * Math.abs(Math.sin(i / 5))) : null,
          proxy_calls: Math.round(calls * 0.78), template_calls: calls - Math.round(calls * 0.78),
        };
      });
      const total = series.reduce((s, x) => s + x.total_calls, 0);
      const failed = series.reduce((s, x) => s + x.failed_calls, 0);
      const errorRate = Math.round((failed / total) * 1000) / 10;
      return {
        start_date: query.start_date, end_date: query.end_date, bucket: hourly ? "hour" : "day",
        summary: { total_calls: total, successful_calls: total - failed, failed_calls: failed, error_rate: errorRate, total_tokens: series.reduce((s, x) => s + x.total_tokens, 0), avg_latency_ms: 1840, active_users: hourly ? 143 : days > 31 ? 412 : 287 },
        comparison: { total_calls_delta: Math.round(total * 0.18), total_calls_percent: 18.4, failed_calls_delta: 12, failed_calls_percent: 9.1, error_rate_delta: opts.errorSpike ? 2.6 : -0.3, avg_latency_ms_delta: -40 },
        series,
        model_breakdown: [
          { model_name: "qwen3-32b", total_calls: Math.round(total * 0.56), total_tokens: Math.round(total * 0.56 * 510), failed_calls: Math.round(failed * 0.4), error_rate: 1.1, avg_latency_ms: 1620 },
          { model_name: "llama-3.3-70b", total_calls: Math.round(total * 0.29), total_tokens: Math.round(total * 0.29 * 590), failed_calls: Math.round(failed * 0.35), error_rate: 1.9, avg_latency_ms: 2240 },
          { model_name: "gemma-3-27b", total_calls: Math.round(total * 0.15), total_tokens: Math.round(total * 0.15 * 560), failed_calls: Math.round(failed * 0.25), error_rate: 2.4, avg_latency_ms: 1980 },
          { model_name: "mistral-7b-instruct", total_calls: 38, total_tokens: 19200, failed_calls: 0, error_rate: 0, avg_latency_ms: 940 },
        ],
      };
    }],
    ["GET", /^\/api\/v1\/ai-api\/monitoring\/litellm-runtime$/, () => {
      const degraded = opts.degraded;
      return {
        checked_at: iso(NOW), liveliness: true, readiness: true,
        gateway: { status: "available", liveliness: true, readiness: true },
        summary: { online: degraded ? 2 : 3, degraded: degraded ? 1 : 0, offline: 0, unknown: 0 },
        models: [
          { name: "qwen3-32b", status: "online", healthy_deployments: 2, unhealthy_deployments: 0 },
          { name: "llama-3.3-70b", status: degraded ? "degraded" : "online", healthy_deployments: 1, unhealthy_deployments: degraded ? 1 : 0 },
          { name: "gemma-3-27b", status: "online", healthy_deployments: 1, unhealthy_deployments: 0 },
        ],
        model_discovery: "ok", healthy_deployment_count: degraded ? 4 : 5, unhealthy_deployment_count: degraded ? 1 : 0, deployment_status_code: 200,
      };
    }],
    ["GET", /^\/api\/v1\/ai-api\/monitoring\/api-calls$/, () => {
      const data = Array.from({ length: 24 }, (_, i) => {
        const u = who[i % who.length];
        const model = MODELS[[0, 1, 0, 2, 1][i % 5]];
        const failed = i === 3 || i === 11 || i === 19;
        const missing = i === 8;
        const swapped = i === 4;
        const input = failed ? 0 : 160 + ((i * 83) % 1200);
        const output = failed || missing ? 0 : 240 + ((i * 149) % 1600);
        const dur = failed ? 30012 : 1100 + ((i * 271) % 3800);
        return {
          id: `d1f2a3b4-${String(1000 + i)}-4e00-8000-0000000${String(10000 + i)}`,
          user_id: u.id, user_email: u.email, user_full_name: u.full_name,
          credential_id: "b3d4e5f6-0001-4d00-8000-000000000001", model_name: model,
          request_type: i % 4 === 1 ? "response" : "chat_completion",
          request_id: hexId(i + 11), upstream_request_id: `chatcmpl-${(0x9f3200 + i * 211).toString(16)}`,
          input_tokens: missing ? 0 : input, output_tokens: output, request_duration_ms: dur,
          first_token_ms: failed ? null : 210 + (i % 7) * 45, stream: true, usage_reported: !failed && !missing,
          response_model: swapped ? `${model}-fp8` : model,
          e2e_output_tokens_per_second: failed || missing ? null : Math.round((output / (dur / 1000)) * 10) / 10,
          status: failed ? "error" : "success",
          error_message: failed ? m.L("Upstream timeout", "上游逾時") : null,
          started_at: iso(NOW - (i * 13 + 4) * MIN), completed_at: iso(NOW - (i * 13 + 3) * MIN), created_at: iso(NOW - (i * 13 + 4) * MIN),
        };
      });
      return { data, count: 1268 };
    }],
    ["GET", /^\/api\/v1\/ai-api\/monitoring\/template-calls$/, () => {
      const types = ["template_recommendation", "ai_navigation", "teacher_judge", "pve_log_chat", "contextual_help"];
      const data = Array.from({ length: 12 }, (_, i) => {
        const u = who[(i + 2) % who.length];
        const input = 900 + ((i * 211) % 2400);
        const output = 300 + ((i * 97) % 800);
        const dur = 2200 + ((i * 313) % 4000);
        return {
          id: `e2a3b4c5-${String(2000 + i)}-4f00-8000-0000000${String(20000 + i)}`,
          user_id: u.id, user_email: u.email, user_full_name: u.full_name, call_type: types[i % types.length],
          model_name: "qwen3-32b", preset: null, request_id: hexId(i + 57), upstream_request_id: null,
          input_tokens: input, output_tokens: output, request_duration_ms: dur, first_token_ms: 380 + i * 20, stream: false,
          usage_reported: true, response_model: "qwen3-32b", e2e_output_tokens_per_second: Math.round((output / (dur / 1000)) * 10) / 10,
          status: "success", error_message: null, started_at: iso(NOW - (i * 41 + 9) * MIN), completed_at: iso(NOW - (i * 41 + 8) * MIN), created_at: iso(NOW - (i * 41 + 9) * MIN),
        };
      });
      return { data, count: 318 };
    }],
    ["GET", /^\/api\/v1\/ai-api\/monitoring\/users$/, () => {
      const rows = [
        [U.student, 212, 41200, 98300, 9, 2200, 3900, 2, 1.0, 1710],
        [P[0], 187, 52800, 121400, 4, 900, 1500, 3, 1.6, 1820],
        [U.teacher, 87, 18800, 40100, 64, 51000, 72000, 1, 0.7, 2010],
        [P[3], 158, 30100, 71400, 3, 700, 1200, 4, 2.5, 1660],
        [P[2], 96, 21500, 44800, 0, 0, 0, 0, 0, 1590],
        [P[5], 41, 7300, 15800, 22, 18000, 26500, 1, 1.6, 2140],
        [U.admin, 12, 9800, 6100, 31, 30500, 21800, 0, 0, 1930],
      ];
      return {
        data: rows.map(([u, pc, pi, po, tc, ti, to, f, er, lat]) => ({
          user_id: u.id, user_email: u.email, user_full_name: u.full_name, proxy_calls: pc, proxy_input_tokens: pi, proxy_output_tokens: po,
          template_calls: tc, template_input_tokens: ti, template_output_tokens: to, failed_calls: f, error_rate: er, avg_latency_ms: lat,
        })),
        count: rows.length,
      };
    }],
    ["POST", /^\/api\/v1\/monitoring\/grafana\/session$/, () => (opts.grafana ? { enabled: true, url: "https://skylab.ntub.edu.tw/grafana" } : { enabled: false, url: null })],
  ]);
}
