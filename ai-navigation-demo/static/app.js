const NAV_GROUPS = [
  {
    key: "personal",
    label: "個人",
    items: [
      { key: "dashboard", label: "總覽" },
      { key: "my-resources", label: "我的資源" },
      { key: "my-requests", label: "我的申請" },
      { key: "request-form", label: "建立申請" },
    ],
  },
  {
    key: "network",
    label: "網路",
    items: [
      { key: "firewall", label: "防火牆" },
      { key: "reverse-proxy", label: "反向代理" },
      { key: "domain", label: "網域管理" },
      { key: "ip-management", label: "IP 管理" },
      { key: "gateway", label: "閘道 VM" },
    ],
  },
  {
    key: "resource",
    label: "資源",
    items: [
      { key: "resource-mgmt", label: "資源管理" },
      { key: "gpu-mgmt", label: "GPU 管理" },
      { key: "request-review", label: "申請審核" },
      { key: "batch-review", label: "批量審核" },
    ],
  },
  {
    key: "ai",
    label: "AI 服務",
    items: [
      { key: "ai-api", label: "AI API 申請" },
      { key: "ai-api-review", label: "AI API 審核" },
      { key: "ai-api-keys", label: "AI API 金鑰" },
      { key: "ai-monitoring", label: "AI 監控" },
      { key: "ai-management", label: "AI 管理" },
    ],
  },
  {
    key: "system",
    label: "系統",
    items: [
      { key: "groups", label: "群組" },
      { key: "admin", label: "使用者管理" },
      { key: "settings", label: "系統設定" },
      { key: "migration", label: "Migration Jobs" },
      { key: "jobs", label: "背景任務" },
      { key: "audit", label: "Audit Logs" },
    ],
  },
]

const roleSelect = document.querySelector("#role")
const queryInput = document.querySelector("#query")
const resolveBtn = document.querySelector("#resolveBtn")
const chatForm = document.querySelector("#chatForm")
const chatLog = document.querySelector("#chatLog")
const sidebarNav = document.querySelector("#sidebarNav")
const pageView = document.querySelector("#pageView")
const chatResizeHandle = document.querySelector("#chatResizeHandle")
const actionBadge = document.querySelector("#actionBadge")
const modeButtons = document.querySelectorAll("[data-mode]")

let pages = []
let pageMap = new Map()
let currentPageKey = "dashboard"
let navigationSource = "ready"
let guideMode = "guide"

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;")
}

function pageByKey(key) {
  return pageMap.get(key) || pageMap.get("dashboard") || pages[0]
}

function navigateTo(key) {
  const page = pageByKey(key)
  if (!page) return
  currentPageKey = page.key
  document.querySelectorAll("[data-nav-key]").forEach((button) => {
    button.classList.toggle("active", button.dataset.navKey === page.key)
    button.disabled = !pageMap.has(button.dataset.navKey)
  })
  renderPage(page)
}

function renderSidebar() {
  sidebarNav.innerHTML = NAV_GROUPS.map((group) => {
    const items = group.items
      .map((item) => {
        const allowed = pageMap.has(item.key)
        return `
          <button class="nav-item ${allowed ? "" : "disabled"}" data-nav-key="${escapeHtml(item.key)}" ${allowed ? "" : "disabled"}>
            <span>${escapeHtml(item.label)}</span>
            <small>${escapeHtml(item.key)}</small>
          </button>
        `
      })
      .join("")
    return `
      <section class="nav-group">
        <h3>${escapeHtml(group.label)}</h3>
        ${items}
      </section>
    `
  }).join("")

  document.querySelectorAll("[data-nav-key]").forEach((button) => {
    button.addEventListener("click", () => navigateTo(button.dataset.navKey))
  })
}

