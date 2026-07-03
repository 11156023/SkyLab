/**
 * AiFloatingChat
 * 浮動 AI 助手 — FAB 按鈕 + 右下角彈出聊天視窗
 * 設計為放在任何有 position:relative 容器內使用
 *
 * 兩種模式：
 *   - 諮詢（chat）    : AI 模板推薦對話（/ai/template-recommendation/chat）
 *   - 導航（navigate）: 自然語言找頁面（/ai/navigation/resolve）
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import styles from "./AiFloatingChat.module.scss";
import MIcon from "../MIcon";
import { AiTemplateRecommendationApi } from "../../services/aiTemplateRecommendation";
import { AiNavigationService } from "../../services/aiNavigation";

function stripThinkTags(text) {
  return String(text || "").replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
}

/** 後端導航目標使用舊版前端路徑，映射到 frontend_new 的路由 */
const PATH_MAP = {
  "/": "/dashboard",
  "/resources": "/resource-mgmt",
  "/resources-create": "/my-requests",
  "/approvals": "/request-review",
  "/gpu-management": "/gpu-mgmt",
  "/ai-api-approvals": "/ai-api-review",
  "/ai-api-credentials": "/ai-api-keys",
  "/admin/audit-logs": "/audit",
  "/admin/migration-jobs": "/migration",
  "/admin/domains": "/domain",
  "/admin/gateway": "/gateway",
  "/admin/ip-management": "/ip-management",
  "/admin/configuration": "/settings",
  "/admin/batch-provision-review": "/batch-review",
  "/admin/ai-management": "/ai-management",
  "/admin/ai-monitoring": "/ai-monitoring",
};

function mapPath(path) {
  if (!path) return null;
  const clean = path.split("?")[0];
  return PATH_MAP[clean] ?? clean;
}

function TypingIndicator() {
  return (
    <div className={styles.bubble}>
      <span className={styles.dot} />
      <span className={styles.dot} />
      <span className={styles.dot} />
    </div>
  );
}

