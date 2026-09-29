import { useEffect, useLayoutEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { useToast } from "../../hooks/useToast";
import { AiPveLogService } from "../../services/aiPveLog";
import { AI_PVE_MARKDOWN_COMPONENTS } from "./aiPveRichText";
import styles from "./AiPveChat.module.scss";

/** 從 openIndex（必須是 "{"）起找出對應的 "}" 位置；會略過 JSON 字串內的括號。
 *  找不到（呼叫被截斷）時回傳 -1。 */
function findBalancedBraceEnd(text, openIndex) {
  let depth = 0;
  let inString = false;
  for (let i = openIndex; i < text.length; i += 1) {
    const ch = text[i];
    if (inString) {
      if (ch === "\\") i += 1;
      else if (ch === "\"") inString = false;
      continue;
    }
    if (ch === "\"") inString = true;
    else if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}

/** 移除沒有結尾標記的 `<|tool_call>call:name{...}`：只切掉緊接著的那一個
 *  平衡 JSON 物件，後面的正文（例如另一段 JSON 範例）不受影響。 */
function stripUnpairedToolCalls(text) {
  const marker = /<\|?tool_call\|?>\s*call:[a-zA-Z0-9_]+\s*\{/g;
  let out = "";
  let last = 0;
  let match;
  while ((match = marker.exec(text))) {
    const end = findBalancedBraceEnd(text, marker.lastIndex - 1);
    out += text.slice(last, match.index);
    // 被截斷、沒有收尾的呼叫：其後都是呼叫參數，整段去掉。
    last = end < 0 ? text.length : end + 1;
    marker.lastIndex = last;
  }
  return out + text.slice(last);
}

/** 清除模型殘留的 tool call 與思考標記，避免原始標記顯示在對話框中。 */
export function sanitizeAiPveContent(value) {
  const withoutPaired = String(value ?? "")
    .replace(/<\|?tool_call\|?>[\s\S]*?<\|?\/?tool_call\|?>/g, "");
  return stripUnpairedToolCalls(withoutPaired)
    .replace(/<think>[\s\S]*?<\/think>/g, "")
    .replace(/<\|[^>]*\|>/g, "")
    .trim();
}

/** 對話的起始訊息。帶著問題進來的（首頁是先打字才展開對話）不要再開場
 *  自我介紹一次：使用者已經問了，開場白只是白佔一格，模型自己通常也會
 *  再問候一次。沒帶問題時才需要開場白說明能問什麼。 */
export function initialAiPveMessages(initialPrompt, introMessage) {
  if (String(initialPrompt ?? "").trim()) return [];
  return [{ role: "assistant", content: introMessage }];
}

export function shouldSubmitAiPveInput(event, composing = false) {
  const native = event.nativeEvent ?? event;
  return event.key === "Enter" && !event.shiftKey && !event.ctrlKey
    && !event.altKey && !event.metaKey && !event.repeat
    && !composing && !native.isComposing && native.keyCode !== 229;
}

export function isAiPveLogNearBottom(element) {
  return element.scrollHeight - element.clientHeight - element.scrollTop <= 48;
}

/** 把要送回 server 的對話歷史控制在上限內（server 端上限 40 筆）。
 *  - system 訊息一律去掉：server 每次都會自己補上系統提示。
 *  - 從最前面整輪整輪地砍（每次砍到下一個 user 訊息），assistant 的
 *    tool_calls 永遠不會和它的 tool 結果分開，結果也一定從 user 訊息開始。
 *  - 找不到下一個 user 訊息（只剩一輪）就停手，不拆開那一輪。 */
export function trimAiPveHistory(history, max = 38) {
  let trimmed = (Array.isArray(history) ? history : [])
    .filter((message) => message?.role !== "system");
  const firstUser = trimmed.findIndex((message) => message?.role === "user");
  if (firstUser > 0) trimmed = trimmed.slice(firstUser);
  while (trimmed.length > max) {
    const nextUser = trimmed.findIndex((message, index) => index > 0 && message?.role === "user");
    if (nextUser === -1) break;
    trimmed = trimmed.slice(nextUser);
  }
  return trimmed;
}

/** 找出發出 pending ssh_exec 的那個 assistant tool-call 訊息位置。
 *  先依 tool_call_id 找；舊回應沒有 id 時改找內容帶有該 token 的 tool 結果，
 *  再往前找到發出它的 assistant。找不到回傳 -1。 */
export function findAiPvePendingRoundIndex(history, { toolCallId, token } = {}) {
  const list = Array.isArray(history) ? history : [];
  if (toolCallId) {
    const index = list.findIndex(
      (message) => message?.role === "assistant"
        && Array.isArray(message.tool_calls)
        && message.tool_calls.some((call) => call?.id === toolCallId),
    );
    if (index !== -1) return index;
  }
  if (!token) return -1;
  const toolIndex = list.findIndex(
    (message) => message?.role === "tool"
      && typeof message.content === "string"
      && message.content.includes(token),
  );
  for (let index = toolIndex - 1; index >= 0; index -= 1) {
    const message = list[index];
    if (message?.role === "assistant" && Array.isArray(message.tool_calls) && message.tool_calls.length) {
      return index;
    }
  }
  return -1;
}

const TOOL_LABELS = {
  get_nodes: "AiPveChat.toolNodes",
  get_resources: "AiPveChat.toolResources",
  get_storage: "AiPveChat.toolStorage",
  get_resource_detail: "AiPveChat.toolResourceDetail",
  get_cluster: "AiPveChat.toolCluster",
  get_guest_diagnostic_summary: "AiPveChat.toolGuestDiagnostic",
  ssh_exec: "AiPveChat.toolSsh",
};

export function AiPveToolHistory({ tools, t }) {
  if (!tools?.length) return null;
  return <details className={styles.toolHistory}>
    <summary>
      <MIcon name="chevron_right" size={16} className={styles.toolChevron} />
      {t("AiPveChat.toolHistory", { count: tools.length })}
    </summary>
    <ul>
      {tools.map((tool, index) => <li key={tool.tool_call_id || `${tool.name}-${index}`}>
        <span>{t(TOOL_LABELS[tool.name] ?? "AiPveChat.toolOther")}</span>
        {tool.args?.vmid != null && <small>VMID {String(tool.args.vmid)}</small>}
        {tool.args?.node && <small>{String(tool.args.node)}</small>}
      </li>)}
    </ul>
  </details>;
}

/** 將 AI 回覆以安全的 Markdown 呈現，避免格式標記以原始文字顯示。
 *  表格內的狀態標記、分層標籤與百分比再轉成徽章／晶片／量表，見 aiPveRichText。 */
export function AiPveMarkdownContent({ content }) {
  return (
    <div className={`${styles.msgContent} ${styles.msgMarkdown}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={AI_PVE_MARKDOWN_COMPONENTS}
      >
        {sanitizeAiPveContent(content)}
      </ReactMarkdown>
    </div>
  );
}

export default function AiPveChat({ initialPrompt = "", compact = false, fill = false }) {
  const { t } = useTranslation("components");
  const toast = useToast();
  const initialPromptRef = useRef(String(initialPrompt ?? "").trim());
  const initialPromptHandledRef = useRef(false);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [messages, setMessages] = useState(
    () => initialAiPveMessages(initialPromptRef.current, t("AiPveChat.introMessage")),
  );
  const [chatHistory, setChatHistory] = useState([]);
  const [pendingTool, setPendingTool] = useState(null);
  const [pendingCommand, setPendingCommand] = useState("");
  const logRef = useRef(null);
  const logContentRef = useRef(null);
  const composerRef = useRef(null);
  const composingRef = useRef(false);
  const followLatestRef = useRef(true);
  const [hasNewMessages, setHasNewMessages] = useState(false);

  function scrollToLatest() {
    const log = logRef.current;
    if (!log) return;
    followLatestRef.current = true;
    // 只捲動對話區，不讓外層首頁也被 scrollIntoView 拉動。
    log.scrollTop = log.scrollHeight;
    setHasNewMessages(false);
  }

  function handleLogScroll() {
    const log = logRef.current;
    if (!log) return;
    followLatestRef.current = isAiPveLogNearBottom(log);
    if (followLatestRef.current) setHasNewMessages(false);
  }

  useLayoutEffect(() => {
    if (followLatestRef.current) scrollToLatest();
    else setHasNewMessages(true);
  }, [messages, isSending, pendingTool]);

  useLayoutEffect(() => {
    const textarea = composerRef.current;
    if (!textarea) return;
    const resize = () => {
      const style = window.getComputedStyle(textarea);
      const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      const maxHeight = parseFloat(style.lineHeight) * 4 + padding;
      textarea.style.height = "0px";
      const height = textarea.scrollHeight;
      textarea.style.height = `${Math.min(height, maxHeight)}px`;
      textarea.style.overflowY = height > maxHeight ? "auto" : "hidden";
      if (followLatestRef.current) scrollToLatest();
    };
    resize();
    // 切換放大模式或視窗寬度後，文字換行數也會改變。
    let previousWidth = textarea.clientWidth;
    const observer = new ResizeObserver(() => {
      if (textarea.clientWidth !== previousWidth) {
        previousWidth = textarea.clientWidth;
        resize();
      }
    });
    observer.observe(textarea);
    return () => observer.disconnect();
  }, [input]);

  useEffect(() => {
    const observer = new ResizeObserver(() => {
      if (followLatestRef.current) scrollToLatest();
    });
    if (logRef.current) observer.observe(logRef.current);
    if (logContentRef.current) observer.observe(logContentRef.current);
    return () => observer.disconnect();
  }, []);

  const canSend = input.trim().length > 0 && !isSending && !pendingTool;

  function handleChatResponse(response) {
    if (response.error) toast.error(response.error);
    setChatHistory(trimAiPveHistory(response.messages || []));
    setMessages((previous) => [
      ...previous,
      {
        role: "assistant",
        content: response.reply || response.error || t("AiPveChat.commandDoneFallback"),
        tools: response.tools_called,
      },
    ]);

    if (response.needs_confirmation) {
      const sshTool = response.tools_called?.find(
        (tool) => tool.name === "ssh_exec" && tool.result?.pending,
      );
      if (sshTool?.result?.confirm_token) {
        const command = sshTool.args?.command || "";
        setPendingTool({
          token: sshTool.result.confirm_token,
          toolCallId: sshTool.tool_call_id || null,
          command,
          reason: sshTool.args?.reason || t("AiPveChat.defaultConfirmReason"),
        });
        setPendingCommand(command);
      }
    }
  }

  async function sendMessage(rawMessage) {
    const message = String(rawMessage ?? "").trim();
    if (!message || isSending || pendingTool) return;

    followLatestRef.current = true;
    setHasNewMessages(false);
    setInput("");
    setIsSending(true);
    setMessages((previous) => [...previous, { role: "user", content: message }]);

    const newHistory = chatHistory.length > 0
      ? trimAiPveHistory([...chatHistory, { role: "user", content: message }])
      : [];

    try {
      const response = await AiPveLogService.chat(
        newHistory.length > 0 ? { messages: newHistory } : { message },
      );
      handleChatResponse(response);
    } catch (error) {
      const detail = error?.message ?? t("AiPveChat.chatFailedFallback");
      toast.error(detail);
      setMessages((previous) => [
        ...previous,
        { role: "assistant", content: t("AiPveChat.errorOccurred", { detail }) },
      ]);
    } finally {
      setIsSending(false);
    }
  }

  useEffect(() => {
    const prompt = initialPromptRef.current;
    if (!prompt || initialPromptHandledRef.current) return;
    initialPromptHandledRef.current = true;
    sendMessage(prompt);
  }, []);

  function handleSubmit(event) {
    event.preventDefault();
    sendMessage(input);
  }

  function handleComposerKeyDown(event) {
    if (!shouldSubmitAiPveInput(event, composingRef.current)) return;
    event.preventDefault();
    if (canSend) sendMessage(input);
  }

  async function handleConfirm(approved) {
    if (!pendingTool) return;
    const command = pendingCommand.trim();
    if (approved && !command) {
      toast.error(t("AiPveChat.enterCommandFirst"));
      return;
    }
    setIsSending(true);
    // 先把目前的待確認指令存成區域變數：下面 await 之後 state 已被清掉。
    const pending = pendingTool;
    const currentToken = pending.token;

    try {
      const result = await AiPveLogService.confirmSsh({
        token: currentToken,
        approved,
        command: approved ? command : undefined,
      });
      setPendingTool(null);
      setPendingCommand("");

      if (!approved) {
        setMessages((previous) => [
          ...previous,
          { role: "assistant", content: t("AiPveChat.commandCancelled") },
        ]);
      }

      // 允許或拒絕都要把 server 的確認結果寫回 pending 的 tool 訊息並立刻接續：
      // server 會消耗 token、模型回應這次結果，同一輪其餘 deferred 的指令
      // 也會接著變成下一個待確認項目。歷史裡留著 {"pending": true} 的話，
      // 之後每一則訊息都會被 server 以 422 拒絕。
      const updatedHistory = [...chatHistory];
      let targetIndex = pending.toolCallId
        ? updatedHistory.findIndex(
          (message) => message.role === "tool"
            && message.tool_call_id === pending.toolCallId,
        )
        : -1;
      if (targetIndex === -1) {
        targetIndex = updatedHistory.findIndex(
          (message) => message.role === "tool"
            && typeof message.content === "string"
            && message.content.includes(currentToken),
        );
      }
      if (targetIndex !== -1) {
        const canonicalResult = {
          ...result,
          confirmation_token: currentToken,
        };
        updatedHistory[targetIndex] = {
          ...updatedHistory[targetIndex],
          content: JSON.stringify(canonicalResult),
        };
      }

      const response = await AiPveLogService.chat({ messages: updatedHistory });
      handleChatResponse(response);
    } catch (error) {
      toast.error(error?.message ?? t("AiPveChat.confirmFailed"));
      // 確認或接續失敗：把那一整輪 tool-call 從歷史拿掉，只留到觸發它的
      // user 訊息為止，下一則訊息才不會帶著 server 不接受的 pending 結果。
      setPendingTool(null);
      setPendingCommand("");
      setChatHistory((previous) => {
        const roundIndex = findAiPvePendingRoundIndex(previous, {
          toolCallId: pending.toolCallId,
          token: currentToken,
        });
        return roundIndex === -1 ? previous : previous.slice(0, roundIndex);
      });
    } finally {
      setIsSending(false);
    }
  }

  return (
    <div className={`${styles.chatCard} ${compact ? styles.compact : ""} ${fill ? styles.fill : ""}`}>
      <div className={styles.chatViewport}>
      <div ref={logRef} className={styles.chatLog} onScroll={handleLogScroll} aria-live="polite" role="log" aria-label={t("AiPveChat.conversationLabel")}>
        <div ref={logContentRef} className={styles.chatLogContent}>
        {messages.map((message, index) => {
          const isUser = message.role === "user";
          return (
            <div
              key={`${message.role}-${index}`}
              className={`${styles.msg} ${isUser ? styles.msg_user : styles.msg_assistant}`}
            >
              {/* 助理有頭像、回覆不加框；使用者是靠右的實心氣泡——
                  與站上另一個對話元件 AiFloatingChat 用同一套語彙。
                  頭像圖示則跟首頁助手標頭一致（support_agent），同一位助手不換臉。 */}
              {!isUser && (
                <span className={styles.avatar}><MIcon name="support_agent" size={18} /></span>
              )}
              <div className={styles.msgBody}>
                {isUser ? (
                  <p className={`${styles.msgContent} ${styles.msgPlain}`}>
                    {sanitizeAiPveContent(message.content)}
                  </p>
                ) : (
                  <AiPveMarkdownContent content={message.content} />
                )}
                <AiPveToolHistory tools={message.tools} t={t} />
              </div>
            </div>
          );
        })}

        {pendingTool && (
          <div className={styles.pendingBox}>
            <div className={styles.pendingHead}>
              <MIcon name="warning" size={18} />
              {t("AiPveChat.pendingHeading")}
            </div>
            <p className={styles.pendingReason}>
              <strong>{t("AiPveChat.pendingReasonLabel")}</strong>
              {pendingTool.reason}
            </p>
            <textarea
              value={pendingCommand}
              onChange={(event) => setPendingCommand(event.target.value)}
              placeholder={t("AiPveChat.pendingCommandPlaceholder")}
              disabled={isSending}
            />
            <p className={styles.pendingHint}>{t("AiPveChat.pendingHint")}</p>
            <div className={styles.pendingActions}>
              <button
                type="button"
                className={styles.btnAllow}
                onClick={() => handleConfirm(true)}
                disabled={isSending || pendingCommand.trim().length === 0}
              >
                <MIcon name="check" size={16} />
                {t("AiPveChat.allowButton")}
              </button>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={() => handleConfirm(false)}
                disabled={isSending}
              >
                <MIcon name="close" size={16} />
                {t("AiPveChat.rejectButton")}
              </button>
            </div>
          </div>
        )}

        {isSending && (
          <div className={`${styles.msg} ${styles.msg_assistant}`}>
            <span className={styles.avatar}><MIcon name="support_agent" size={18} /></span>
            <div className={styles.thinking}>
              <span className={styles.pulse} />
              {t("AiPveChat.thinking")}
            </div>
          </div>
        )}
        </div>
      </div>
      {hasNewMessages && <button type="button" className={styles.latestMessage} onClick={scrollToLatest}>
        <MIcon name="arrow_downward" size={15} />{t("AiPveChat.latestMessage")}
      </button>}
      </div>

      <form className={styles.composer} onSubmit={handleSubmit}>
        <textarea
          ref={composerRef}
          rows={1}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleComposerKeyDown}
          onCompositionStart={() => { composingRef.current = true; }}
          onCompositionEnd={() => { composingRef.current = false; }}
          aria-label={t("AiPveChat.composerLabel")}
          title={t("AiPveChat.composerKeyboardHint")}
          placeholder={t("AiPveChat.composerPlaceholder")}
        />
        <div className={styles.composerActions}>
          <button type="submit" className={styles.btnPrimary} disabled={!canSend}>
            <MIcon name="send" size={16} />
            {t("AiPveChat.sendButton")}
          </button>
        </div>
      </form>
    </div>
  );
}