function renderPage(page) {
  const mockRows = {
    dashboard: ["資源使用率 68%", "待審申請 4", "背景任務 12", "AI API 呼叫 1,284"],
    "my-resources": ["VM ubuntu-lab running", "LXC nginx stopped", "GPU quota 1/2"],
    "my-requests": ["GPU VM 申請審核中", "LXC 課程環境已核准", "反向代理申請退回補件"],
    "request-form": ["資源類型", "CPU / Memory / Disk", "使用時段", "送出申請"],
    firewall: ["VM 連線拓樸", "Inbound 22/tcp", "Web 443/tcp", "NAT 規則"],
    "reverse-proxy": ["Domain", "Target VM", "Internal Port", "HTTPS 狀態"],
    domain: ["example.edu", "lab.example.edu", "api.example.edu"],
    "ip-management": ["10.0.1.0/24", "10.0.2.0/24", "可用 IP 42"],
    gateway: ["Traefik running", "DNS sync ready", "Gateway VM online"],
    "resource-mgmt": ["vm-201 running", "ct-104 stopped", "vm-335 provisioning"],
    "gpu-mgmt": ["RTX 4090 2/4", "A100 0/1", "節點 pve-gpu-01"],
    "request-review": ["學生 A GPU VM", "學生 B LXC", "課程 C 批次資源"],
    "batch-review": ["Linux Lab 48 users", "AI Course 32 users", "Pending batch 2"],
    "ai-api": ["用途說明", "模型選擇", "每日額度", "送出申請"],
    "ai-api-review": ["待審 token 3", "高額度申請 1", "退回補件 2"],
    "ai-api-keys": ["sk-demo-**** active", "今日用量 18%", "可輪替金鑰"],
    "ai-monitoring": ["Requests/min 42", "Error rate 0.8%", "Top user demo@lab"],
    "ai-management": ["Model Qwen", "Policy default", "Gateway healthy"],
    groups: ["CS101", "AI Lab", "Docker Workshop"],
    admin: ["teacher@example", "student@example", "admin@example"],
    settings: ["資源上限", "審核政策", "SMTP / Gateway"],
    migration: ["job migrate-12 running", "job migrate-09 failed", "job migrate-08 done"],
    jobs: ["provision running", "delete queued", "sync completed"],
    audit: ["login success", "role changed", "resource deleted"],
  }

  const rows = mockRows[page.key] || page.actions || []
  const cards = rows
    .slice(0, 4)
    .map((row) => `<div class="data-row">${escapeHtml(row)}</div>`)
    .join("")
  const actions = page.actions
    .map((action) => `<button class="page-action-btn">${escapeHtml(action)}</button>`)
    .join("")

  pageView.innerHTML = `
    <div class="page-body">
      <div class="slide-header">
        <div>
          <p class="eyebrow">Page Capability Router</p>
          <h1>${escapeHtml(page.title)}</h1>
        </div>
        <div class="status-strip">
          <span>${escapeHtml(page.key)}</span>
          <span>${escapeHtml(navigationSource)}</span>
        </div>
      </div>
      <p class="page-summary">${escapeHtml(page.summary)}</p>
      <div class="page-main">
        <div class="mock-table">${cards}</div>
        ${actions ? `<div class="action-stack">${actions}</div>` : ""}
      </div>
    </div>
  `
}

function appendMessage(type, html) {
  const article = document.createElement("article")
  article.className = `message ${type}`
  article.innerHTML = html
  chatLog.appendChild(article)
  chatLog.scrollTop = chatLog.scrollHeight
  return article
}

function renderTargetButton(target, label) {
  return `
    <button class="guide-btn" data-guide-key="${escapeHtml(target.page_key)}">
      <strong>${escapeHtml(label)} ${escapeHtml(target.title)}</strong>
      <span>${escapeHtml(target.reason)}</span>
    </button>
  `
}

function renderWorkflow(workflow) {
  if (!workflow || workflow.length === 0) return ""
  const title = guideMode === "shortcut" ? "快速路徑：" : "建議依序點擊："
  const steps = workflow
    .map(
      (step) => `
        <button class="workflow-step" data-guide-key="${escapeHtml(step.page_key)}">
          <b>${step.step}</b>
          <span>
            <strong>${escapeHtml(step.title)}</strong>
            <small>${escapeHtml(step.instruction || step.expected_result)}</small>
          </span>
        </button>
      `,
    )
    .join("")
  return `
    <div class="workflow ${guideMode === "shortcut" ? "workflow-shortcut" : ""}">
      <p>${title}</p>
      ${steps}
    </div>
  `
}