function Message({ msg, onNavigate }) {
  const isUser = msg.role === "user";
  return (
    <div className={`${styles.msgRow} ${isUser ? styles.msgRowUser : ""}`}>
      {!isUser && (
        <div className={styles.avatar}>
          <MIcon name="smart_toy" size={14} />
        </div>
      )}
      <div className={`${styles.msgBubble} ${isUser ? styles.msgBubbleUser : styles.msgBubbleAi}`}>
        {msg.content}
        {msg.targets?.length > 0 && (
          <div className={styles.navTargets}>
            {msg.targets.map((t) => (
              <button
                key={t.path}
                type="button"
                className={styles.navTargetBtn}
                title={t.reason}
                onClick={() => onNavigate(t.path)}
              >
                <MIcon name="arrow_forward" size={13} />
                {t.title}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const GREETING_CHAT = "嗨！我是 AI 助手，可以幫你決定要申請什麼規格的資源。\n你有什麼需求嗎？";
const GREETING_NAV = "想去哪個頁面？直接告訴我，例如「我要看防火牆規則」或「幫我找申請審核」。";

export default function AiFloatingChat({ context }) {
  const navigate = useNavigate();
  const [open, setOpen]         = useState(false);
  const [mode, setMode]         = useState("chat"); // "chat" | "navigate"
  const [messages, setMessages] = useState([]);
  const [history, setHistory]   = useState([]);
  const [input, setInput]       = useState("");
  const [loading, setLoading]   = useState(false);
  const [closing, setClosing]   = useState(false);
  const scrollRef               = useRef(null);
  const inputRef                = useRef(null);

  const greeting = mode === "chat" ? GREETING_CHAT : GREETING_NAV;

  /* 開啟時若無訊息，顯示問候語 */
  useEffect(() => {
    if (open && messages.length === 0) {
      setMessages([{ role: "assistant", content: greeting }]);
    }
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 120);
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  /* 自動捲到最新訊息 */
  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, loading]);

  function handleClose() {
    setClosing(true);
    setTimeout(() => {
      setOpen(false);
      setClosing(false);
    }, 180);
  }

  function switchMode(next) {
    if (next === mode) return;
    setMode(next);
    setHistory([]);
    setMessages([{
      role: "assistant",
      content: next === "chat" ? GREETING_CHAT : GREETING_NAV,
    }]);
    inputRef.current?.focus();
  }

  function handleNavigate(path) {
    const target = mapPath(path);
    if (!target) return;
    navigate(target);
    handleClose();
  }

  async function sendChat(text, nextHistory) {
    const data = await AiTemplateRecommendationApi.chat({
      messages: nextHistory,
      top_k: 5,
      device_nodes: [],
      form_context: context ?? null,
    });
    const aiMsg = {
      role: "assistant",
      content: stripThinkTags(data.reply) || "我收到你的需求了。",
    };
    setMessages((prev) => [...prev, aiMsg]);
    setHistory((prev) => [...prev, aiMsg]);
  }

  async function sendNavigate(text) {
    const data = await AiNavigationService.resolve(text);
    const targets = [
      ...(data.primary ? [data.primary] : []),
      ...(data.suggestions ?? []),
    ].filter((t, i, arr) => arr.findIndex((x) => x.path === t.path) === i);

    let content;
    if (data.action === "clarify") {
      content = data.clarification_question || "可以再描述得具體一點嗎？";
    } else if (targets.length === 0) {
      content = "找不到符合的頁面，換個說法試試？";
    } else if (data.action === "navigate" && data.primary) {
      content = `找到了：${data.primary.title}。${data.primary.reason ?? ""}`;
    } else {
      content = "這幾個頁面可能是你要找的：";
    }

    setMessages((prev) => [...prev, { role: "assistant", content, targets }]);
  }

  async function send() {
    const text = input.trim();
    if (!text || loading) return;

    setInput("");
    const userMsg = { role: "user", content: text };
    const nextHistory = [...history, userMsg];
    setMessages((prev) => [...prev, userMsg]);
    setHistory(nextHistory);
    setLoading(true);

    try {
      if (mode === "chat") {
        await sendChat(text, nextHistory);
      } else {
        await sendNavigate(text);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: err?.message ?? "發生錯誤，請稍後再試。" },
      ]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function clearChat() {
    setHistory([]);
    setMessages([{ role: "assistant", content: greeting }]);
  }

  return (
    <div className={styles.root}>
      {/* ── Chat panel ── */}
      {open && (
        <div className={`${styles.panel} ${closing ? styles.panelOut : styles.panelIn}`}>
          {/* Header */}
          <div className={styles.header}>
            <span className={styles.headerIcon}>
              <MIcon name="smart_toy" size={16} />
            </span>
            <span className={styles.headerTitle}>AI 助手</span>
            <div className={styles.modeTabs}>
              <button
                type="button"
                className={mode === "chat" ? styles.modeTabActive : styles.modeTab}
                onClick={() => switchMode("chat")}
              >
                諮詢
              </button>
              <button
                type="button"
                className={mode === "navigate" ? styles.modeTabActive : styles.modeTab}
                onClick={() => switchMode("navigate")}
              >
                導航
              </button>
            </div>
            <button type="button" className={styles.headerClear} onClick={clearChat} title="清除對話">
              <MIcon name="refresh" size={16} />
            </button>
            <button type="button" className={styles.headerClose} onClick={handleClose} title="關閉">
              <MIcon name="close" size={18} />
            </button>
          </div>

          {/* Messages */}
          <div className={styles.messages} ref={scrollRef}>
            {messages.map((msg, i) => (
              <Message key={i} msg={msg} onNavigate={handleNavigate} />
            ))}
            {loading && (
              <div className={styles.msgRow}>
                <div className={styles.avatar}>
                  <MIcon name="smart_toy" size={14} />
                </div>
                <TypingIndicator />
              </div>
            )}
          </div>

          {/* Input */}
          <div className={styles.inputWrap}>
            <textarea
              ref={inputRef}
              className={styles.input}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={mode === "chat" ? "輸入訊息… (Enter 送出)" : "描述你要找的頁面… (Enter 送出)"}
              rows={1}
              disabled={loading}
            />
            <button
              type="button"
              className={styles.sendBtn}
              onClick={send}
              disabled={loading || !input.trim()}
              title="送出"
            >
              <MIcon name="send" size={16} />
            </button>
          </div>
        </div>
      )}

      {/* ── FAB ── */}
      <button
        type="button"
        className={`${styles.fab} ${open ? styles.fabOpen : ""}`}
        onClick={() => (open ? handleClose() : setOpen(true))}
        title="AI 助手"
        aria-label="開啟 AI 助手"
      >
        <span className={`${styles.fabIcon} ${styles.fabIconAi}`}>
          <MIcon name="smart_toy" size={22} />
        </span>
        <span className={`${styles.fabIcon} ${styles.fabIconClose}`}>
          <MIcon name="close" size={22} />
        </span>
        {!open && <span className={styles.fabLabel}>AI 助手</span>}
      </button>
    </div>
  );
}
