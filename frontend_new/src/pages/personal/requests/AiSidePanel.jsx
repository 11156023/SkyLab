import { useEffect, useRef, useState } from "react";
import styles from "./RequestFormPage.module.scss";
import MIcon from "../../../components/MIcon";
import { AiTemplateRecommendationApi } from "../../../services/aiTemplateRecommendation";

const GREETING = "嗨！我是 AI 助手，可以幫你決定要申請什麼規格的資源。\n你有什麼需求嗎？";

function stripThinkTags(text) {
  return String(text || "").replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
}

function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderInlineMarkdown(text) {
  return text
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>");
}

function renderMarkdown(text) {
  const codeBlocks = [];
  const escaped = escapeHtml(text).replace(/```([\s\S]*?)```/g, (_, code) => {
    const token = `@@CODE_BLOCK_${codeBlocks.length}@@`;
    codeBlocks.push(`<pre><code>${code.trim()}</code></pre>`);
    return `\n${token}\n`;
  });

  const lines = escaped.split(/\r?\n/);
  const html = [];
  let inList = false;

  const closeList = () => {
    if (!inList) return;
    html.push("</ul>");
    inList = false;
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      closeList();
      continue;
    }

    const codeIndex = codeBlocks.findIndex((_, index) => line === `@@CODE_BLOCK_${index}@@`);
    if (codeIndex >= 0) {
      closeList();
      html.push(codeBlocks[codeIndex]);
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 1, 4);
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const listItem = line.match(/^[-*]\s+(.+)$/);
    if (listItem) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${renderInlineMarkdown(listItem[1])}</li>`);
      continue;
    }

    closeList();
    html.push(`<p>${renderInlineMarkdown(line)}</p>`);
  }

  closeList();
  return html.join("");
}

function planSummary(data) {
  const plan = data?.final_plan;
  const prefill = plan?.form_prefill ?? {};
  const target = plan?.application_target ?? {};
  const lines = [
    data?.summary,
    target.environment_reason,
    prefill.resource_type
      ? `建議類型：${prefill.resource_type === "vm" ? "VM" : "LXC"}`
      : "",
    prefill.cores || prefill.memory_mb || prefill.disk_gb
      ? `建議規格：${prefill.cores ?? "-"} vCPU / ${prefill.memory_mb ?? "-"} MB RAM / ${prefill.disk_gb ?? "-"} GB Disk`
      : "",
    prefill.reason ? `申請理由：${prefill.reason}` : "",
  ].filter(Boolean);
  return lines.join("\n");
}

export default function AiSidePanel({
  className = "",
  recommendationContext,
  onImportPlan,
}) {
  const [messages, setMessages] = useState([{ role: "assistant", content: GREETING }]);
  const [history, setHistory]   = useState([]);
  const [input, setInput]       = useState("");
  const [loading, setLoading]   = useState(false);
  const [latestPlan, setLatestPlan] = useState(null);
  const scrollRef               = useRef(null);
  const inputRef                = useRef(null);

  useEffect(() => {
    if (!scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, loading]);

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
      const data = await AiTemplateRecommendationApi.chat({
        messages: nextHistory,
        top_k: 5,
        device_nodes: [],
        form_context: recommendationContext,
      });
      const aiMsg = {
        role: "assistant",
        content: stripThinkTags(data.reply) || "我收到你的需求了。",
      };
      setMessages((prev) => [...prev, aiMsg]);
      setHistory((prev) => [...prev, aiMsg]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: err?.message || "AI 目前無法回覆，請稍後再試。" },
      ]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  async function recommend() {
    if (loading) return;
    const requestMessages = history.length > 0
      ? history
      : [{ role: "user", content: "請根據我目前表單內容產生推薦配置。" }];
    setLoading(true);
    try {
      const data = await AiTemplateRecommendationApi.recommend({
        messages: requestMessages,
        top_k: 5,
        device_nodes: [],
        form_context: recommendationContext,
      });
      setLatestPlan(data);
      const summary = planSummary(data) || "AI 已產生推薦配置。";
      const aiMsg = { role: "assistant", content: summary };
      setMessages((prev) => [...prev, aiMsg]);
      setHistory((prev) => [...prev, aiMsg]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: err?.message || "產生推薦配置失敗，請稍後再試。" },
      ]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  }

  return (
    <div className={`${styles.aiPanel} ${className}`}>
      <div className={styles.aiMessages} ref={scrollRef}>
        {messages.map((msg, i) => (
          <div key={i} className={`${styles.aiMsgRow} ${msg.role === "user" ? styles.aiMsgRowUser : ""}`}>
            {msg.role === "assistant" && (
              <div className={styles.aiAvatar}><MIcon name="smart_toy" size={13} /></div>
            )}
            <div className={`${styles.aiMsgBubble} ${msg.role === "user" ? styles.aiMsgBubbleUser : styles.aiMsgBubbleAi}`}>
              {msg.role === "assistant" ? (
                <div
                  className={styles.aiMarkdown}
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                />
              ) : (
                msg.content
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className={styles.aiMsgRow}>
            <div className={styles.aiAvatar}><MIcon name="smart_toy" size={13} /></div>
            <div className={styles.aiTyping}>
              <span /><span /><span />
            </div>
          </div>
        )}
        {latestPlan?.final_plan?.form_prefill && onImportPlan && (
          <div className={styles.aiMsgRow}>
            <div className={styles.aiAvatar}><MIcon name="auto_fix_high" size={13} /></div>
            <button
              type="button"
              className={styles.aiTemplateBtn}
              onClick={() => onImportPlan(latestPlan.final_plan.form_prefill)}
            >
              <MIcon name="download" size={14} />
              匯入推薦配置
            </button>
          </div>
        )}
      </div>

      <div className={styles.aiInputWrap}>
        <textarea
          ref={inputRef}
          className={styles.aiInput}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="輸入訊息… (Enter 送出)"
          disabled={loading}
        />
        <div className={styles.aiInputToolbar}>
          <button type="button" className={styles.aiTemplateBtn} disabled={loading} onClick={recommend}>
            <MIcon name="auto_fix_high" size={14} />
            產生推薦配置
          </button>
          <button
            type="button"
            className={styles.aiSendBtn}
            onClick={send}
            disabled={loading || !input.trim()}
          >
            <MIcon name="send" size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