function wireGuideButtons() {
  document.querySelectorAll("[data-guide-key]").forEach((button) => {
    button.addEventListener("click", () => navigateTo(button.dataset.guideKey))
  })
}

function renderAiResult(data) {
  navigationSource = data.source
  actionBadge.textContent = data.action
  navigateTo(currentPageKey)

  const hasWorkflow = Array.isArray(data.workflow) && data.workflow.length > 0
  const primary = !hasWorkflow && data.primary ? renderTargetButton(data.primary, "前往") : ""
  const suggestions = !hasWorkflow
    ? data.suggestions.map((target) => renderTargetButton(target, "也可前往")).join("")
    : ""
  const clarify = data.clarification_question
    ? `<div class="clarify-box">${escapeHtml(data.clarification_question)}</div>`
    : ""

  appendMessage(
    "assistant",
    `
      ${primary}
      ${renderWorkflow(data.workflow)}
      ${suggestions}
      ${clarify}
    `,
  )
  wireGuideButtons()
}

async function loadCapabilities() {
  const response = await fetch(`/api/capabilities?role=${encodeURIComponent(roleSelect.value)}`)
  pages = await response.json()
  pageMap = new Map(pages.map((page) => [page.key, page]))
  renderSidebar()
  navigateTo(pageMap.has(currentPageKey) ? currentPageKey : "dashboard")
}

async function resolveNavigation(query) {
  appendMessage("user", `<p>${escapeHtml(query)}</p>`)
  resolveBtn.disabled = true
  actionBadge.textContent = "thinking"
  const pendingMessage = appendMessage(
    "assistant",
    `
      <div class="typing-state">
        <span></span>
        <span></span>
        <span></span>
        <p>${guideMode === "shortcut" ? "正在找最短路徑..." : "正在判斷目標頁面與操作順序..."}</p>
      </div>
    `,
  )
  try {
    const response = await fetch("/api/navigation/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, role: roleSelect.value, mode: guideMode }),
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    pendingMessage.remove()
    renderAiResult(await response.json())
  } catch (error) {
    pendingMessage.remove()
    appendMessage("assistant", `<div class="clarify-box">後端請求失敗：${escapeHtml(error.message)}</div>`)
    actionBadge.textContent = "error"
  } finally {
    resolveBtn.disabled = false
  }
}

function setGuideMode(mode) {
  guideMode = mode === "shortcut" ? "shortcut" : "guide"
  modeButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === guideMode)
  })
}

function setupChatResize() {
  if (!chatResizeHandle) return
  let dragging = false
  let startX = 0
  let startWidth = 0

  chatResizeHandle.addEventListener("pointerdown", (event) => {
    dragging = true
    startX = event.clientX
    startWidth = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--chat-width"), 10) || 320
    document.body.classList.add("is-resizing-chat")
    chatResizeHandle.setPointerCapture(event.pointerId)
  })

  chatResizeHandle.addEventListener("pointermove", (event) => {
    if (!dragging) return
    const nextWidth = Math.min(520, Math.max(280, startWidth - (event.clientX - startX)))
    document.documentElement.style.setProperty("--chat-width", `${nextWidth}px`)
  })

  chatResizeHandle.addEventListener("pointerup", (event) => {
    dragging = false
    document.body.classList.remove("is-resizing-chat")
    chatResizeHandle.releasePointerCapture(event.pointerId)
  })
}

chatForm.addEventListener("submit", (event) => {
  event.preventDefault()
  const query = queryInput.value.trim()
  if (query.length < 2) return
  queryInput.value = ""
  resolveNavigation(query)
})

queryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault()
    chatForm.dispatchEvent(new Event("submit"))
  }
})

roleSelect.addEventListener("change", loadCapabilities)
modeButtons.forEach((button) => {
  button.addEventListener("click", () => setGuideMode(button.dataset.mode))
})
setupChatResize()
loadCapabilities()
